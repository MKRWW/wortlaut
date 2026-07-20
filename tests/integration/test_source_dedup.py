"""Integration: source_exists Dedup-Check (AC5–AC6).

Echtes Postgres (pgvector) via Testcontainers. Nutzt die Fixtures aus conftest.py.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from wortlaut.store.migrations import upgrade_head
from wortlaut.store.sources import source_exists

pytestmark = pytest.mark.integration


async def test_source_exists_false_for_unknown(
    pg_dsn: str,
    db_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # AC5: frische DB, unbekannter Hash -> False
    await upgrade_head(pg_dsn)

    async with sessions() as session:
        unknown_hash = "0" * 64
        assert (await source_exists(session, unknown_hash)) is False


async def test_source_exists_true_after_insert(
    pg_dsn: str,
    db_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # AC6: Source eingefuegt, gleicher Hash -> True (Duplikat erkannt)
    await upgrade_head(pg_dsn)

    content_hash = "b" * 64

    async with sessions() as session:
        # Adapter + Source per raw SQL einfuegen (Transaktions-Rollback-Isolation)
        # IMMER CAST(:x AS typ) — NIEMALS :x::typ (R-SQL-01)
        await session.execute(
            text(
                "INSERT INTO ingest_adapter (name, version, trust_level, description, created_at) "
                "VALUES (:name, :version, CAST(:trust_level AS trust_level), "
                ":description, CAST(:created_at AS timestamptz))"
            ),
            {
                "name": "test-adapter",
                "version": "1.0.0",
                "trust_level": "verified_primary",
                "description": "Test-Adapter",
                "created_at": datetime.now(UTC),
            },
        )

        await session.execute(
            text(
                "INSERT INTO source (source_type, rights_basis, adapter_name, adapter_version, "
                "origin_url, content_hash, byte_size, mime_type, retrieved_at, raw_bytes_ref, "
                "archive_wayback) "
                "VALUES (CAST(:source_type AS source_type), CAST(:rights_basis AS rights_basis), "
                ":adapter_name, :adapter_version, :origin_url, :content_hash, :byte_size, "
                ":mime_type, CAST(:retrieved_at AS timestamptz), :raw_bytes_ref, :archive_wayback)"
            ),
            {
                "source_type": "rede",
                "rights_basis": "amtliches_werk_p5",
                "adapter_name": "test-adapter",
                "adapter_version": "1.0.0",
                "origin_url": "https://example.com/source/1",
                "content_hash": content_hash,
                "byte_size": 1024,
                "mime_type": "text/plain",
                "retrieved_at": datetime.now(UTC),
                "raw_bytes_ref": "worm://test/raw.bin",
                "archive_wayback": "https://web.archive.org/test",
            },
        )

        # Derselben Session/Verbindung: Dedup muss True liefern
        assert (await source_exists(session, content_hash)) is True

    # Rollback durch Contextmanager-Exit -> DB bleibt sauber
