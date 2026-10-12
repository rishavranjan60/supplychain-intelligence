import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from supplychain.validation.check_raw_snapshot import (
    calculate_sha256,
    validate_snapshot,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def read_csv(directory, name):
    return pd.read_csv(
        directory / f"{name}.csv",
        keep_default_na=False,
    )


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

    raw_directory = PROJECT_ROOT / "data" / "raw" / args.run_id
    curated_root = PROJECT_ROOT / "data" / "curated"
    final_directory = curated_root / args.run_id
    staging_directory = curated_root / f".incomplete_{args.run_id}"

    if final_directory.exists():
        print("Reports already exist for this batch. No files changed.")
        return

    # Refuse to transform corrupted or incomplete input.
    validate_snapshot(raw_directory)

    if staging_directory.exists():
        raise RuntimeError(
            "An incomplete output folder already exists. "
            "Check that another transformation is not running."
        )

    curated_root.mkdir(parents=True, exist_ok=True)
    staging_directory.mkdir()

    try:
        products = read_csv(raw_directory, "products")
        warehouses = read_csv(raw_directory, "warehouses")
        orders = read_csv(raw_directory, "orders")
        items = read_csv(raw_directory, "order_items")
        movements = read_csv(raw_directory, "inventory_movements")
        shipments = read_csv(raw_directory, "shipments")

        # Product and warehouse keys must be unique.
        if products["product_id"].duplicated().any():
            raise ValueError("Duplicate product IDs.")

        if warehouses["warehouse_id"].duplicated().any():
            raise ValueError("Duplicate warehouse IDs.")

        if orders["order_id"].duplicated().any():
            raise ValueError("Duplicate order IDs.")

        if shipments["order_id"].duplicated().any():
            raise ValueError("Multiple shipments per order are unsupported.")

        if not items["order_id"].isin(orders["order_id"]).all():
            raise ValueError("An order item references an unknown order.")

        if not items["product_id"].isin(products["product_id"]).all():
            raise ValueError("An order item references an unknown product.")

        if not orders["warehouse_id"].isin(
            warehouses["warehouse_id"]
        ).all():
            raise ValueError("An order references an unknown warehouse.")

        if not shipments["order_id"].isin(orders["order_id"]).all():
            raise ValueError("A shipment references an unknown order.")

        if not movements["product_id"].isin(
            products["product_id"]
        ).all():
            raise ValueError("A movement references an unknown product.")

        if not movements["warehouse_id"].isin(
            warehouses["warehouse_id"]
        ).all():
            raise ValueError("A movement references an unknown warehouse.")

        if (items["quantity"] <= 0).any():
            raise ValueError("Order quantities must be positive.")

        if (items["unit_price"] < 0).any():
            raise ValueError("Order prices cannot be negative.")

        for column in [
            "shipped_at",
            "promised_delivery_at",
            "delivered_at",
        ]:
            shipments[column] = pd.to_datetime(
                shipments[column].replace("", pd.NA),
                utc=True,
                errors="raise",
            )

        if shipments[
            ["shipped_at", "promised_delivery_at"]
        ].isna().any().any():
            raise ValueError("Required shipment timestamps are missing.")

        if (
            shipments["promised_delivery_at"]
            < shipments["shipped_at"]
        ).any():
            raise ValueError("Promised delivery precedes shipment.")

        if (
            shipments["delivered_at"] < shipments["shipped_at"]
        ).any():
            raise ValueError("Delivery precedes shipment.")

        # REPORT 1: shipped units and gross shipped value.
        # Returns, taxes and discounts are not deducted.
        shipped_lines = items.merge(
            orders[["order_id", "warehouse_id"]],
            on="order_id",
            validate="many_to_one",
        ).merge(
            shipments[["order_id", "shipped_at"]],
            on="order_id",
            validate="many_to_one",
        )

        shipped_lines["date"] = (
            shipped_lines["shipped_at"]
            .dt.floor("D")
            .dt.tz_localize(None)
        )

        # Store money as integer cents rather than aggregated floats.
        shipped_lines["unit_price_cents"] = (
            shipped_lines["unit_price"] * 100
        ).round().astype("int64")

        shipped_lines["gross_value_cents"] = (
            shipped_lines["quantity"]
            * shipped_lines["unit_price_cents"]
        )

        daily_sales = (
            shipped_lines.groupby(
                ["date", "warehouse_id", "product_id"],
                as_index=False,
            )
            .agg(
                units_shipped=("quantity", "sum"),
                gross_shipped_value_cents=(
                    "gross_value_cents", "sum"
                ),
                order_count=("order_id", "nunique"),
            )
            .sort_values(["date", "warehouse_id", "product_id"])
        )

        # REPORT 2: closing inventory from all snapshot movements.
        inventory = (
            movements.groupby(
                ["warehouse_id", "product_id"],
                as_index=False,
            )
            .agg(stock_on_hand=("quantity_change", "sum"))
            .sort_values(["warehouse_id", "product_id"])
        )

        if (inventory["stock_on_hand"] < 0).any():
            raise ValueError("Negative closing inventory detected.")

        # REPORT 3: performance among delivered shipments.
        delivered = shipments.loc[
            shipments["status"] == "DELIVERED"
        ].copy()

        if delivered["delivered_at"].isna().any():
            raise ValueError(
                "A delivered shipment has no delivery timestamp."
            )

        delivered = delivered.merge(
            orders[["order_id", "warehouse_id"]],
            on="order_id",
            validate="many_to_one",
        )

        delivered["on_time"] = (
            delivered["delivered_at"]
            <= delivered["promised_delivery_at"]
        )

        delivered["delivery_hours"] = (
            delivered["delivered_at"] - delivered["shipped_at"]
        ).dt.total_seconds() / 3600

        shipment_performance = (
            delivered.groupby("warehouse_id", as_index=False)
            .agg(
                delivered_shipments=("shipment_id", "count"),
                on_time_shipments=("on_time", "sum"),
                average_delivery_hours=("delivery_hours", "mean"),
            )
        )

        shipment_performance["on_time_delivery_rate"] = (
            shipment_performance["on_time_shipments"]
            / shipment_performance["delivered_shipments"]
        )

        # Reconcile the aggregation against its input.
        if int(daily_sales["units_shipped"].sum()) != int(
            shipped_lines["quantity"].sum()
        ):
            raise ValueError("Shipped-unit reconciliation failed.")

        if int(inventory["stock_on_hand"].sum()) != int(
            movements["quantity_change"].sum()
        ):
            raise ValueError("Inventory reconciliation failed.")

        reports = {
            "daily_sales": daily_sales,
            "current_inventory": inventory,
            "shipment_performance": shipment_performance,
        }

        manifest = {
            "run_id": args.run_id,
            "source_run_id": args.run_id,
            "status": "SUCCESS",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reports": [],
        }

        for name, dataframe in reports.items():
            file_path = staging_directory / f"{name}.parquet"
            dataframe.to_parquet(file_path, index=False)

            manifest["reports"].append(
                {
                    "name": name,
                    "file": file_path.name,
                    "row_count": len(dataframe),
                    "sha256": calculate_sha256(file_path),
                }
            )

            print(f"Created {name}: {len(dataframe)} rows")

        (staging_directory / "manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

        staging_directory.rename(final_directory)

    except Exception:
        shutil.rmtree(staging_directory, ignore_errors=True)
        raise

    print(f"\nReports published to: {final_directory}")


if __name__ == "__main__":
    main()