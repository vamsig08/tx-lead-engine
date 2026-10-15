from datetime import date

import pytest
from pydantic import ValidationError

from jobs import ingest
from jobs.common import load_config
from tests.conftest import load_fixture

SOURCES = load_config("sources.yaml")["sources"]
SALES_TAX = SOURCES["sales_tax"]
FRANCHISE = SOURCES["franchise_tax"]


class FakeSocrata:
    """Serves fixture rows per dataset, or raises for datasets in `fail`."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.wheres = {}

    def query(self, dataset_id, where):
        self.wheres[dataset_id] = where
        if dataset_id in self.fail:
            raise RuntimeError("socrata down")
        name = {"jrea-zgmq": "sales_tax", "9cir-efmm": "franchise_tax"}[dataset_id]
        return iter(load_fixture(f"socrata_{name}.json"))

    def close(self):
        pass


def test_parse_since():
    today = date(2026, 10, 14)
    assert ingest.parse_since("7d", today) == date(2026, 10, 7)
    assert ingest.parse_since("2026-09-01", today) == date(2026, 9, 1)
    with pytest.raises(ValueError):
        ingest.parse_since("last week", today)


def test_build_where_matches_padded_and_unpadded_county_codes():
    where = ingest.build_where(FRANCHISE, date(2026, 10, 1), ["057", "220"])
    assert where == (
        "sos_charter_date >= '2026-10-01T00:00:00'"
        " AND taxpayer_county_code IN ('057', '220', '57')"
    )


def test_normalize_sales_tax_uses_outlet_address_and_name():
    row = load_fixture("socrata_sales_tax.json")[3]
    entity = ingest.normalize(row, SALES_TAX)
    assert entity.business_name == "GREAT CLIPS AT POINT WEST"
    assert entity.address_line1 == "1575 S BELT LINE RD # 120"
    assert entity.city == row["outlet_city"]
    assert entity.zip == row["outlet_zip_code"]
    assert entity.naics_code == "812111"
    assert entity.source_system == "comptroller"
    assert entity.source_record_id == "17528995503-3"
    assert entity.filing_date == date.fromisoformat(row["outlet_permit_issue_date"][:10])
    assert entity.raw_payload == row


def test_normalize_sales_tax_falls_back_to_taxpayer_name():
    row = dict(load_fixture("socrata_sales_tax.json")[0], outlet_name="  ")
    assert ingest.normalize(row, SALES_TAX).business_name == "HALLIN SC INVESTMENTS LLC"


def test_normalize_franchise():
    with_naics, _, without_naics, _ = load_fixture("socrata_franchise_tax.json")
    entity = ingest.normalize(with_naics, FRANCHISE)
    assert entity.business_name == "LISTINGWITHLYONS LLC"
    assert entity.source_system == "sos"
    assert entity.source_record_id == "0806701768"
    assert entity.naics_code == "531210"
    assert ingest.normalize(without_naics, FRANCHISE).naics_code is None


def test_normalize_collapses_whitespace():
    row = dict(load_fixture("socrata_sales_tax.json")[0], outlet_address=" 12  MAIN\tST ")
    assert ingest.normalize(row, SALES_TAX).address_line1 == "12 MAIN ST"


def test_normalize_rejects_missing_address():
    row = dict(load_fixture("socrata_sales_tax.json")[0])
    del row["outlet_address"]
    with pytest.raises(ValidationError):
        ingest.normalize(row, SALES_TAX)


def test_dry_run_writes_nothing():
    fake = FakeSocrata()
    assert ingest.run(date(2026, 10, 1), dry_run=True, socrata=fake)
    assert set(fake.wheres) == {"jrea-zgmq", "9cir-efmm"}


def test_default_cutoff_uses_each_sources_lookback():
    fake = FakeSocrata()
    ingest.run(dry_run=True, socrata=fake)
    today = date.today()
    for source in (SALES_TAX, FRANCHISE):
        expected = date.fromordinal(today.toordinal() - source["lookback_days"])
        assert fake.wheres[source["dataset_id"]].startswith(
            f"{source['date_field']} >= '{expected.isoformat()}T00:00:00'"
        )


def test_run_inserts_dedupes_and_records_job_run(db):
    dsn, conn = db
    assert ingest.run(date(2026, 9, 1), dsn=dsn, socrata=FakeSocrata())
    assert ingest.run(date(2026, 9, 1), dsn=dsn, socrata=FakeSocrata())

    counts = dict(conn.execute(
        "SELECT source_system, count(*) FROM raw_entities GROUP BY 1").fetchall())
    assert counts == {"comptroller": 4, "sos": 4}

    runs = conn.execute(
        "SELECT status, success_count, error_count, details, finished_at IS NOT NULL"
        " FROM job_runs ORDER BY started_at").fetchall()
    assert [r[:3] for r in runs] == [("succeeded", 8, 0), ("succeeded", 0, 0)]
    assert runs[1][3]["sources"]["sales_tax"]["duplicates"] == 4
    assert all(r[4] for r in runs)


def test_run_dedupes_same_business_across_sources(db):
    dsn, conn = db
    row = load_fixture("socrata_sales_tax.json")[0]
    other = dict(row, taxpayer_number="999", outlet_name=row["outlet_name"].lower() + ".")

    class TwoRows(FakeSocrata):
        def query(self, dataset_id, where):
            return iter([row, other]) if dataset_id == "jrea-zgmq" else iter([])

    assert ingest.run(date(2026, 9, 1), dsn=dsn, socrata=TwoRows())
    assert conn.execute("SELECT count(*) FROM raw_entities").fetchone()[0] == 1


def test_source_failure_fails_run_but_other_sources_still_ingest(db):
    dsn, conn = db
    ok = ingest.run(date(2026, 9, 1), dsn=dsn, socrata=FakeSocrata(fail={"9cir-efmm"}))
    assert not ok
    assert conn.execute("SELECT count(*) FROM raw_entities").fetchone()[0] == 4
    status, errors, details = conn.execute(
        "SELECT status, error_count, details FROM job_runs").fetchone()
    assert (status, errors) == ("failed", 1)
    assert details["sources"]["franchise_tax"] == {"failed": True}


def test_invalid_rows_count_as_errors(db):
    dsn, conn = db
    bad = dict(load_fixture("socrata_sales_tax.json")[0], outlet_zip_code=None)

    class OneBad(FakeSocrata):
        def query(self, dataset_id, where):
            return iter([bad]) if dataset_id == "jrea-zgmq" else iter([])

    assert ingest.run(date(2026, 9, 1), dsn=dsn, socrata=OneBad())
    status, errors, details = conn.execute(
        "SELECT status, error_count, details FROM job_runs").fetchone()
    assert (status, errors) == ("succeeded", 1)
    assert "zip" in details["errors"][0]
