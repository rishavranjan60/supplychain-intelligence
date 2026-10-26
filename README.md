# SupplyChain Intelligence

A local supply-chain data engineering platform built with Python, PostgreSQL, Parquet and Streamlit.

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

## Environment setup

Install Python 3.10 or newer and Docker with Compose support. From the repository root:

```bash
python -m venv .venv
```

Activate the environment with `.venv\\Scripts\\Activate.ps1` in PowerShell or `source .venv/bin/activate` on Linux/macOS, then install dependencies:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pip install -e .
```

Copy `.env.example` to `.env` and review the local database settings before starting PostgreSQL. Keep `.env` out of Git. The pipeline expects source data; generator modules are in `src/supplychain/generator/`.

## Run locally

Start PostgreSQL:

```bash
docker compose up -d
```

Run the pipeline with the virtual environment activated:

```bash
python -m supplychain.run_pipeline
```

Start the dashboard:

```bash
python -m streamlit run dashboard/app.py
```

## Unit tests

```bash
python -m pytest tests/unit -v
```

## Incremental ingestion

Separate modules for baseline creation, incremental export, merging and equivalence validation are present under `src/supplychain/ingestion/` and `src/supplychain/validation/`. The ingestion-state migration is `sql/migrations/002_ingestion_state.sql`. These steps are separate from the full-snapshot pipeline command above.

## Current limitations

- The one-command pipeline currently runs full snapshots; incremental ingestion uses separate modules.
- Data is synthetic.
- Inventory reporting shows closing balances, not daily snapshots.
- Stock movements are reconciled by product and warehouse.
- AWS deployment and forecasting are not implemented yet.