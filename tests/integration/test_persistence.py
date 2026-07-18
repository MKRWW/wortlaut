"""Integrationstests (AC3–AC4) gegen echtes Postgres+pgvector.

Beweist die Fundament-Invarianten: async-Verbindung, Migration aktiviert pgvector,
Vektor-Roundtrip über das ORM.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

pytestmark = pytest.mark.integration


async def test_session_select_one(sessions: async_sessionmaker[AsyncSession]) -> None:
    # AC3: async Engine/Session verbindet und liefert SELECT 1 == 1.
    async with sessions() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1


async def test_alembic_upgrade_head_enables_pgvector(pg_dsn: str, db_engine: AsyncEngine) -> None:
    # AC4a: `alembic upgrade head` läuft fehlerfrei und aktiviert die vector-Extension.
    from wortlaut.store.migrations import upgrade_head

    await upgrade_head(pg_dsn)
    async with db_engine.connect() as conn:
        count = await conn.scalar(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
        )
    assert count == 1


async def test_orm_vector_roundtrip(
    pg_dsn: str,
    db_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # AC4b: ORM-Modell mit Vector(3) schreibt [1,2,3] und liest es identisch.
    from pgvector.sqlalchemy import Vector
    from sqlalchemy import Integer
    from sqlalchemy.orm import Mapped, mapped_column

    from wortlaut.store.db import Base
    from wortlaut.store.migrations import upgrade_head

    await upgrade_head(pg_dsn)  # aktiviert pgvector

    class VectorProbe(Base):
        __tablename__ = "vector_probe"
        __table_args__ = {"extend_existing": True}
        id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
        embedding: Mapped[list[float]] = mapped_column(Vector(3))

    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sessions() as session:
        session.add(VectorProbe(embedding=[1.0, 2.0, 3.0]))
        await session.commit()

    async with sessions() as session:
        probe = (await session.scalars(select(VectorProbe))).one()
        assert list(probe.embedding) == [1.0, 2.0, 3.0]
