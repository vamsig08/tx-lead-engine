-- Shared helpers used by the table migrations.
BEGIN;

CREATE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

-- Spec: industry enum for leads_enriched.
CREATE TYPE lead_industry AS ENUM (
    'medical',
    're_investor',
    'hotel',
    'contractor',
    'restaurant',
    'other'
);

COMMIT;
