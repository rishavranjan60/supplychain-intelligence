import argparse
import csv
import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]

EXPECTED_DATASETS = {
    "products",
    "warehouses",
    "orders",
    "order_items",
    "inventory_movements",
    "shipments",
    "returns",
}


def calculate_sha256(path):
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def validate_snapshot(directory):
    manifest_path = directory / "manifest.json"

    if not manifest_path.is_file():
        raise ValueError("manifest.json is missing.")

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    if manifest.get("status") != "SUCCESS":
        raise ValueError("Snapshot is not marked SUCCESS.")

    if manifest.get("run_id") != directory.name:
        raise ValueError("Run ID does not match the batch folder.")

    datasets = manifest.get("datasets", [])
    names = [dataset["name"] for dataset in datasets]

    if (
        len(names) != len(EXPECTED_DATASETS)
        or set(names) != EXPECTED_DATASETS
    ):
        raise ValueError(
            "Manifest must contain each expected dataset exactly once."
        )

    failures = []

    for dataset in datasets:
        name = dataset["name"]
        expected_filename = f"{name}.csv"

        if dataset.get("file") != expected_filename:
            failures.append(f"{name}: unexpected filename")
            continue

        file_path = directory / expected_filename

        if not file_path.is_file():
            failures.append(f"{name}: file missing")
            continue

        dataset_errors = []

        if file_path.stat().st_size != dataset["size_bytes"]:
            dataset_errors.append("file size mismatch")

        if calculate_sha256(file_path) != dataset["sha256"]:
            dataset_errors.append("checksum mismatch")

        with file_path.open(
            "r", encoding="utf-8", newline=""
        ) as file:
            reader = csv.reader(file)
            header = next(reader, None)

            if not header:
                dataset_errors.append("missing CSV header")
                row_count = 0
            else:
                if len(header) != len(set(header)):
                    dataset_errors.append("duplicate column names")

                row_count = 0
                invalid_width_count = 0

                for row in reader:
                    row_count += 1

                    if len(row) != len(header):
                        invalid_width_count += 1

                if invalid_width_count:
                    dataset_errors.append(
                        f"{invalid_width_count} rows have "
                        "an incorrect number of fields"
                    )

        if row_count != dataset["row_count"]:
            dataset_errors.append(
                f"row count mismatch: expected "
                f"{dataset['row_count']}, found {row_count}"
            )

        if dataset_errors:
            print(f"FAIL: {name}")
            failures.extend(
                f"{name}: {error}" for error in dataset_errors
            )
        else:
            print(f"PASS: {name} ({row_count} rows)")

    if failures:
        print("\nValidation failed:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)

    print("\nAll raw snapshot checks passed.")
    print(f"Validated batch: {directory.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-id",
        required=True,
        help="Exact folder name of the exported snapshot.",
    )
    args = parser.parse_args()

    # Accept a folder name, not an arbitrary path.
    if (
        not args.run_id
        or args.run_id.startswith(".")
        or "/" in args.run_id
        or "\\" in args.run_id
        or ":" in args.run_id
    ):
        parser.error("--run-id must be a batch folder name.")

    directory = PROJECT_ROOT / "data" / "raw" / args.run_id

    if not directory.is_dir():
        parser.error(f"Snapshot folder does not exist: {directory}")

    validate_snapshot(directory)


if __name__ == "__main__":
    main()