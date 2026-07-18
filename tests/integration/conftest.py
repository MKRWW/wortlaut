"""Fixtures für Integrationstests: echtes Postgres (pgvector) via Testcontainers.

Image ist digest-gepinnt (Supply-Chain, R-SEC). Diese Fixtures starten einen
Container — daher nur unter dem `integration`-Marker (AC5).
"""

from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

# ADR-0006: digest-gepinnt.
PG_IMAGE = (
    "pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"
)


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(PG_IMAGE, driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture
async def db_engine(pg_dsn: str) -> AsyncIterator[AsyncEngine]:
    from wortlaut.store.db import create_async_engine_from
    from wortlaut.store.settings import DbSettings

    engine = create_async_engine_from(DbSettings(dsn=pg_dsn))
    yield engine
    await engine.dispose()


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    from wortlaut.store.db import make_sessionmaker

    return make_sessionmaker(db_engine)
