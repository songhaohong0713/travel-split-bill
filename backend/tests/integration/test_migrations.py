import os
import sqlite3
import subprocess
import sys


def test_alembic_upgrade_creates_owner_scoped_tables(tmp_path) -> None:
    database_path = tmp_path / "migration.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite+pysqlite:///{database_path}"}

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "backend/alembic.ini",
            "upgrade",
            "head",
        ],
        check=False,
        capture_output=True,
        cwd=".",
        env=environment,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    tables = {
        row[0]
        for row in sqlite3.connect(database_path).execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "users",
        "refresh_tokens",
        "trips",
        "expenses",
        "idempotency_keys",
    } <= tables
