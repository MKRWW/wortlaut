"""Integration (#8, AC5): verify_source gegen echtes Postgres + MinIO.

Legt eine source mit WORM-Objekt an und rechnet die Integrität nach.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from wortlaut.evidence.hashing import content_hash
from wortlaut.pipeline.verify import verify_source
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.sources import NewSource, insert_source
from wortlaut.store.worm import WormStore

pytestmark = pytest.mark.integration


async def test_verify_ok_against_real_pg_minio(
    pg_dsn: str,
    db_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
) -> None:
    await upgrade_head(pg_dsn)
    raw = b"wortlaut-0008-verify-integration"
    expected = content_hash(raw)
    ref = await worm_store.put(expected, raw, content_type="application/pdf")

    async with sessions() as session:
        await session.execute(
            text(
                "INSERT INTO ingest_adapter (name, version, trust_level) "
                "VALUES (:n, :v, CAST(:t AS trust_level)) ON CONFLICT (name, version) DO NOTHING"
            ),
            {"n": "dip-api", "v": "1.0.0", "t": "verified_primary"},
        )
        await session.commit()
        source_id = await insert_source(
            session,
            NewSource(
                content_hash=expected,
                raw_bytes_ref=ref,
                archive_wayback="https://web.archive.org/snap",
                archive_today=None,
                origin_url="https://dserver.bundestag.de/x.pdf",
                source_type="plenarprotokoll",
                rights_basis="amtliches_werk_p5",
                adapter_name="dip-api",
                adapter_version="1.0.0",
                byte_size=len(raw),
                mime_type="application/pdf",
                retrieved_at=datetime.now(UTC),
            ),
        )

    async with sessions() as session:
        report = await verify_source(source_id, session=session, worm=worm_store)

    assert report.ok is True
    assert report.status == "ok"
    assert report.content_hash_expected == report.content_hash_actual == expected
    assert report.archive_wayback == "https://web.archive.org/snap"
