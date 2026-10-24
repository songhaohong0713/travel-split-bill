import json
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


def test_incremental_migration_preserves_old_records_and_backfills_owner(tmp_path):
    path = tmp_path / "legacy.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite+pysqlite:///{path}"}

    def upgrade(target):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "backend/alembic.ini",
                "upgrade",
                target,
            ],
            env=environment,
            capture_output=True,
            check=False,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    upgrade("20261021_06")
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO users(id) VALUES('a')")
        connection.execute(
            "INSERT INTO trips(id,owner_id,name,default_currency) VALUES('old','a','legacy','CNY')"
        )
        connection.execute(
            "INSERT INTO expenses(id,trip_id,owner_id,revision,occurred_at,payload_json) VALUES('e','old','a',1,'2026-10-07','{}')"
        )
    upgrade("head")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT user_id FROM trip_members").fetchall() == [
            ("a",)
        ]
        assert (
            connection.execute("SELECT payload_json FROM expenses").fetchone()[0]
            == "{}"
        )
        state = json.loads(
            connection.execute("SELECT state_json FROM trip_ledgers").fetchone()[0]
        )
        assert state["history_confirmation"]["status"] == "pending"
        assert state["payments"] == []
