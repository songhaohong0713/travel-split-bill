"""A throwaway PostgreSQL database for tests that run the deployed SQL itself.

Used only when TSB_TEST_POSTGRES_URL points at a server where a scratch
database may be created and dropped, e.g. postgresql://postgres@127.0.0.1:5432.
"""

import os
import subprocess
from contextlib import contextmanager
from uuid import uuid4

import pytest

SERVER = os.environ.get("TSB_TEST_POSTGRES_URL")
requires_postgres = pytest.mark.skipif(
    not SERVER, reason="TSB_TEST_POSTGRES_URL not set"
)
ROLES = """
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
    CREATE ROLE service_role; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
    CREATE ROLE anon; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
    CREATE ROLE authenticated; END IF;
END $$;
"""


@contextmanager
def scratch_database(setup):
    """Yield run(sql, check=True) bound to a fresh database prepared by `setup`."""
    name = "tsb_" + uuid4().hex

    def run(sql, database=name, check=True):
        result = subprocess.run(
            ["psql", f"{SERVER}/{database}", "-v", "ON_ERROR_STOP=1", "-Atq"],
            input=sql,
            capture_output=True,
            text=True,
            check=False,
        )
        if check:
            assert result.returncode == 0, result.stderr
        return result

    run.url = f"{SERVER}/{name}"
    run(f"CREATE DATABASE {name}", "postgres")
    try:
        run(ROLES + setup)
        yield run
    finally:
        run(f"DROP DATABASE {name} WITH (FORCE)", "postgres")
