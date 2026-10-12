# TX New-Business Lead Engine

Finds newly registered Texas businesses, discovers contact info, scores them as commercial-insurance
prospects with an LLM, and pushes qualified leads into Momentum AMS as prospects + tags + call tasks.

## Source of truth
- `docs/spec.md` is the client's spec: data model, 4 jobs, Momentum field/tag mapping, payloads,
  error handling. Follow it unless a deviation below says otherwise.
- If something in the spec is ambiguous or seems wrong, stop and ask. Do not invent behavior.

## Agreed deviations from the spec
- Contact discovery uses the Google Places API, NOT Playwright scraping of Google search results.
  Playwright/HTTP is only for crawling a business's own website (contact/about/footer pages).
- Job 2 also stores cleaned website text (needed by Job 3's prompt).
- `not_found` contacts are kept and re-checked later (pending client confirmation; see open questions).
- MVP inserts a producer review step (Google Sheet/CSV export) between Job 3 and Job 4.

## Stack
- Postgres (Supabase). Schema lives in `db/migrations/` as plain SQL.
- Python 3.12 workers, one module per job in `jobs/` (ingest, contacts, enrich, push).
- Orchestration: n8n (per spec) calls the workers; workers must also run standalone from the CLI.
- LLM: Azure OpenAI or OpenAI (per spec). Structured JSON output validated with Pydantic;
  industry and needs are strict enums matching the spec.
- Momentum AMS REST API (NowCerts, `https://api.nowcerts.com`). Prospects via `api/Zapier/InsertProspect`.
  Tag and task endpoints are NOT confirmed yet: isolate them behind `integrations/momentum.py`.

## Planned layout
```
db/migrations/      SQL schema (raw_entities, contacts_enriched, leads_enriched, job_runs)
jobs/               ingest.py, contacts.py, enrich.py, push.py
integrations/       places.py, crawler.py, llm.py, momentum.py
config/             prompt.md, scoring_rubric.md, tags.yaml, thresholds.yaml
tests/fixtures/     saved real API responses (JSON) used by tests
docs/               spec.md, decisions.md
```

## Rules
- Build one job at a time in spec order: schema -> Job 1 -> Job 2 -> Job 3 -> Job 4. Plan first.
- Prompts, scoring rubric, tag names and score thresholds live in `config/`, never hard-coded.
- Tests must not call paid APIs. Use fixtures in `tests/fixtures/`.
- Every job writes a row to `job_runs` (job_name, started_at, finished_at, success_count, error_count).
- External API calls: 3 retries with exponential backoff (per spec).
- Secrets only from environment variables (`.env`, never committed, never read by Claude).
- Never auto-email, auto-text or auto-dial leads. The system only creates CRM records and tasks.
- Ask before adding a new dependency or a new paid service.
- Log important decisions in `docs/decisions.md`.

## Commands
(Fill in as they are created, e.g. `python -m jobs.ingest --since 7d`, `pytest`.)
- Apply migrations, in order:
  `for f in db/migrations/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f" || break; done`

## Open questions (waiting on client)
- Geography for MVP (DFW first?), SOSDirect access, producer assignment rule
- Momentum API user with "API Integration" role; whether tag/task endpoints exist
- Whether custom fields and tags already exist in Momentum
- Azure OpenAI vs OpenAI account
