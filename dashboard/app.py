import hashlib
import json
from pathlib import Path

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CURATED_ROOT = PROJECT_ROOT / "data" / "curated"

REPORT_NAMES = [
    "daily_sales",
    "current_inventory",
    "shipment_performance",
]


def calculate_sha256(path):
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def load_reports(directory):
    manifest = json.loads(
        (directory / "manifest.json").read_text(encoding="utf-8")
    )

    if manifest.get("status") != "SUCCESS":
        raise ValueError("This reporting batch is incomplete.")

    if manifest.get("run_id") != directory.name:
        raise ValueError("Manifest run ID does not match the folder.")

    entries = manifest.get("reports", [])

    if (
        len(entries) != len(REPORT_NAMES)
        or {entry["name"] for entry in entries} != set(REPORT_NAMES)
    ):
        raise ValueError("The expected reporting datasets are missing.")

    reports = {}

    for entry in entries:
        name = entry["name"]

        if entry["file"] != f"{name}.parquet":
            raise ValueError(f"Unexpected filename for {name}.")

        path = directory / entry["file"]

        if calculate_sha256(path) != entry["sha256"]:
            raise ValueError(f"Checksum mismatch: {name}")

        dataframe = pd.read_parquet(path)

        if len(dataframe) != entry["row_count"]:
            raise ValueError(f"Row count mismatch: {name}")

        reports[name] = dataframe

    return manifest, reports


st.set_page_config(
    page_title="SupplyChain Intelligence",
    page_icon="📦",
    layout="wide",
)

st.title("SupplyChain Intelligence")
st.caption("Supply-chain analytics using synthetic data.")

if not CURATED_ROOT.exists():
    st.info("Create a curated reporting batch first.")
    st.stop()

batches = sorted(
    [
        path.name
        for path in CURATED_ROOT.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
        and (path / "manifest.json").is_file()
    ],
    reverse=True,
)

if not batches:
    st.info("No published reporting batches found.")
    st.stop()

selected_batch = st.sidebar.selectbox(
    "Reporting batch",
    batches,
)

try:
    manifest, reports = load_reports(
        CURATED_ROOT / selected_batch
    )
except Exception as error:
    st.error(f"Cannot load this batch: {error}")
    st.stop()

sales = reports["daily_sales"]
inventory = reports["current_inventory"]
performance = reports["shipment_performance"]

warehouse_ids = sorted(
    set(sales["warehouse_id"])
    | set(inventory["warehouse_id"])
    | set(performance["warehouse_id"])
)

selected_warehouse = st.sidebar.selectbox(
    "Warehouse",
    ["All"] + warehouse_ids,
)

if selected_warehouse != "All":
    sales = sales.loc[
        sales["warehouse_id"] == selected_warehouse
    ].copy()

    inventory = inventory.loc[
        inventory["warehouse_id"] == selected_warehouse
    ].copy()

    performance = performance.loc[
        performance["warehouse_id"] == selected_warehouse
    ].copy()

sales_tab, inventory_tab, delivery_tab, health_tab = st.tabs(
    ["Sales", "Inventory", "Delivery", "Batch health"]
)

with sales_tab:
    st.subheader("Shipped sales")

    units = int(sales["units_shipped"].sum())
    gross_value = (
        sales["gross_shipped_value_cents"].sum() / 100
    )

    left, right = st.columns(2)
    left.metric("Units shipped", f"{units:,}")
    right.metric("Gross shipped value (EUR)", f"{gross_value:,.2f}")

    st.caption(
        "Grouped by UTC shipment date. "
        "Returns, discounts and taxes are not deducted. "
        "This demo assumes all prices are in EUR."
    )

    if sales.empty:
        st.info("No sales for this selection.")
    else:
        daily_totals = (
            sales.groupby("date")[["units_shipped"]]
            .sum()
            .sort_index()
        )

        st.line_chart(daily_totals)
        st.dataframe(sales, hide_index=True, use_container_width=True)

with inventory_tab:
    st.subheader("Current inventory")

    threshold = st.number_input(
        "Low-stock threshold",
        min_value=0,
        value=100,
        step=10,
    )

    low_stock = inventory.loc[
        inventory["stock_on_hand"] < threshold
    ]

    left, right = st.columns(2)
    left.metric(
        "Total stock units",
        f"{int(inventory['stock_on_hand'].sum()):,}",
    )
    right.metric("Low-stock product/warehouse pairs", len(low_stock))

    st.caption(
        "Balances include all inventory movements in the selected "
        "snapshot. They are not live stock levels."
    )

    st.dataframe(
        inventory,
        hide_index=True,
        use_container_width=True,
    )

    st.markdown("**Low-stock items**")
    st.dataframe(
        low_stock,
        hide_index=True,
        use_container_width=True,
    )

with delivery_tab:
    st.subheader("Delivery performance")

    delivered_count = int(
        performance["delivered_shipments"].sum()
    )
    on_time_count = int(
        performance["on_time_shipments"].sum()
    )

    if delivered_count:
        # Weighted across warehouses, not an average of percentages.
        on_time_rate = on_time_count / delivered_count

        average_hours = (
            (
                performance["average_delivery_hours"]
                * performance["delivered_shipments"]
            ).sum()
            / delivered_count
        )

        first, second, third = st.columns(3)
        first.metric("Delivered shipments", delivered_count)
        second.metric("On-time delivery", f"{on_time_rate:.1%}")
        third.metric("Average delivery hours", f"{average_hours:.1f}")
    else:
        st.info("No delivered shipments for this selection.")

    st.caption(
        "On-time means delivered on or before the promised timestamp. "
        "Only delivered shipments are included."
    )

    st.dataframe(
        performance,
        hide_index=True,
        use_container_width=True,
    )

with health_tab:
    st.subheader("Reporting batch")
    st.success("Manifest, file checksums and row counts verified.")

    st.write("Source batch:", manifest["source_run_id"])
    st.write("Reports created at:", manifest["created_at"])

    st.caption(
        "This confirms artifact integrity, not every business rule. "
        "Pipeline monitoring and data-freshness alerts come later."
    )

    st.dataframe(
        pd.DataFrame(manifest["reports"])[
            ["name", "row_count", "sha256"]
        ],
        hide_index=True,
        use_container_width=True,
    )