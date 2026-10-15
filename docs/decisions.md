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

## 2026-10-14: Job 1 ingest (`jobs/ingest.py`, `config/sources.yaml`)

- **Sources**: two Texas Comptroller datasets on data.texas.gov (Socrata, free, no SOSDirect):
  Active Sales Tax Permit Holders (`jrea-zgmq`, `source_system = 'comptroller'`, filtered on
  `outlet_permit_issue_date`) and Active Franchise Taxpayers (`9cir-efmm`, `source_system = 'sos'`
  because the rows are SOS charters, filtered on `sos_charter_date`). County/city portals deferred.
- **Geography**: filtered to DFW counties (Collin, Dallas, Denton, Ellis, Hunt, Johnson, Kaufman,
  Parker, Rockwall, Tarrant, Wise) via Comptroller county codes in `config/sources.yaml`. The
  datasets format codes differently ("057" vs "57"); the query matches both.
- **Spec deviation: lookback window.** The spec filters on filing date >= now - 7 days. As of
  2026-10-14 the datasets lag: sales tax ~4-5 days, franchise tax ~2-3 weeks (almost no charters
  after 09-25 despite a 10-10 refresh). Each source has `lookback_days` (sales_tax 14,
  franchise_tax 45); overlap is harmless because inserts dedupe. `--since` overrides both.
- **Mapping**: sales tax uses the outlet's name (falling back to the taxpayer's legal name) and
  outlet address, since that is where the business operates and what a Places search will match.
  The legal name stays in `raw_payload`. `source_record_id` is `taxpayer_number-outlet_number`
  (sales tax) or the SOS file number (franchise). `owner_name` is always null: neither dataset has it.
  Values are whitespace-collapsed but otherwise kept as published (upper case).
- **Counts**: `success_count` = rows inserted. Duplicates are not errors (per-source counts in
  `job_runs.details`). Rows missing name/address/city/zip are errors. A source that fails after
  retries marks the run `failed` (exit code 1) but the other sources still run.
- **Volume** (first live run, 2026-10-14): 1,795 sales-tax + 10,035 franchise rows inserted for the
  45/14-day backfill; steady state is roughly 900 + 1,500 rows/week. Franchise NAICS is filled for
  ~4% of rows.

### To raise with the client

- Franchise addresses are mailing/registered-agent addresses: 7% of DFW SOS rows sit at an address
  shared by 5+ entities (312 at one Bryan St suite). Filter these, or rely on Job 2 to find the
  real location?
- Franchise rows include non-profits and holding/real-estate LLCs. Filter by organisation type
  in Job 1, or leave it to Job 3's scoring?
- Some sales-tax outlet names are just an address (e.g. "531 BEDFORD ROAD APT 104").
