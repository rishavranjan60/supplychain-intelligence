import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from supplychain.ingestion.export_snapshot import (
    main as export_snapshot,
)
from supplychain.validation.check_raw_snapshot import (
    validate_snapshot,
)
from supplychain.validation.check_source_data import (
    main as check_source_data,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def main():
    execution_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "_"
        + uuid4().hex[:8]
    )

    log_directory = PROJECT_ROOT / "logs" / "pipeline"
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / f"{execution_id}.json"

    record = {
        "execution_id": execution_id,
        "started_at": utc_now(),
        "status": "RUNNING",
        "source_run_id": None,
        "steps": [],
    }

    def save_log():
        temporary_path = log_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(record, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(log_path)

    def run_step(name, function):
        step = {
            "name": name,
            "started_at": utc_now(),
            "status": "RUNNING",
        }
        record["steps"].append(step)
        save_log()

        print(f"\n--- {name} ---", flush=True)

        try:
            result = function()
        except BaseException as error:
            step["status"] = "FAILED"
            step["error"] = f"{type(error).__name__}: {error}"
            step["completed_at"] = utc_now()
            save_log()
            raise

        step["status"] = "SUCCESS"
        step["completed_at"] = utc_now()
        save_log()
        return result

    save_log()

    try:
        run_step("Validate source data", check_source_data)

        run_id = run_step("Export snapshot", export_snapshot)
        record["source_run_id"] = run_id
        save_log()

        raw_directory = PROJECT_ROOT / "data" / "raw" / run_id

        run_step(
            "Validate raw snapshot",
            lambda: validate_snapshot(raw_directory),
        )

        run_step(
            "Build reporting datasets",
            lambda: subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "supplychain.transformations.build_reports",
                    "--run-id",
                    run_id,
                ],
                cwd=PROJECT_ROOT,
                check=True,
            ),
        )

    except BaseException as error:
        record["status"] = "FAILED"
        record["error"] = f"{type(error).__name__}: {error}"
        record["completed_at"] = utc_now()
        save_log()

        print(f"\nPipeline failed. Log: {log_path}")

        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise

        traceback.print_exc()
        raise SystemExit(1)

    record["status"] = "SUCCESS"
    record["completed_at"] = utc_now()
    save_log()

    print("\nPipeline completed successfully.")
    print(f"Reporting batch: {run_id}")
    print(f"Execution log: {log_path}")


if __name__ == "__main__":
    main()