import argparse
import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from supplychain.db import get_connection
from supplychain.ingestion.export_incremental import DATASETS
from supplychain.validation.check_raw_snapshot import (
    calculate_sha256,
    validate_snapshot,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def read_csv(path, primary_key):
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        header = reader.fieldnames

        if not header or primary_key not in header:
            raise ValueError(f"Missing primary key in {path.name}")

        rows = {}

        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV row in {path.name}")

            key = row[primary_key]

            if not key or key in rows:
                raise ValueError(
                    f"Missing or duplicate primary key in {path.name}"
                )

            rows[key] = row

    return header, rows


def verify_file(path, metadata):
    if not path.is_file():
        raise ValueError(f"Missing file: {path.name}")

    if path.stat().st_size != metadata["size_bytes"]:
        raise ValueError(f"File size mismatch: {path.name}")

    if calculate_sha256(path) != metadata["sha256"]:
        raise ValueError(f"Checksum mismatch: {path.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()

    if (
        not args.run_id
        or args.run_id.startswith(".")
        or any(character in args.run_id for character in "/\\:")
    ):
        parser.error("--run-id must be a batch folder name.")

    incremental_directory = (
        PROJECT_ROOT / "data" / "incremental" / args.run_id
    )

    delta_manifest = json.loads(
        (incremental_directory / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    if (
        delta_manifest.get("run_id") != args.run_id
        or delta_manifest.get("status") != "EXTRACTED"
        or delta_manifest.get("extraction_mode") != "incremental"
    ):
        raise ValueError("Invalid incremental manifest.")

    parent_id = delta_manifest["parent_run_id"]

    if (
        not parent_id
        or parent_id.startswith(".")
        or any(character in parent_id for character in "/\\:")
    ):
        raise ValueError("Invalid parent batch ID.")

    cutoff = datetime.fromisoformat(delta_manifest["cutoff"])

    if cutoff.tzinfo is None:
        raise ValueError("Cutoff must include a timezone.")

    entries = delta_manifest["datasets"]

    if (
        len(entries) != len(DATASETS)
        or {entry["name"] for entry in entries} != set(DATASETS)
    ):
        raise ValueError("Expected seven distinct delta datasets.")

    delta_metadata = {entry["name"]: entry for entry in entries}

    merged_id = f"merged_{args.run_id}"
    raw_root = PROJECT_ROOT / "data" / "raw"
    staging = raw_root / f".incomplete_{merged_id}"
    destination = raw_root / merged_id

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '15s';")
            cursor.execute("SELECT pg_advisory_xact_lock(73010);")

            cursor.execute(
                """
                SELECT dataset_name, watermark, last_successful_run_id
                FROM platform.ingestion_state;
                """
            )

            states = {
                name: (watermark, run_id)
                for name, watermark, run_id in cursor.fetchall()
            }

            if set(states) != set(DATASETS):
                raise ValueError("Incomplete ingestion state.")

            # A completed rerun must not reapply the delta.
            if all(
                watermark == cutoff and run_id == merged_id
                for watermark, run_id in states.values()
            ):
                print("This incremental batch was already applied.")
                return

            for name in DATASETS:
                watermark, current_parent = states[name]
                expected_watermark = datetime.fromisoformat(
                    delta_metadata[name]["previous_watermark"]
                )

                if (
                    current_parent != parent_id
                    or watermark != expected_watermark
                ):
                    raise ValueError(
                        "This batch is stale relative to ingestion state. "
                        "Export a new incremental batch."
                    )

                if cutoff <= watermark:
                    raise ValueError("Cutoff must advance the watermark.")

            parent_directory = raw_root / parent_id
            validate_snapshot(parent_directory)

            merged_manifest = {
                "run_id": merged_id,
                "schema_version": 1,
                "extraction_mode": "merged_snapshot",
                "parent_run_id": parent_id,
                "incremental_run_id": args.run_id,
                "cutoff": cutoff.isoformat(),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "status": "SUCCESS",
                "datasets": [],
            }

            if staging.exists():
                raise RuntimeError(
                    "Incomplete merge folder exists. "
                    "Inspect it before retrying."
                )

            staging.mkdir()
            published = False

            try:
                for name, columns in DATASETS.items():
                    primary_key, change_column = columns
                    metadata = delta_metadata[name]

                    if (
                        metadata["file"] != f"{name}.csv"
                        or metadata["primary_key"] != primary_key
                        or metadata["change_column"] != change_column
                    ):
                        raise ValueError(f"Invalid metadata for {name}")

                    delta_path = incremental_directory / f"{name}.csv"
                    verify_file(delta_path, metadata)

                    header, merged_rows = read_csv(
                        parent_directory / f"{name}.csv",
                        primary_key,
                    )
                    delta_header, delta_rows = read_csv(
                        delta_path, primary_key
                    )

                    if header != delta_header:
                        raise ValueError(f"Schema mismatch: {name}")

                    if len(delta_rows) != metadata["row_count"]:
                        raise ValueError(f"Delta row count mismatch: {name}")

                    previous = states[name][0]

                    for row in delta_rows.values():
                        changed_at = datetime.fromisoformat(
                            row[change_column]
                        )

                        if not previous < changed_at <= cutoff:
                            raise ValueError(
                                f"Record outside extraction window: {name}"
                            )

                    # Existing keys are replaced; new keys are inserted.
                    merged_rows.update(delta_rows)

                    output_path = staging / f"{name}.csv"

                    with output_path.open(
                        "w", encoding="utf-8", newline=""
                    ) as output:
                        writer = csv.DictWriter(
                            output,
                            fieldnames=header,
                            lineterminator="\n",
                        )
                        writer.writeheader()

                        for key in sorted(merged_rows, key=int):
                            writer.writerow(merged_rows[key])

                    merged_manifest["datasets"].append(
                        {
                            "name": name,
                            "file": output_path.name,
                            "row_count": len(merged_rows),
                            "size_bytes": output_path.stat().st_size,
                            "sha256": calculate_sha256(output_path),
                        }
                    )

                    print(f"Merged {name}: {len(merged_rows)} rows")

                merged_manifest["completed_at"] = datetime.now(
                    timezone.utc
                ).isoformat()

                (staging / "manifest.json").write_text(
                    json.dumps(merged_manifest, indent=2),
                    encoding="utf-8",
                )

                if destination.exists():
                    # Recover an earlier publication whose DB commit
                    # did not finish. Its inputs must match this merge.
                    validate_snapshot(destination)

                    existing_manifest = json.loads(
                        (destination / "manifest.json").read_text(
                            encoding="utf-8"
                        )
                    )

                    if (
                        existing_manifest.get("parent_run_id") != parent_id
                        or existing_manifest.get("incremental_run_id")
                        != args.run_id
                        or existing_manifest.get("cutoff")
                        != cutoff.isoformat()
                        or existing_manifest["datasets"]
                        != merged_manifest["datasets"]
                    ):
                        raise ValueError(
                            "Existing merged output differs from this batch."
                        )

                    shutil.rmtree(staging)
                else:
                    staging.rename(destination)

                published = True
                validate_snapshot(destination)

                # Force a fresh report build on retries rather than
                # accepting possibly incomplete previous reports.
                reports_directory = (
                    PROJECT_ROOT / "data" / "curated" / merged_id
                )

                if reports_directory.exists():
                    shutil.rmtree(reports_directory)

                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "supplychain.transformations.build_reports",
                        "--run-id",
                        merged_id,
                    ],
                    cwd=PROJECT_ROOT,
                    check=True,
                )

                for name in DATASETS:
                    cursor.execute(
                        """
                        UPDATE platform.ingestion_state
                        SET watermark = %s,
                            last_successful_run_id = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE dataset_name = %s;
                        """,
                        (cutoff, merged_id, name),
                    )

                cursor.execute(
                    """
                    INSERT INTO platform.ingestion_runs (
                        run_id,
                        extraction_mode,
                        status,
                        completed_at
                    )
                    VALUES (%s, 'INCREMENTAL', 'SUCCESS',
                            CURRENT_TIMESTAMP);
                    """,
                    (args.run_id,),
                )

            except Exception:
                if not published:
                    shutil.rmtree(staging, ignore_errors=True)
                raise

    print("\nIncremental batch applied successfully.")
    print(f"Merged snapshot: {merged_id}")
    print("Reports published and watermarks advanced.")


if __name__ == "__main__":
    main()