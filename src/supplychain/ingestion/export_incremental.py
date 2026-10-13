import json
import shutil
from pathlib import Path
from uuid import uuid4

from psycopg import sql

from supplychain.db import get_connection
from supplychain.validation.check_raw_snapshot import (
    calculate_sha256,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Dataset: primary key and change-tracking column.
DATASETS = {
    "products": ("product_id", "updated_at"),
    "warehouses": ("warehouse_id", "updated_at"),
    "orders": ("order_id", "updated_at"),
    "order_items": ("order_item_id", "updated_at"),
    "inventory_movements": ("movement_id", "created_at"),
    "shipments": ("shipment_id", "updated_at"),
    "returns": ("return_id", "updated_at"),
}


def main():
    run_id = f"incremental_{uuid4().hex}"
    output_root = PROJECT_ROOT / "data" / "incremental"
    staging = output_root / f".incomplete_{run_id}"
    destination = output_root / run_id

    output_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir()

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '15s';")
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(73010);"
                )

                # Allow reads, but block source writes during extraction.
                source_tables = sql.SQL(", ").join(
                    sql.Identifier("source", name)
                    for name in DATASETS
                )

                cursor.execute(
                    sql.SQL(
                        "LOCK TABLE {} IN SHARE MODE;"
                    ).format(source_tables)
                )

                cursor.execute(
                    """
                    SELECT
                        dataset_name,
                        watermark,
                        last_successful_run_id
                    FROM platform.ingestion_state;
                    """
                )

                states = {
                    name: {
                        "watermark": watermark,
                        "parent_run_id": parent_run_id,
                    }
                    for name, watermark, parent_run_id
                    in cursor.fetchall()
                }

                if set(states) != set(DATASETS):
                    raise RuntimeError(
                        "Expected ingestion state for all seven datasets."
                    )

                parent_ids = {
                    state["parent_run_id"]
                    for state in states.values()
                }

                if len(parent_ids) != 1:
                    raise RuntimeError(
                        "Dataset states reference different parent batches."
                    )

                cursor.execute("SELECT clock_timestamp();")
                cutoff = cursor.fetchone()[0]

                manifest = {
                    "run_id": run_id,
                    "extraction_mode": "incremental",
                    "schema_version": 1,
                    "status": "EXTRACTED",
                    "parent_run_id": next(iter(parent_ids)),
                    "cutoff": cutoff.isoformat(),
                    "datasets": [],
                }

                for name, columns in DATASETS.items():
                    primary_key, change_column = columns
                    watermark = states[name]["watermark"]

                    if cutoff <= watermark:
                        raise RuntimeError(
                            f"Cutoff must be later than watermark: {name}"
                        )

                    condition = sql.SQL(
                        "{} > {} AND {} <= {}"
                    ).format(
                        sql.Identifier(change_column),
                        sql.Literal(watermark),
                        sql.Identifier(change_column),
                        sql.Literal(cutoff),
                    )

                    cursor.execute(
                        sql.SQL(
                            "SELECT COUNT(*) FROM {}.{} WHERE {};"
                        ).format(
                            sql.Identifier("source"),
                            sql.Identifier(name),
                            condition,
                        )
                    )
                    row_count = cursor.fetchone()[0]

                    copy_query = sql.SQL(
                        """
                        COPY (
                            SELECT *
                            FROM {}.{}
                            WHERE {}
                            ORDER BY {}
                        )
                        TO STDOUT WITH (
                            FORMAT CSV,
                            HEADER TRUE,
                            ENCODING 'UTF8'
                        )
                        """
                    ).format(
                        sql.Identifier("source"),
                        sql.Identifier(name),
                        condition,
                        sql.Identifier(primary_key),
                    )

                    file_path = staging / f"{name}.csv"

                    with file_path.open("wb") as output:
                        with cursor.copy(copy_query) as copy:
                            for chunk in copy:
                                output.write(chunk)

                    manifest["datasets"].append(
                        {
                            "name": name,
                            "file": file_path.name,
                            "primary_key": primary_key,
                            "change_column": change_column,
                            "previous_watermark": watermark.isoformat(),
                            "row_count": row_count,
                            "size_bytes": file_path.stat().st_size,
                            "sha256": calculate_sha256(file_path),
                        }
                    )

                    print(f"Extracted {name}: {row_count} changed rows")

                (staging / "manifest.json").write_text(
                    json.dumps(manifest, indent=2),
                    encoding="utf-8",
                )

                staging.rename(destination)

        print("\nIncremental extraction completed.")
        print(f"Run ID: {run_id}")
        print(f"Output: {destination}")
        print("Watermarks unchanged; merge is still required.")

    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


if __name__ == "__main__":
    main()