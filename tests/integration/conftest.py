"""Fixtures für Integrationstests: echtes Postgres (pgvector) via Testcontainers.

Image ist digest-gepinnt (Supply-Chain, R-SEC). Diese Fixtures starten einen
Container — daher nur unter dem `integration`-Marker (AC5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from wortlaut.store.worm import MinioWormStore

# ADR-0006: digest-gepinnt (repository@sha256, ohne Tag — sonst pullt docker-py nicht).
PG_IMAGE = (
    "pgvector/pgvector@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"
)


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(PG_IMAGE, driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture
def fresh_pg_dsn() -> Iterator[str]:
    """Ein eigener, frischer Container je Test — für schema-mutierende Tests
    (z.B. Downgrade, AC9 #2), damit sie die session-geteilte DB nicht stören."""
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


# ADR-0006: digest-gepinnt (repository@sha256, ohne Tag — sonst pullt docker-py nicht).
# Unveraenderte Kopie von minio/minio:RELEASE.2025-09-07T16-13-09Z.
# Upstream ist nicht mehr abrufbar (#119).
MINIO_IMAGE = (
    "ghcr.io/mkrww/minio@sha256:a16cad481969d7ddb6fcd2f1c58284af3d1266190b9bedc3ef0801aeb05a93a9"
)


@pytest.fixture
async def worm_store() -> AsyncIterator[MinioWormStore]:
    """Echter MinIO-Container mit Object-Lock für WORM-Integrationstests."""
    from testcontainers.minio import MinioContainer

    from wortlaut.store.settings import WormSettings
    from wortlaut.store.worm import MinioWormStore

    with MinioContainer(MINIO_IMAGE) as c:
        config = c.get_config()
        settings = WormSettings(
            endpoint=config["endpoint"],
            access_key=config["access_key"],
            secret_key=config["secret_key"],
            bucket="wortlaut-worm",
            secure=False,
        )
        store = MinioWormStore(settings)
        await store.ensure_bucket()
        yield store


_ATTEST_SQL = text(
    "INSERT INTO source_archive "
    "(source_id, archiver, snapshot_url, snapshot_at, verified_sha256) "
    "SELECT id, 'wayback', 'https://web.archive.org/web/20260101000000/' || origin_url, "
    "now(), content_hash FROM source WHERE id = CAST(:sid AS uuid)"
)


@pytest.fixture
def seed_attestation() -> Callable[[AsyncSession | AsyncConnection, UUID | str], Awaitable[None]]:
    """Testdaten: trägt für eine Quelle eine gültige Attestierung ein (Hash = content_hash)."""

    async def _seed(executor: AsyncSession | AsyncConnection, source_id: UUID | str) -> None:
        await executor.execute(_ATTEST_SQL, {"sid": str(source_id)})

    return _seed
