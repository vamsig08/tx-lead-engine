-- job_runs: one row per run of each job.
BEGIN;

CREATE TABLE job_runs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_name      text NOT NULL
        CHECK (job_name IN ('ingest', 'contacts', 'enrich', 'push')),
    started_at    timestamptz NOT NULL DEFAULT now(),
    -- Null while running.
    finished_at   timestamptz,
    success_count integer NOT NULL DEFAULT 0 CHECK (success_count >= 0),
    error_count   integer NOT NULL DEFAULT 0 CHECK (error_count >= 0),
    status        text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    -- Error summaries, per-source counts.
    details       jsonb,
    -- API/LLM spend for the run. Null = not recorded.
    cost_usd      numeric(10,4) CHECK (cost_usd >= 0)
);

CREATE INDEX job_runs_job_name_started_at_idx ON job_runs (job_name, started_at DESC);

-- No policies: only the workers' direct connection may read or write.
ALTER TABLE job_runs ENABLE ROW LEVEL SECURITY;

COMMIT;
