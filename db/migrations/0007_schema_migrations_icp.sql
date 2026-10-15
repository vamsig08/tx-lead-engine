-- schema_migrations: which migration files have been applied (read by db/migrate.sh).
-- raw_entities: ICP pre-filter result, set by Job 1 from config/icp_filter.yaml.
BEGIN;

CREATE TABLE schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- No policies: only the workers' direct connection may read or write.
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;

-- 0001-0006 were applied before this table existed.
INSERT INTO schema_migrations (version) VALUES
    ('0001'), ('0002'), ('0003'), ('0004'), ('0005'), ('0006'), ('0007');

ALTER TABLE raw_entities
    -- Null until evaluated. Job 2 only searches candidates.
    ADD COLUMN icp_candidate    boolean,
    -- The pre-filter's industry guess; a hint for Job 3, whose LLM decides
    -- leads_enriched.industry. Kept so the two can be compared.
    ADD COLUMN icp_industry     lead_industry,
    -- What matched, e.g. 'naics:722511', 'name:restaurant:TAQUERIA', or 'no_match'.
    ADD COLUMN icp_reason       text,
    -- config/icp_filter.yaml version that produced the result. Job 1 re-evaluates
    -- rows whose version differs from the current config.
    ADD COLUMN icp_rule_version text;

CREATE INDEX raw_entities_icp_candidate_idx ON raw_entities (created_at) WHERE icp_candidate;

COMMIT;
