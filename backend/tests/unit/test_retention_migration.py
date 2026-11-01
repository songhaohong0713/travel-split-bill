import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def test_populated_local_database_gets_retention_timestamp_without_losing_rows():
    path = Path("backend/alembic/versions/20261101_11_calendar_orphans.py")
    spec = importlib.util.spec_from_file_location("retention_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE trips (id text PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE receipt_images (id text PRIMARY KEY, object_key text UNIQUE NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE receipt_jobs (id text PRIMARY KEY,image_id text REFERENCES receipt_images(id))"
            )
        )
        connection.execute(
            text("INSERT INTO receipt_images VALUES ('old','original.jpg')")
        )
        connection.execute(text("INSERT INTO receipt_jobs VALUES ('job','old')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.execute(
            text(
                "SELECT object_key,created_at IS NOT NULL FROM receipt_images WHERE id='old'"
            )
        ).one() == ("original.jpg", 1)
        assert (
            connection.execute(text("SELECT image_id FROM receipt_jobs")).scalar_one()
            == "old"
        )
        connection.execute(
            text("INSERT INTO receipt_images(id,object_key) VALUES ('new','new.jpg')")
        )
        assert (
            connection.execute(
                text("SELECT created_at IS NOT NULL FROM receipt_images WHERE id='new'")
            ).scalar_one()
            == 1
        )
