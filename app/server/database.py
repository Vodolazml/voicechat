from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class Base(DeclarativeBase):
    pass


def _connect_args() -> dict[str, object]:
    return {"check_same_thread": False} if get_settings().database_url.startswith("sqlite") else {}


engine = create_engine(get_settings().database_url, connect_args=_connect_args(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def alembic_config(connection=None):
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def run_migrations(target: Engine) -> None:
    """Bring the schema to the latest version. Safe to run on every start."""
    from alembic import command

    from . import models  # noqa: F401  (register tables on Base.metadata)

    with target.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        config = alembic_config(connection)
        if "users" in tables and "alembic_version" not in tables:
            raise RuntimeError(
                "Database was created by a pre-migration server version. "
                "Start from a fresh database (see docs/DEPLOY.md)."
            )
        command.upgrade(config, "head")
