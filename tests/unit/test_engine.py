"""Unit: Engine-Factory baut eine AsyncEngine mit erwarteter URL (ohne Verbindung)."""

from sqlalchemy.ext.asyncio import AsyncEngine

from wortlaut.store.db import create_async_engine_from
from wortlaut.store.settings import DbSettings


def test_engine_factory_builds_expected_url() -> None:
    settings = DbSettings(dsn="postgresql+asyncpg://u:p@h:5432/db")
    engine = create_async_engine_from(settings)
    assert isinstance(engine, AsyncEngine)
    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.database == "db"
