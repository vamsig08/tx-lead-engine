import json
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def db():
    """A migrated test database with empty tables. Skipped unless TEST_DATABASE_URL is set.

    Apply the migrations to it first. It is truncated, so the database name must end in _test.
    """
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
    from jobs.common import connect

    conn = connect(dsn)
    name = conn.execute("SELECT current_database()").fetchone()[0]
    if not name.endswith("_test"):
        pytest.fail(f"refusing to truncate {name!r}: test database name must end in _test")
    conn.execute(
        "TRUNCATE raw_entities, contacts_enriched, leads_enriched, job_runs CASCADE"
    )
    yield dsn, conn
    conn.close()
