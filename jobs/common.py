"""Shared by all jobs: config loading, DB connection, job_runs bookkeeping."""
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
import yaml
from psycopg.types.json import Jsonb

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
# Keeps job_runs.details small when a source returns many bad rows.
MAX_ERRORS_IN_DETAILS = 50


def load_config(name: str) -> dict:
    with open(CONFIG_DIR / name) as f:
        return yaml.safe_load(f)


def connect(dsn: str | None = None) -> psycopg.Connection:
    return psycopg.connect(dsn or os.environ["DATABASE_URL"], autocommit=True)


class JobRun:
    """A job_runs row. Counts and details are written when the run finishes."""

    def __init__(self, conn: psycopg.Connection, job_name: str):
        self.conn = conn
        self.success_count = 0
        self.error_count = 0
        self.failed = False
        self.details: dict = {"errors": []}
        self.id = conn.execute(
            "INSERT INTO job_runs (job_name) VALUES (%s) RETURNING id", (job_name,)
        ).fetchone()[0]

    def error(self, message: str) -> None:
        self.error_count += 1
        if len(self.details["errors"]) < MAX_ERRORS_IN_DETAILS:
            self.details["errors"].append(message)

    def finish(self, status: str) -> None:
        self.conn.execute(
            """UPDATE job_runs
               SET finished_at = now(), status = %s, success_count = %s,
                   error_count = %s, details = %s
               WHERE id = %s""",
            (status, self.success_count, self.error_count, Jsonb(self.details), self.id),
        )


@contextmanager
def job_run(conn: psycopg.Connection, job_name: str):
    """Record a run in job_runs. It fails if the body raises or sets run.failed."""
    run = JobRun(conn, job_name)
    try:
        yield run
    except BaseException as exc:
        run.error(f"run aborted: {exc!r}")
        run.finish("failed")
        raise
    run.finish("failed" if run.failed else "succeeded")
