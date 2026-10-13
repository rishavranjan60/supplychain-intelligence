import csv
from pathlib import Path

from supplychain.db import get_connection
from supplychain.ingestion.export_incremental import DATASETS
from supplychain.ingestion.export_snapshot import (
    main as export_snapshot,
)
from supplychain.validation.check_raw_snapshot import validate_snapshot


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def read_records(path, primary_key):
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        header = reader.fieldnames

        if not header or primary_key not in header:
            raise ValueError(f"Invalid header: {path.name}")

        records = {}

        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed row: {path.name}")

            key = row[primary_key]

            if not key or key in records:
                raise ValueError(f"Invalid or duplicate key: {path.name}")

            records[key] = row

    return header, records


def main():
    failures = []

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '15s';")
            cursor.execute("SELECT pg_advisory_xact_lock(73010);")

            # Freeze source writes during the full-export comparison.
            from psycopg import sql

            table_names = sql.SQL(", ").join(
                sql.Identifier("source", name)
                for name in DATASETS
            )

            cursor.execute(
                sql.SQL("LOCK TABLE {} IN SHARE MODE;").format(
                    table_names
                )
            )

            cursor.execute(
                """
                SELECT dataset_name, last_successful_run_id
                FROM platform.ingestion_state;
                """
            )
            states = dict(cursor.fetchall())

            if set(states) != set(DATASETS):
                raise ValueError("Expected state for seven datasets.")

            batch_ids = set(states.values())

            if len(batch_ids) != 1:
                raise ValueError("Datasets reference different snapshots.")

            merged_id = next(iter(batch_ids))

            if not merged_id.startswith("merged_incremental_"):
                raise ValueError(
                    "Apply an incremental batch before this check."
                )

            print("Creating a fresh full snapshot...", flush=True)
            full_id = export_snapshot()

            if not full_id:
                raise ValueError("Exporter did not return a run ID.")

            merged_directory = (
                PROJECT_ROOT / "data" / "raw" / merged_id
            )
            full_directory = (
                PROJECT_ROOT / "data" / "raw" / full_id
            )

            validate_snapshot(merged_directory)
            validate_snapshot(full_directory)

            print("\nCOMPARING DATASETS")

            for name, columns in DATASETS.items():
                primary_key = columns[0]

                merged_header, merged_records = read_records(
                    merged_directory / f"{name}.csv",
                    primary_key,
                )
                full_header, full_records = read_records(
                    full_directory / f"{name}.csv",
                    primary_key,
                )

                if merged_header != full_header:
                    failures.append(f"{name}: column mismatch")
                    print(f"FAIL: {name} — column mismatch")
                    continue

                if merged_records == full_records:
                    print(
                        f"PASS: {name} "
                        f"({len(full_records)} matching records)"
                    )
                else:
                    merged_keys = set(merged_records)
                    full_keys = set(full_records)

                    missing = full_keys - merged_keys
                    extra = merged_keys - full_keys
                    changed = {
                        key
                        for key in merged_keys & full_keys
                        if merged_records[key] != full_records[key]
                    }

                    failures.append(name)
                    print(
                        f"FAIL: {name} — "
                        f"missing={len(missing)}, "
                        f"extra={len(extra)}, "
                        f"different={len(changed)}"
                    )

    if failures:
        print(
            "\nComparison failed. Check whether source changes "
            "remain unprocessed before diagnosing the merge."
        )
        raise SystemExit(1)

    print("\nIncremental snapshot matches the full snapshot.")
    print("Ingestion watermarks were not changed.")


if __name__ == "__main__":
    main()