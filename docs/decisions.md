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
- **Volume** (live run, 2026-10-14, after excluding non-profits): 1,779 sales-tax + 9,574 franchise
  rows for the 14/45-day backfill. Steady state for DFW is about 1,040 sales-tax + 2,340 franchise
  rows/week (~3,400). Franchise NAICS is filled for ~4% of rows.
- **Non-profits excluded** (pending client confirmation): rows whose organisation type is `CN`
  (Texas non-profit corporation) or `CM` (foreign non-profit corporation) are skipped and counted as
  `excluded`. Codes from the dataset's record layout ("Franchise Layout.docx" on data.texas.gov);
  both datasets use the same codes. `AR` ("other association") is kept: it mixes PTAs with
  businesses. ~460 franchise and ~16 sales-tax rows in the backfill were excluded.
- **Shared-address flag** (pending client confirmation): `raw_entities.shared_address` is true when
  at least `shared_address_min_entities` (config/thresholds.yaml, 5) rows share `address_key`
  (address line 1 + zip5, normalised like `dedupe_key`). Rows are kept; Job 1 recomputes the flag
  across the whole table after each run. Migration `0006`. ~7.7% of franchise rows are flagged.

### To raise with the client

- Confirm the defaults above: exclude non-profits (CN, CM) in Job 1; keep registered-agent
  addresses but flag them (`shared_address`). 312 DFW SOS rows sit at one Bryan St suite.
- Some sales-tax outlet names are just an address (e.g. "531 BEDFORD ROAD APT 104").

## 2026-10-15: ICP pre-filter, schema_migrations, dotenv (`0007`, `jobs/icp_filter.py`)

- **Pre-filter before paid APIs.** Job 1 sets `raw_entities.icp_candidate` / `icp_industry` /
  `icp_reason` / `icp_rule_version`; Job 2 will only search candidates. Rules are in
  `config/icp_filter.yaml`:
  1. NAICS prefix, any source (longest prefix wins): 621 medical, 7211 hotel, 722 restaurant,
     23 contractor, 531 re_investor.
  2. Otherwise, franchise (`sos`) rows only: whole-word name keywords, strict list. Generic words
     (`holdings`, `ventures`, `capital`, `investments`, `suites`, `bar`, bare `pllc`) were dropped
     after sampling showed they mostly matched non-ICP businesses. `PLLC` counts only next to a
     medical word (therapy, counseling, radiology, care, health, ...). Names ending "RE, LLC" /
     "RE LP" count as re_investor. `equipment` vetoes re_investor ("equipment rental").
  Sales-tax rows are NAICS-only (every row has one). Non-candidates are kept, not deleted.
- **Rule changes**: bump `version` in the config; each Job 1 run re-evaluates rows whose
  `icp_rule_version` differs.
- **Weekly volume** (DFW, measured 2026-10-15 on full weeks: sales tax 10-03..10-09, franchise
  avg of 08-31..09-27): ~3,450 rows in (1,109 sales tax + 2,340 franchise), **~513 pass (15%)**:
  sales tax 211 (restaurant 134, contractor 46, medical 19, hotel 7, re_investor 5); franchise 302
  (re_investor 121, contractor 93, medical 57, restaurant 26, hotel 6). ~21/week passing rows
  have `shared_address`.
- **Shared addresses**: Job 2 searches them anyway; the flag is kept for Job 3's prompt.
- **Industry hint**: `icp_industry` will be passed to Job 3 as a hint; the LLM decides
  `leads_enriched.industry`. Both are stored so disagreement can be measured.
- **schema_migrations** (0007, backfilled with 0001-0006). Every new migration inserts its own
  version inside its transaction; `db/migrate.sh` applies only unrecorded files. Databases that
  already had 0001-0006 (local, Supabase) got 0007 applied directly with psql.
- **python-dotenv**: each job's `main()` calls `load_dotenv()`, since cron/n8n runs don't source
  `.env`. Real environment variables take precedence.

### To raise with the client

- Keyword rules miss ICP businesses with uninformative names ("KOBA SOLUTIONS LLC"); those only
  reach Job 2 if Comptroller NAICS is filled. Acceptable for MVP cost control?
