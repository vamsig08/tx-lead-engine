"""Job 1: ingest newly registered Texas businesses into raw_entities.

    python -m jobs.ingest [--since 14d|YYYY-MM-DD] [--source sales_tax] [--dry-run]

Sources, per-source lookback and the county filter live in config/sources.yaml.
--since overrides every source's lookback. Each row is also run through the ICP
pre-filter (config/icp_filter.yaml); rows evaluated under an older rule version are
re-evaluated at the end of every run.
"""
import argparse
import logging
import re
import sys
from datetime import date, timedelta
from typing import Annotated

import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv
from pydantic import BaseModel, StringConstraints, ValidationError

from integrations.socrata import SocrataClient
from jobs.common import JobRun, connect, job_run, load_config
from jobs.icp_filter import IcpFilter

log = logging.getLogger("jobs.ingest")

NonEmpty = Annotated[str, StringConstraints(min_length=1)]
# Rows per executemany round trip.
BATCH_SIZE = 500


class RawEntity(BaseModel):
    business_name: NonEmpty
    owner_name: str | None = None
    address_line1: NonEmpty
    city: NonEmpty
    state: NonEmpty
    zip: NonEmpty
    naics_code: str | None
    source_system: str
    filing_date: date | None
    source_record_id: str | None
    raw_payload: dict


INSERT_SQL = """
    INSERT INTO raw_entities (business_name, owner_name, address_line1, city, state, zip,
                              naics_code, source_system, filing_date, source_record_id,
                              raw_payload, icp_candidate, icp_industry, icp_reason,
                              icp_rule_version)
    VALUES (%(business_name)s, %(owner_name)s, %(address_line1)s, %(city)s, %(state)s,
            %(zip)s, %(naics_code)s, %(source_system)s, %(filing_date)s,
            %(source_record_id)s, %(raw_payload)s, %(icp_candidate)s, %(icp_industry)s,
            %(icp_reason)s, %(icp_rule_version)s)
    ON CONFLICT DO NOTHING
    RETURNING id
"""

FLAG_SHARED_ADDRESSES_SQL = """
    UPDATE raw_entities r
    SET shared_address = s.n >= %(min)s
    FROM (SELECT address_key, count(*) AS n FROM raw_entities GROUP BY address_key) s
    WHERE r.address_key = s.address_key AND r.shared_address <> (s.n >= %(min)s)
"""

STALE_ICP_SQL = """
    SELECT id, business_name, naics_code, source_system FROM raw_entities
    WHERE icp_rule_version IS DISTINCT FROM %s
"""

UPDATE_ICP_SQL = """
    UPDATE raw_entities
    SET icp_candidate = %(icp_candidate)s, icp_industry = %(icp_industry)s,
        icp_reason = %(icp_reason)s, icp_rule_version = %(icp_rule_version)s
    WHERE id = %(id)s
"""


def parse_since(value: str, today: date) -> date:
    """'7d' means 7 days before today; otherwise an ISO date."""
    if m := re.fullmatch(r"(\d+)d", value):
        return today - timedelta(days=int(m[1]))
    return date.fromisoformat(value)


def build_where(source: dict, since: date, counties: list[str]) -> str:
    # One dataset zero-pads county codes ("057"), the other does not ("57").
    codes = sorted({form for c in counties for form in (c, c.lstrip("0") or "0")})
    code_list = ", ".join(f"'{c}'" for c in codes)
    return (
        f"{source['date_field']} >= '{since.isoformat()}T00:00:00'"
        f" AND {source['county_field']} IN ({code_list})"
    )


def _clean(value) -> str | None:
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value or None


def normalize(row: dict, source: dict) -> RawEntity:
    """Map a source row onto raw_entities columns. Raises ValidationError."""
    fields = source["fields"]
    name = next(
        (n for f in fields["business_name"] if (n := _clean(row.get(f)))), None
    )
    record_id_parts = [_clean(row.get(f)) for f in source["record_id_fields"]]
    filing = _clean(row.get(source["date_field"]))
    return RawEntity(
        business_name=name or "",
        address_line1=_clean(row.get(fields["address_line1"])) or "",
        city=_clean(row.get(fields["city"])) or "",
        state=_clean(row.get(fields["state"])) or "TX",
        zip=_clean(row.get(fields["zip"])) or "",
        naics_code=_clean(row.get(fields["naics_code"])),
        source_system=source["source_system"],
        filing_date=date.fromisoformat(filing[:10]) if filing else None,
        source_record_id="-".join(record_id_parts) if all(record_id_parts) else None,
        raw_payload=row,
    )


def icp_params(icp: IcpFilter, business_name: str, naics_code: str | None,
               source_system: str) -> dict:
    result = icp.classify(business_name, naics_code, source_system)
    return {
        "icp_candidate": result.candidate,
        "icp_industry": result.industry,
        "icp_reason": result.reason,
        "icp_rule_version": icp.version,
    }


