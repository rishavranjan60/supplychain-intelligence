import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from psycopg import sql

from supplychain.db import get_connection
from supplychain.ingestion.export_snapshot import (
    TABLES,
    main as export_snapshot,
)
from supplychain.validation.check_raw_snapshot import (
    validate_snapshot,
)
from supplychain.validation.check_source_data import (
    main as check_source_data,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def mark_failed(run_id, error):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE platform.ingestion_runs
                SET status = 'FAILED',
                    completed_at = CURRENT_TIMESTAMP,
                    error_message = %s
                WHERE run_id = %s;
                """,
                (str(error)[:2000], run_id),
            )


def main():
    baseline_id = f"baseline_{uuid4().hex}"

    # Persist the run record separately so failures remain visible.
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO platform.ingestion_runs (
                    run_id, extraction_mode, status
                )
                VALUES (%s, 'BASELINE', 'RUNNING');
                """,
                (baseline_id,),
            )

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '15s';")

                # Future incremental loaders will use this same lock.
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(73010);"
                )

                cursor.execute(
                    "SELECT COUNT(*) FROM platform.ingestion_state;"
                )
                state_count = cursor.fetchone()[0]

                if state_count:
                    raise RuntimeError(
                        "Ingestion state already exists. "
                        "Do not recreate the baseline."
                    )

                # SHARE locks permit reads but block source changes
                # until this transaction finishes.
                tables = sql.SQL(", ").join(
                    sql.Identifier("source", table)
                    for table in TABLES
                )

                cursor.execute(
                    sql.SQL("LOCK TABLE {} IN SHARE MODE;").format(
                        tables
                    )
                )

                cursor.execute("SELECT clock_timestamp();")
                cutoff = cursor.fetchone()[0]

                print("Validating source data...", flush=True)
                check_source_data()

                print("\nExporting baseline...", flush=True)
                snapshot_id = export_snapshot()

                if not snapshot_id:
                    raise RuntimeError(
                        "export_snapshot() must return run_id."
                    )

                raw_directory = (
                    PROJECT_ROOT / "data" / "raw" / snapshot_id
                )

                validate_snapshot(raw_directory)

                print("\nBuilding baseline reports...", flush=True)

                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "supplychain.transformations.build_reports",
                        "--run-id",
                        snapshot_id,
                    ],
                    cwd=PROJECT_ROOT,
                    check=True,
                )

                # Save state only after publication succeeds.
                for table in TABLES:
                    cursor.execute(
                        """
                        INSERT INTO platform.ingestion_state (
                            dataset_name,
                            watermark,
                            last_successful_run_id
                        )
                        VALUES (%s, %s, %s);
                        """,
                        (table, cutoff, snapshot_id),
                    )

                cursor.execute(
                    """
                    UPDATE platform.ingestion_runs
                    SET status = 'SUCCESS',
                        completed_at = CURRENT_TIMESTAMP
                    WHERE run_id = %s;
                    """,
                    (baseline_id,),
                )

        print("\nBaseline completed successfully.")
        print(f"Execution ID: {baseline_id}")
        print(f"Snapshot ID: {snapshot_id}")
        print(f"Starting watermark: {cutoff.isoformat()}")

    except Exception as error:
        mark_failed(baseline_id, error)
        raise


if __name__ == "__main__":
    main()