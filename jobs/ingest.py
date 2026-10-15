"""Job 1: ingest newly registered Texas businesses into raw_entities.

    python -m jobs.ingest [--since 14d|YYYY-MM-DD] [--source sales_tax] [--dry-run]

Sources, per-source lookback and the county filter live in config/sources.yaml.
--since overrides every source's lookback.
"""
import argparse
import logging
import re
import sys
from datetime import date, timedelta
from typing import Annotated

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, StringConstraints, ValidationError

from integrations.socrata import SocrataClient
from jobs.common import JobRun, connect, job_run, load_config

log = logging.getLogger("jobs.ingest")

NonEmpty = Annotated[str, StringConstraints(min_length=1)]


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
                              raw_payload)
    VALUES (%(business_name)s, %(owner_name)s, %(address_line1)s, %(city)s, %(state)s,
            %(zip)s, %(naics_code)s, %(source_system)s, %(filing_date)s,
            %(source_record_id)s, %(raw_payload)s)
    ON CONFLICT DO NOTHING
    RETURNING id
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


def ingest_source(name: str, source: dict, rows, conn: psycopg.Connection | None,
                  run: JobRun | None) -> dict:
    """Normalise and insert one source's rows. conn=None is a dry run."""
    counts = {"fetched": 0, "inserted": 0, "duplicates": 0, "invalid": 0}
    for row in rows:
        counts["fetched"] += 1
        try:
            entity = normalize(row, source)
        except ValidationError as exc:
            counts["invalid"] += 1
            missing = ", ".join(str(e["loc"][0]) for e in exc.errors())
            if run:
                run.error(f"{name}: invalid row {row.get(source['record_id_fields'][0])}: {missing}")
            continue
        if conn is None:
            continue
        params = entity.model_dump()
        params["raw_payload"] = Jsonb(params["raw_payload"])
        if conn.execute(INSERT_SQL, params).fetchone():
            counts["inserted"] += 1
            run.success_count += 1
        else:
            counts["duplicates"] += 1
    return counts


def run(since: date | None = None, only_source: str | None = None, dry_run: bool = False,
        dsn: str | None = None, socrata: SocrataClient | None = None) -> bool:
    """Run Job 1. Returns False if any source failed."""
    config = load_config("sources.yaml")
    counties = list(config["counties"])
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
                    name, source, socrata.query(source["dataset_id"], where), conn, job
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    since = parse_since(args.since, date.today()) if args.since else None
    ok = run(since, args.source, args.dry_run)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
