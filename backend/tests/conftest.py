from collections.abc import Generator

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session(tmp_path):
    from app.db.session import (
        SessionLocal,
        configure_database,
        create_schema,
        drop_schema,
    )

    configure_database(f"sqlite+pysqlite:///{tmp_path / 'unit.db'}")
    create_schema()
    value = SessionLocal()
    yield value
    value.close()
    drop_schema()
