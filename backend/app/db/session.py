import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite+pysqlite:///:memory:"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)

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
