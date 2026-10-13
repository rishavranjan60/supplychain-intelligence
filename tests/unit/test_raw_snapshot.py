import csv
import json

import pytest

from supplychain.validation.check_raw_snapshot import (
    EXPECTED_DATASETS,
    calculate_sha256,
    validate_snapshot,
)


def create_snapshot(tmp_path):
    directory = tmp_path / "test_batch"
    directory.mkdir()

    manifest = {
        "run_id": directory.name,
        "status": "SUCCESS",
        "datasets": [],
    }

    for name in sorted(EXPECTED_DATASETS):
        path = directory / f"{name}.csv"

        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["id", "description"])
            writer.writerow([1, "Contains a comma, and\nanother line"])

        manifest["datasets"].append(
            {
                "name": name,
                "file": path.name,
                "row_count": 1,
                "size_bytes": path.stat().st_size,
                "sha256": calculate_sha256(path),
            }
        )

    save_manifest(directory, manifest)
    return directory, manifest


def save_manifest(directory, manifest):
    (directory / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )


def test_valid_snapshot_with_multiline_csv(tmp_path):
    directory, _ = create_snapshot(tmp_path)
    validate_snapshot(directory)


def test_modified_file_is_rejected(tmp_path):
    directory, _ = create_snapshot(tmp_path)

    with (directory / "orders.csv").open("ab") as file:
        file.write(b"2,unexpected record\n")

    with pytest.raises(SystemExit) as error:
        validate_snapshot(directory)

    assert error.value.code == 1


def test_missing_file_is_rejected(tmp_path):
    directory, _ = create_snapshot(tmp_path)
    (directory / "orders.csv").unlink()

    with pytest.raises(SystemExit) as error:
        validate_snapshot(directory)

    assert error.value.code == 1


def test_incorrect_row_count_is_rejected(tmp_path):
    directory, manifest = create_snapshot(tmp_path)

    for dataset in manifest["datasets"]:
        if dataset["name"] == "orders":
            dataset["row_count"] = 99

    save_manifest(directory, manifest)

    with pytest.raises(SystemExit) as error:
        validate_snapshot(directory)

    assert error.value.code == 1


def test_incomplete_batch_is_rejected(tmp_path):
    directory, manifest = create_snapshot(tmp_path)
    manifest["status"] = "IN_PROGRESS"
    save_manifest(directory, manifest)

    with pytest.raises(ValueError, match="not marked SUCCESS"):
        validate_snapshot(directory)


def test_duplicate_dataset_is_rejected(tmp_path):
    directory, manifest = create_snapshot(tmp_path)
    manifest["datasets"][0] = manifest["datasets"][1].copy()
    save_manifest(directory, manifest)

    with pytest.raises(ValueError, match="exactly once"):
        validate_snapshot(directory)