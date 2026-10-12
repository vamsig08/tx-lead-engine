# Decisions

## 2026-10-11: Schema (`db/migrations/0001`-`0005`)

- **Four tables**: `raw_entities`, `contacts_enriched`, `leads_enriched`, `job_runs`. The producer
  review step is columns on `leads_enriched` (one decision per lead), not a separate table.
- **`discovered_date` vs `filing_date`**: `discovered_date` is the day we ingested the record.
  `filing_date` (nullable) is the source's filing date, and Job 1's 7-day filter uses it.
- **Dedupe**: two unique constraints on `raw_entities`. `(source_system, source_record_id)` makes
  re-ingest from one source idempotent; rows without a source id are not constrained by it.
  `dedupe_key` (generated: lower-cased business name + address line 1 with non-alphanumerics
  stripped, plus the first 5 digits of zip) implements the spec's "business_name + address" rule
  across sources. Normalisation is minimal: "L.L.C." matches "LLC", "Inc" does not match
  "Incorporated".
- **`state`** defaults to `TX` but has no CHECK, since a Texas filing can carry an out-of-state address.
- **Enums**: `industry` is a native Postgres enum (per spec). Statuses, `source_system`, `needs`
  and `review_verdict` are `text` with CHECK constraints so values can be added without `ALTER TYPE`.
- **Contacts**: one `contacts_enriched` row per entity. `google_place_id` / `places_fetched_at`
  support the Places API deviation; `site_text` holds cleaned website text for Job 3;
  `recheck_attempts`, `last_checked_at`, `next_recheck_at` support re-checking `not_found` rows.
- **Leads**: one lead per contacts row. `llm_model` and `prompt_version` record what produced a score.
- **Job 4 selection** is `review_verdict = 'approved' AND pushed_at IS NULL AND score >= threshold`,
  not the spec's `momentum_prospect_id IS NULL`. With `momentum_tags_applied_at` and
  `momentum_task_id`, a run can finish a lead whose prospect exists but whose tags or task failed,
  without creating a second prospect. The threshold stays in `config/thresholds.yaml`.
- **`job_runs`** adds `status`, `details` and `cost_usd` to the spec's five columns. `cost_usd` is
  nullable so a run that crashed before recording spend does not read as costing 0.
- **Row level security** is enabled on every table with no policies, because Supabase exposes the
  `public` schema over its REST API. Workers connect directly as the table owner, which bypasses RLS.

### To raise with the client

- Google's Places terms limit how long Places-sourced content (phone, website) may be stored;
  place IDs are exempt. The limit is believed to be 30 days but has not been checked against the
  current terms.
- `address_line1` and `zip` are NOT NULL per spec, so source rows missing them will be counted as
  ingest errors.
