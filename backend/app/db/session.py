import os
from collections.abc import Generator, Mapping
from urllib.parse import quote

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite+pysqlite:///:memory:"


def resolve_database_url(environment: Mapping[str, str] | None = None) -> str:
    values = os.environ if environment is None else environment
    explicit_url = values.get("DATABASE_URL")
    if explicit_url:
        return explicit_url

    required_names = ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD")
    if not all(values.get(name) for name in required_names):
        return DEFAULT_DATABASE_URL

    host = values["PGHOST"]
    port = values.get("PGPORT", "5432")
    database = quote(values["PGDATABASE"], safe="")
    user = quote(values["PGUSER"], safe="")
    password = quote(values["PGPASSWORD"], safe="")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}?sslmode=require"


DATABASE_URL = resolve_database_url()

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, class_=Session
)


def configure_database(database_url: str) -> None:
    global engine
    engine = create_engine(database_url)
    SessionLocal.configure(bind=engine)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def create_schema() -> None:
    from app.db.models import Base

    Base.metadata.create_all(bind=engine)


def drop_schema() -> None:
    from app.db.models import Base

    Base.metadata.drop_all(bind=engine)