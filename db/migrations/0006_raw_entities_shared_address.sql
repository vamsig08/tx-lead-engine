-- raw_entities: flag addresses shared by many entities (registered agents, mail drops).
-- Pending client confirmation; see docs/decisions.md (2026-10-14).
BEGIN;

ALTER TABLE raw_entities
    -- Address half of dedupe_key: address line 1 + zip5, normalised the same way.
    ADD COLUMN address_key text GENERATED ALWAYS AS (
        regexp_replace(lower(address_line1), '[^a-z0-9]', '', 'g')
        || '|' || left(regexp_replace(zip, '[^0-9]', '', 'g'), 5)
    ) STORED,
    -- Set by Job 1 after each run: true when at least
    -- config/thresholds.yaml shared_address_min_entities rows share address_key.
    ADD COLUMN shared_address boolean NOT NULL DEFAULT false;

CREATE INDEX raw_entities_address_key_idx ON raw_entities (address_key);

COMMIT;
