"""Async-DB-Fundament: ORM-Basis, Engine-Factory, Sessionmaker.

Wichtig (ADR-0003 rev.): Immutability wird über DB-Trigger (Migrationen) erzwungen,
nicht über das ORM. Dieses Modul liefert nur den async Zugriff.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from wortlaut.store.settings import DbSettings


class Base(DeclarativeBase):
    """Deklarative Basis für alle ORM-Modelle."""


def create_async_engine_from(settings: DbSettings) -> AsyncEngine:
    """Baut eine async Engine aus dem DSN (keine Verbindung wird geöffnet)."""
    return create_async_engine(settings.dsn)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessionmaker; ``expire_on_commit=False`` für stabile Objekte nach Commit."""
    return async_sessionmaker(engine, expire_on_commit=False)
