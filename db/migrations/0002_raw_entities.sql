-- raw_entities: newly registered businesses from SOS, Comptroller, county/city portals (Job 1).
BEGIN;

CREATE TABLE raw_entities (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_name    text NOT NULL,
    owner_name       text,
    address_line1    text NOT NULL,
    city             text NOT NULL,
    state            text NOT NULL DEFAULT 'TX',
    zip              text NOT NULL,
    naics_code       text,
    source_system    text NOT NULL
        CHECK (source_system IN ('sos', 'comptroller', 'county', 'city')),
    -- The day we ingested the record.
    discovered_date  date NOT NULL DEFAULT current_date,
    -- The source's filing/registration date; Job 1's 7-day filter uses it.
    filing_date      date,
    -- The source's own id (SOS filing number, Comptroller taxpayer number).
    source_record_id text,
    -- The source record as received.
    raw_payload      jsonb,
    -- Spec: "deduplicate by business_name + address". Lower-cased, non-alphanumerics
    -- stripped, zip cut to its first 5 digits.
    dedupe_key       text GENERATED ALWAYS AS (
        regexp_replace(lower(business_name), '[^a-z0-9]', '', 'g')
        || '|' || regexp_replace(lower(address_line1), '[^a-z0-9]', '', 'g')
        || '|' || left(regexp_replace(zip, '[^0-9]', '', 'g'), 5)
    ) STORED,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),

    -- Idempotent re-ingest. Rows with a null source_record_id are not constrained
    -- here and fall back to dedupe_key.
    CONSTRAINT raw_entities_source_record_key UNIQUE (source_system, source_record_id),
    CONSTRAINT raw_entities_dedupe_key_key UNIQUE (dedupe_key)
);

CREATE INDEX raw_entities_discovered_date_idx ON raw_entities (discovered_date);
CREATE INDEX raw_entities_filing_date_idx ON raw_entities (filing_date);

CREATE TRIGGER raw_entities_set_updated_at
    BEFORE UPDATE ON raw_entities
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- No policies: only the workers' direct connection may read or write.
ALTER TABLE raw_entities ENABLE ROW LEVEL SECURITY;

COMMIT;
