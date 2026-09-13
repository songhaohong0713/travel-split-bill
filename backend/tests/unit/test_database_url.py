from app.db.session import DEFAULT_DATABASE_URL, resolve_database_url


def test_prefers_explicit_database_url() -> None:
    assert resolve_database_url({"DATABASE_URL": "postgresql+psycopg://explicit"}) == "postgresql+psycopg://explicit"


def test_builds_postgresql_url_from_cloudbase_runtime_variables() -> None:
    environment = {
        "PGHOST": "pg.internal",
        "PGPORT": "5432",
        "PGDATABASE": "travel_split",
        "PGUSER": "app_user",
        "PGPASSWORD": "pass:word",
    }

    assert resolve_database_url(environment) == (
        "postgresql+psycopg://app_user:pass%3Aword@pg.internal:5432/travel_split?sslmode=require"
    )


def test_uses_sqlite_when_cloudbase_runtime_variables_are_incomplete() -> None:
    assert resolve_database_url({"PGHOST": "pg.internal"}) == DEFAULT_DATABASE_URL
