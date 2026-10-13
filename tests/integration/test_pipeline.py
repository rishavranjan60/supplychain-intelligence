import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Integration tests require the isolated test database.",
)
def test_baseline_incremental_and_replay(tmp_path):
    # Copy source into a temporary project so generated files
    # cannot overwrite development snapshots or reports.
    shutil.copytree(
        PROJECT_ROOT / "src",
        tmp_path / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
    )

    environment = os.environ.copy()
    environment.update(
        {
            "POSTGRES_DB": "supplychain_test",
            "POSTGRES_USER": "supplychain_test",
            "POSTGRES_PASSWORD": "local_test_only",
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": "5434",
            "PYTHONPATH": str(tmp_path / "src"),
        }
    )

    def connect():
        return psycopg.connect(
            dbname=environment["POSTGRES_DB"],
            user=environment["POSTGRES_USER"],
            password=environment["POSTGRES_PASSWORD"],
            host=environment["POSTGRES_HOST"],
            port=environment["POSTGRES_PORT"],
            connect_timeout=10,
        )

    def run_module(module, *arguments):
        result = subprocess.run(
            [sys.executable, "-m", module, *arguments],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, (
            f"Module failed: {module}\n"
            f"{result.stdout}\n{result.stderr}"
        )
        return result.stdout

    # Refuse to use a previously populated test database.
    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_database(),
                       (SELECT COUNT(*) FROM source.orders),
                       (SELECT COUNT(*) FROM platform.ingestion_state);
                """
            )
            database, orders, states = cursor.fetchone()

    assert database == "supplychain_test"
    assert orders == 0 and states == 0, (
        "Test database is not empty. Recreate the test container."
    )

    for module in [
        "seed_master_data",
        "seed_orders",
        "seed_shipments",
        "seed_returns",
    ]:
        run_module(f"supplychain.generator.{module}")

    run_module("supplychain.ingestion.create_baseline")
    run_module("supplychain.generator.add_order_batch")
    run_module("supplychain.ingestion.export_incremental")

    batches = [
        path
        for path in (tmp_path / "data" / "incremental").iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    assert len(batches) == 1
    run_id = batches[0].name

    run_module(
        "supplychain.ingestion.merge_incremental",
        "--run-id",
        run_id,
    )

    comparison = run_module(
        "supplychain.validation.check_incremental_equivalence"
    )
    assert "Incremental snapshot matches the full snapshot." in comparison

    replay = run_module(
        "supplychain.ingestion.merge_incremental",
        "--run-id",
        run_id,
    )
    assert "This incremental batch was already applied." in replay

    with connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM source.orders;")
            assert cursor.fetchone()[0] == 210

            cursor.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT last_successful_run_id)
                FROM platform.ingestion_state;
                """
            )
            assert cursor.fetchone() == (7, 1)

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM platform.ingestion_runs
                WHERE extraction_mode = 'INCREMENTAL'
                  AND status = 'SUCCESS';
                """
            )
            assert cursor.fetchone()[0] == 1