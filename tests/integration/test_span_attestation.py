"""Integration (Spec 0126, ADR-0009): Migration 0006 — Attestierungs-Guard für span.

AC2: Auf einer DB in Revision 0005 mit einer Quelle, die einen Span, aber keine
``source_archive``-Zeile hat, verweigert das Upgrade auf ``head`` (die Meldung
nennt ``attest``); der Trigger existiert danach nicht. Nach der Attestierung
gelingt das Upgrade, der Trigger existiert.

AC3: ``0006`` lässt sich auf ``0005`` zurückfahren; danach ist ein Span-Insert
für eine unattestierte Quelle wieder möglich.

Isolation: ``fresh_pg_dsn`` (eigener Container je Test); Quelle/Sprecher/
Mandat/Span werden per rohem SQL angelegt (Vorbild ``test_span_schema.py``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from wortlaut.store.db import create_async_engine_from
from wortlaut.store.migrations import downgrade_to, upgrade_head
from wortlaut.store.settings import DbSettings

pytestmark = pytest.mark.integration

_TRIGGER = "trg_span_requires_attestation"
_REV_SOURCE_ARCHIVE = "0005"
_SPAN_HASH = "c" * 64

_ADAPTER_INSERT = text(
    "INSERT INTO ingest_adapter (name, version, trust_level) "
    "VALUES ('dip-api', '1.0.0', CAST('verified_primary' AS trust_level))"
)

_SOURCE_INSERT = text(
    "INSERT INTO source (source_type, rights_basis, adapter_name, adapter_version, "
    "origin_url, content_hash, byte_size, mime_type, retrieved_at, raw_bytes_ref, "
    "archive_wayback, archive_today) VALUES ("
    "CAST(:source_type AS source_type), CAST(:rights_basis AS rights_basis), "
    "'dip-api', '1.0.0', :origin_url, :content_hash, :byte_size, :mime_type, "
    "CAST(:retrieved_at AS timestamptz), :raw_bytes_ref, :archive_wayback, NULL)"
)

_SPEAKER_INSERT = text("INSERT INTO speaker (full_name) VALUES (:name) RETURNING id")

_MANDATE_INSERT = text(
    "INSERT INTO mandate (speaker_id, role, parliament, active_from) "
    "VALUES (CAST(:speaker_id AS uuid), :role, :parliament, CAST(:active_from AS date)) "
    "RETURNING id"
)

_SPAN_INSERT = text(
    "INSERT INTO span (source_id, speaker_id, mandate_id, verbatim_text, "
    "text_start, text_end, spoken_at, permalink, span_hash) "
    "VALUES (CAST(:source_id AS uuid), CAST(:speaker_id AS uuid), "
    "CAST(:mandate_id AS uuid), :verbatim_text, :text_start, :text_end, "
    "CAST(:spoken_at AS date), :permalink, :span_hash)"
)


def _source_params(content_hash: str) -> dict[str, object]:
    return {
        "source_type": "drucksache",
        "rights_basis": "amtliches_werk_p5",
        "origin_url": "https://example.test/doc",
        "content_hash": content_hash,
        "byte_size": 123,
        "mime_type": "text/plain",
        "retrieved_at": datetime(2026, 7, 19, tzinfo=UTC),
        "raw_bytes_ref": "worm://x",
        "archive_wayback": "https://web.archive.org/x",
    }


async def _seed_span_chain(conn: AsyncConnection, content_hash: str) -> UUID:
    """Legt adapter → source → speaker → mandate → span an (ohne Attestierung)."""
    await conn.execute(_ADAPTER_INSERT)
    await conn.execute(_SOURCE_INSERT, _source_params(content_hash))
    result = await conn.execute(_SPEAKER_INSERT, {"name": "Dr. Max Mustermann"})
    speaker_id = result.scalar_one()
    mandate_result = await conn.execute(
        _MANDATE_INSERT,
        {
            "speaker_id": speaker_id,
            "role": "MdB",
            "parliament": "bundestag",
            "active_from": date(2021, 10, 1),
        },
    )
    raw_id = await conn.scalar(
        text("SELECT id FROM source WHERE content_hash = :h"),
        {"h": content_hash},
    )
    assert raw_id is not None
    source_id = UUID(str(raw_id))
    await conn.execute(
        _SPAN_INSERT,
        {
            "source_id": source_id,
            "speaker_id": speaker_id,
            "mandate_id": mandate_result.scalar_one(),
            "verbatim_text": "Testtext",
            "text_start": 0,
            "text_end": 10,
            "spoken_at": date(2023, 3, 15),
            "permalink": "https://example.test/span",
            "span_hash": _SPAN_HASH,
        },
    )
    return source_id


async def _trigger_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        count = await conn.scalar(
            text("SELECT count(*) FROM pg_trigger WHERE tgname = :name"),
            {"name": _TRIGGER},
        )
    assert count is not None
    return int(count)


async def _span_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        count = await conn.scalar(text("SELECT count(*) FROM span"))
    assert count is not None
    return int(count)


async def test_migration_refuses_unattested_spans(
    fresh_pg_dsn: str,
    seed_attestation: Callable[[AsyncSession | AsyncConnection, UUID | str], Awaitable[None]],
) -> None:
    # AC2: DB auf 0005 mit einer Quelle, die einen Span, aber keine
    # source_archive-Zeile hat → das Upgrade schlägt fehl (Meldung nennt
    # `attest`), der Trigger existiert nicht. Mit Attestierung gelingt es.
    await upgrade_head(fresh_pg_dsn)
    await downgrade_to(fresh_pg_dsn, _REV_SOURCE_ARCHIVE)
    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            source_id = await _seed_span_chain(conn, "a" * 64)
            await trans.commit()

        with pytest.raises(Exception, match="attest"):
            await upgrade_head(fresh_pg_dsn)

        assert await _trigger_count(engine) == 0

        async with engine.connect() as conn:
            trans = await conn.begin()
            await seed_attestation(conn, source_id)
            await trans.commit()

        await upgrade_head(fresh_pg_dsn)

        assert await _trigger_count(engine) == 1
    finally:
        await engine.dispose()


async def test_migration_downgrade_removes_guard(fresh_pg_dsn: str) -> None:
    # AC3: 0006 lässt sich auf 0005 zurückfahren; danach ist ein Span-Insert
    # für eine unattestierte Quelle wieder möglich.
    await upgrade_head(fresh_pg_dsn)
    await downgrade_to(fresh_pg_dsn, _REV_SOURCE_ARCHIVE)
    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        assert await _trigger_count(engine) == 0

        async with engine.connect() as conn:
            trans = await conn.begin()
            await _seed_span_chain(conn, "b" * 64)
            await trans.commit()

        assert await _span_count(engine) == 1
    finally:
        await engine.dispose()
