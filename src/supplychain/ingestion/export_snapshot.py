import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from psycopg import sql

from supplychain.db import get_connection


TABLES = [
    "products",
    "warehouses",
    "orders",
    "order_items",
    "inventory_movements",
    "shipments",
    "returns",
]

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def calculate_sha256(path):
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def main():
    started_at = datetime.now(timezone.utc)
    run_id = (
        started_at.strftime("%Y%m%dT%H%M%SZ")
        + "_"
        + uuid4().hex[:8]
    )

    raw_root = PROJECT_ROOT / "data" / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)

    # Incomplete exports remain separate from published batches.
    staging_directory = raw_root / f".incomplete_{run_id}"
    final_directory = raw_root / run_id
    staging_directory.mkdir()

    manifest = {
        "run_id": run_id,
        "schema_version": 1,
        "extraction_mode": "full_snapshot",
        "started_at": started_at.isoformat(),
        "status": "IN_PROGRESS",
        "datasets": [],
    }

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                # Every table is exported from the same snapshot.
                cursor.execute(
                    """
                    SET TRANSACTION ISOLATION LEVEL
                    REPEATABLE READ, READ ONLY;
                    """
                )

                for table in TABLES:
                    file_path = staging_directory / f"{table}.csv"

                    count_query = sql.SQL(
                        "SELECT COUNT(*) FROM {}.{}"
                    ).format(
                        sql.Identifier("source"),
                        sql.Identifier(table),
                    )
                    cursor.execute(count_query)
                    row_count = cursor.fetchone()[0]

                    copy_query = sql.SQL(
                        """
                        COPY (
                            SELECT *
                            FROM {}.{}
                            ORDER BY 1
                        )
                        TO STDOUT WITH (
                            FORMAT CSV,
                            HEADER TRUE,
                            ENCODING 'UTF8'
                        )
                        """
                    ).format(
                        sql.Identifier("source"),
                        sql.Identifier(table),
                    )

                    with file_path.open("wb") as output:
                        with cursor.copy(copy_query) as copy:
                            for chunk in copy:
                                output.write(chunk)

                    manifest["datasets"].append(
                        {
                            "name": table,
                            "file": file_path.name,
                            "row_count": row_count,
                            "size_bytes": file_path.stat().st_size,
                            "sha256": calculate_sha256(file_path),
                        }
                    )

                    print(f"Exported {table}: {row_count} rows")

        manifest["status"] = "SUCCESS"
        manifest["completed_at"] = datetime.now(
            timezone.utc
        ).isoformat()

        manifest_path = staging_directory / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

        # Publish only after every export and the manifest succeed.
        staging_directory.rename(final_directory)

    except Exception:
        shutil.rmtree(staging_directory, ignore_errors=True)
        raise

    print("\nSnapshot published successfully.")
    print(f"Run ID: {run_id}")
    print(f"Output: {final_directory}")

    return run_id



if __name__ == "__main__":
    main()
    