def _insert_batch(conn: psycopg.Connection, batch: list[dict], counts: dict,
                  run: JobRun) -> None:
    with conn.cursor() as cur:
        cur.executemany(INSERT_SQL, batch, returning=True)
        for params in batch:
            if cur.fetchone():
                counts["inserted"] += 1
                run.success_count += 1
                if params["icp_candidate"]:
                    counts["inserted_icp_candidates"] += 1
            else:
                counts["duplicates"] += 1
            cur.nextset()
    batch.clear()


def ingest_source(name: str, source: dict, rows, conn: psycopg.Connection | None,
                  run: JobRun | None, icp: IcpFilter,
                  exclude_org_types: frozenset[str] = frozenset()) -> dict:
    """Normalise, pre-filter and insert one source's rows. conn=None is a dry run."""
    counts = {"fetched": 0, "excluded": 0, "invalid": 0, "icp_candidates": 0,
              "inserted": 0, "inserted_icp_candidates": 0, "duplicates": 0}
    batch: list[dict] = []
    for row in rows:
        counts["fetched"] += 1
        if _clean(row.get(source["org_type_field"])) in exclude_org_types:
            counts["excluded"] += 1
            continue
        try:
            entity = normalize(row, source)
        except ValidationError as exc:
            counts["invalid"] += 1
            missing = ", ".join(str(e["loc"][0]) for e in exc.errors())
            if run:
                run.error(f"{name}: invalid row {row.get(source['record_id_fields'][0])}: {missing}")
            continue
        params = entity.model_dump()
        params.update(icp_params(icp, entity.business_name, entity.naics_code,
                                 entity.source_system))
        if params["icp_candidate"]:
            counts["icp_candidates"] += 1
        if conn is None:
            continue
        params["raw_payload"] = Jsonb(params["raw_payload"])
        batch.append(params)
        if len(batch) >= BATCH_SIZE:
            _insert_batch(conn, batch, counts, run)
    if batch:
        _insert_batch(conn, batch, counts, run)
    return counts


def reclassify_stale(conn: psycopg.Connection, icp: IcpFilter) -> int:
    """Re-run the pre-filter on rows evaluated under another rule version (or never)."""
    rows = conn.execute(STALE_ICP_SQL, (icp.version,)).fetchall()
    updates = [
        {"id": row_id, **icp_params(icp, name, naics, source_system)}
        for row_id, name, naics, source_system in rows
    ]
    if updates:
        with conn.cursor() as cur:
            cur.executemany(UPDATE_ICP_SQL, updates)
    return len(updates)


def run(since: date | None = None, only_source: str | None = None, dry_run: bool = False,
        dsn: str | None = None, socrata: SocrataClient | None = None) -> bool:
    """Run Job 1. Returns False if any source failed."""
    config = load_config("sources.yaml")
    counties = list(config["counties"])
    exclude_org_types = frozenset(config.get("exclude_org_types", []))
    icp = IcpFilter(load_config("icp_filter.yaml"))
    sources = {
        name: s for name, s in config["sources"].items()
        if s["enabled"] and (only_source is None or name == only_source)
    }
    if not sources:
        raise SystemExit(f"no enabled source named {only_source!r}")

    sc = config["socrata"]
    socrata = socrata or SocrataClient(
        sc["base_url"], page_size=sc["page_size"], timeout=sc["timeout_seconds"]
    )

    def process(conn, job):
        ok = True
        for name, source in sources.items():
            cutoff = since or date.today() - timedelta(days=source["lookback_days"])
            where = build_where(source, cutoff, counties)
            log.info("%s: querying %s where %s", name, source["dataset_id"], where)
            try:
                counts = ingest_source(
                    name, source, socrata.query(source["dataset_id"], where), conn, job,
                    icp, exclude_org_types,
                )
            except Exception as exc:
                ok = False
                log.exception("%s: source failed", name)
                if job:
                    job.error(f"{name}: source failed: {exc!r}")
                    job.details.setdefault("sources", {})[name] = {"failed": True}
                continue
            log.info("%s: %s", name, counts)
            if job:
                job.details.setdefault("sources", {})[name] = {**counts, "since": cutoff.isoformat()}
        return ok

    try:
        if dry_run:
            return process(None, None)
        with connect(dsn) as conn, job_run(conn, "ingest") as job:
            if since:
                job.details["since"] = since.isoformat()
            ok = process(conn, job)
            min_entities = load_config("thresholds.yaml")["shared_address_min_entities"]
            flagged = conn.execute(FLAG_SHARED_ADDRESSES_SQL, {"min": min_entities}).rowcount
            job.details["shared_address_flags_changed"] = flagged
            job.details["icp_rule_version"] = icp.version
            job.details["icp_reclassified"] = reclassify_stale(conn, icp)
            job.failed = not ok
            return ok
    finally:
        socrata.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since",
                        help="filing-date cutoff for every source: Nd (days ago) or YYYY-MM-DD "
                             "(default: each source's lookback_days)")
    parser.add_argument("--source", help="run only this source from config/sources.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and validate only; no database writes")
    args = parser.parse_args(argv)
    # Scheduled runs (cron/n8n) don't source .env, so load it here. Real env vars win.
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    since = parse_since(args.since, date.today()) if args.since else None
    ok = run(since, args.source, args.dry_run)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
