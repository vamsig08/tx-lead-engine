# Client spec (source of truth)

Verbatim from the client. Do not edit without client sign-off. Deviations are listed in CLAUDE.md.

1. Core data model (tables and key fields)
Table: raw_entities
Source: SOS, Comptroller, county/city portals.

id: UUID

business_name: text

owner_name: text (nullable)

address_line1: text

city: text

state: text (TX)

zip: text

naics_code: text (nullable)

source_system: text (sos, comptroller, county, city)

discovered_date: date

created_at / updated_at: timestamps

Table: contacts_enriched
Source: websites, Google Maps, Yelp, BBB.

id: UUID

raw_entity_id: UUID (FK → raw_entities.id)

website_url: text (nullable)

email: text (nullable)

phone: text (nullable)

google_maps_url: text (nullable)

yelp_url: text (nullable)

bbb_url: text (nullable)

status: text (pending, found, not_found)

created_at / updated_at: timestamps

Table: leads_enriched
Source: LLM enrichment.

id: UUID

raw_entity_id: UUID (FK)

contacts_enriched_id: UUID (FK)

industry: enum (medical, re_investor, hotel, contractor, restaurant, other)

needs: text[] (bop, gl, wc, auto, umbrella, prof, inland_marine, liquor)

score: integer (0–10)

rationale: text

outreach_line: text

momentum_prospect_id: text (nullable)

status: text (new, pushed_to_momentum, converted, discarded)

created_at / updated_at: timestamps

2. Jobs and scheduling (n8n / Make.com)
Job 1: Ingest new entities (daily/weekly)
Trigger: Cron (e.g., daily at 3 AM).

Steps:

HTTP Request / Scrapy script:

Pull new records from:

SOS/Comptroller (where accessible via HTML/API/CSV).

County/city open data (CSV/API).

Filter by created/filing_date >= NOW() - interval '7 days'.

Transform:

Normalize fields into raw_entities schema.

Deduplicate by business_name + address.

DB Insert:

Insert into raw_entities.

Job 2: Contact discovery (hourly / batch)
Trigger: Cron or “new raw_entities where no contacts_enriched”.

Steps:

Query DB:

Select raw_entities where no contacts_enriched row exists.

Search step (Playwright / HTTP):

Google: "<business_name>" "<city> TX"

Parse top results for:

Website URL

Google Maps listing

Yelp/BBB links

Scrape contact info:

Visit website contact/about/footer pages.

Extract email + phone via regex + HTML parsing.

DB Insert:

Insert into contacts_enriched.

Job 3: AI enrichment + scoring (batch)
Trigger: New contacts_enriched rows with status = 'found' and no leads_enriched.

Steps:

Query DB:

Join raw_entities + contacts_enriched.

LLM Call (Azure/OpenAI):

Prompt includes:

Business name, address, NAICS (if any).

Website text (scraped separately).

Ask model to:

Classify industry into your 5 ICPs.

List insurance needs.

Assign score 0–10.

Provide rationale + outreach line.

DB Insert:

Insert into leads_enriched.

Job 4: Push to Momentum (API)
Trigger: New leads_enriched with score >= 5 and momentum_prospect_id IS NULL.

Steps:

Prepare payload:

Map fields to Momentum’s prospect/insured creation API:

Name, business, address, email, phone.

Custom fields: NAICS, source, discovered date, AI score.

HTTP Request:

POST to Momentum API: create prospect.

Apply tags:

POST tag application:

Industry: X

Needs: Y

Pipeline: New Business

Trigger: High Score (if score >= 8).

Create task:

POST task:

Assigned user (producer).

Due date = NOW() + 2 days.

Description = rationale + outreach line.

Update DB:

Save momentum_prospect_id.

Set status = 'pushed_to_momentum'.

3. Momentum field and tag mapping
Prospect fields
Name: business_name

Contact name: owner_name (if available)

Address: address_line1, city, state, zip

Email: contacts_enriched.email

Phone: contacts_enriched.phone

Custom fields:

NAICS → naics_code

Source → source_system

Discovered Date → discovered_date

AI Score → score

Tags (string values)
Industry tags:

Industry: Medical

Industry: Real Estate Investor

Industry: Hotel

Industry: Contractor

Industry: Restaurant

Needs tags:

Needs: BOP

Needs: General Liability

Needs: Workers Comp

Needs: Commercial Auto

Needs: Professional Liability

Needs: Umbrella

Needs: Inland Marine

Needs: Liquor Liability

Pipeline/trigger tags:

Pipeline: New Business

Pipeline: Commercial

Trigger: High Score (score ≥ 8)

Trigger: COI Heavy (contractors, hotels, RE investors)

4. Example API payloads (conceptual)
Create prospect (Momentum)
json
{
  "name": "DFW Family Clinic, PLLC",
  "contactName": "Dr. Jane Doe",
  "email": "info@dfwfamilyclinic.com",
  "phone": "817-555-1234",
  "address": {
    "line1": "123 Main St",
    "city": "Arlington",
    "state": "TX",
    "zip": "76010"
  },
  "customFields": {
    "NAICS": "621111",
    "Source": "sos",
    "DiscoveredDate": "2026-10-07",
    "AIScore": 9
  }
}
Apply tags
json
{
  "prospectId": "MOMENTUM_PROSPECT_ID",
  "tags": [
    "Industry: Medical",
    "Needs: BOP",
    "Needs: Professional Liability",
    "Needs: Workers Comp",
    "Pipeline: New Business",
    "Pipeline: Commercial",
    "Trigger: High Score"
  ]
}
Create task
json
{
  "prospectId": "MOMENTUM_PROSPECT_ID",
  "assignedToUserId": "PRODUCER_USER_ID",
  "title": "Call DFW Family Clinic about coverage",
  "dueDate": "2026-10-09",
  "description": "Score 9/10. Medical office in Arlington. Likely needs BOP + Prof + WC.\nSuggested opener: \"We work with medical practices across DFW to simplify coverage and COIs while keeping costs predictable.\""
}
5. Error handling and logging
Retries:

API calls: retry 3 times with backoff.

Scraping: mark status = 'not_found' after failures.

Logging:

Store job runs in a job_runs table with:

job_name, started_at, finished_at, success_count, error_count.

Alerts:

If Momentum API returns repeated errors, send email/Slack to admin.

6. Implementation order (for your dev)
Set up DB + tables (raw_entities, contacts_enriched, leads_enriched).

Build Job 1 (ingest new entities).

Build Job 2 (contact discovery via Playwright/Scrapy).

Build Job 3 (LLM enrichment + scoring).

Integrate Momentum API and build Job 4 (push + tags + tasks).

Configure Momentum workflows based on tags (Trigger: High Score, Trigger: COI Heavy).