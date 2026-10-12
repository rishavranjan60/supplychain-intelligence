## Implemented

- PostgreSQL source database with seven operational tables
- Repeat-safe synthetic master data, orders, shipments and returns
- Source-data validation and inventory reconciliation
- Consistent full-snapshot CSV extraction
- Batch manifests with record counts and SHA-256 checksums
- Raw-file integrity validation
- Parquet reporting datasets
- Streamlit analytics dashboard
- One-command pipeline execution with step-status logs

## Run locally

Start PostgreSQL:
docker compose up -d

Run the pipeline:
.\.venv\Scripts\python.exe -m supplychain.run_pipeline

Start the dashboard:
.\.venv\Scripts\python.exe -m streamlit run dashboard/app.py

## Current limitations

- Ingestion creates full snapshots, not incremental batches.
- Data is synthetic.
- Inventory reporting shows closing balances, not daily snapshots.
- Stock movements are reconciled by product and warehouse.
- AWS deployment and forecasting are not implemented yet.