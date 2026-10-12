-- contacts_enriched: contact info found via Google Places and the business's own website (Job 2).
BEGIN;

CREATE TABLE contacts_enriched (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_entity_id        uuid NOT NULL UNIQUE REFERENCES raw_entities (id),
    website_url          text,
    email                text,
    phone                text,
    google_maps_url      text,
    yelp_url             text,
    bbb_url              text,
    status               text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'found', 'not_found')),
    -- Deviation: discovery uses the Google Places API.
    google_place_id      text,
    places_fetched_at    timestamptz,
    -- Deviation: cleaned website text, used by Job 3's prompt.
    site_text            text,
    site_text_fetched_at timestamptz,
    -- Deviation: not_found rows are kept and re-checked later.
    recheck_attempts     integer NOT NULL DEFAULT 0 CHECK (recheck_attempts >= 0),
    last_checked_at      timestamptz,
    -- Null = no re-check scheduled.
    next_recheck_at      timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX contacts_enriched_status_idx ON contacts_enriched (status);
CREATE INDEX contacts_enriched_next_recheck_at_idx ON contacts_enriched (next_recheck_at)
    WHERE status = 'not_found';

CREATE TRIGGER contacts_enriched_set_updated_at
    BEFORE UPDATE ON contacts_enriched
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- No policies: only the workers' direct connection may read or write.
ALTER TABLE contacts_enriched ENABLE ROW LEVEL SECURITY;

COMMIT;
