BEGIN;

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.ingestion_state (
    dataset_name TEXT PRIMARY KEY,
    watermark TIMESTAMPTZ NOT NULL,
    last_successful_run_id TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS platform.ingestion_runs (
    run_id TEXT PRIMARY KEY,
    extraction_mode TEXT NOT NULL CHECK (
        extraction_mode IN ('BASELINE', 'INCREMENTAL')
    ),
    status TEXT NOT NULL CHECK (
        status IN ('RUNNING', 'SUCCESS', 'FAILED')
    ),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    error_message TEXT
);

COMMIT;