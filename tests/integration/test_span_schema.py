"""Integrationstests (#40) gegen echtes Postgres: Span-Schema, Constraints, Immutability.

Beweist die Invarianten aus docs/datamodel.md §2/§3.3–3.6/§4:
Objekte existieren (AC1), span ist append-only (AC2/AC3), Offset-Check (AC4),
FK-Kette source→speaker→mandate→span→span_state (AC5), FTS-Generierung (AC6),
span_state 1:1 + Enum-Validierung (AC7), Migration reversibel (AC8).
Isolation: jede Prüfung in einer zurückgerollten Transaktion.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import TextClause, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from wortlaut.store.db import create_async_engine_from
from wortlaut.store.migrations import downgrade_to, upgrade_head
from wortlaut.store.settings import DbSettings

pytestmark = pytest.mark.integration

# -- Seed-Statements (wie test_db_schema.py) --

_ADAPTER_INSERT = text(
    "INSERT INTO ingest_adapter (name, version, trust_level) "
    "VALUES (:name, :version, CAST(:trust_level AS trust_level))"
)

_SOURCE_INSERT = text(
    "INSERT INTO source (source_type, rights_basis, adapter_name, adapter_version, "
    "origin_url, content_hash, byte_size, mime_type, retrieved_at, raw_bytes_ref, "
    "archive_wayback, archive_today) VALUES ("
    "CAST(:source_type AS source_type), CAST(:rights_basis AS rights_basis), :adapter_name, "
    ":adapter_version, :origin_url, :content_hash, :byte_size, :mime_type, "
    ":retrieved_at, :raw_bytes_ref, :archive_wayback, :archive_today)"
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
    "CAST(:spoken_at AS date), :permalink, :span_hash) RETURNING id"
)

_SPAN_STATE_INSERT = text(
    "INSERT INTO span_state (span_id, verification, visibility) "
    "VALUES (CAST(:span_id AS uuid), CAST(:verification AS verification), "
    "CAST(:visibility AS visibility_class))"
)

_ADAPTER = {"name": "dip-api", "version": "1.0.0", "trust_level": "verified_primary"}


def _source(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "source_type": "drucksache",
        "rights_basis": "amtliches_werk_p5",
        "adapter_name": "dip-api",
        "adapter_version": "1.0.0",
        "origin_url": "https://example.test/doc",
        "content_hash": "a" * 64,
        "byte_size": 123,
        "mime_type": "text/plain",
        "retrieved_at": datetime(2026, 7, 19, tzinfo=UTC),
        "raw_bytes_ref": "worm://x",
        "archive_wayback": "https://web.archive.org/x",
        "archive_today": None,
    }
    params.update(overrides)
    return params


# -- conn-Fixture (Muster: test_db_schema.py) --


@pytest.fixture
async def conn(pg_dsn: str, db_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Migriertes Schema + eine Verbindung in einer am Ende zurückgerollten Transaktion."""
    await upgrade_head(pg_dsn)  # idempotent (Alembic-Versionstracking)
    async with db_engine.connect() as connection:
        trans = await connection.begin()
        try:
            yield connection
        finally:
            await trans.rollback()


# -- Seed-Helfer (alle über conn, kein Commit — Rollback-Isolation) --


async def _seed_adapter(conn: AsyncConnection) -> None:
    await conn.execute(_ADAPTER_INSERT, _ADAPTER)


async def _seed_source(conn: AsyncConnection) -> dict[str, str]:
    await conn.execute(_SOURCE_INSERT, _source())
    return {"id": "a" * 64}


async def _seed_speaker(conn: AsyncConnection, name: str = "Dr. Max Mustermann") -> str:
    result = await conn.execute(_SPEAKER_INSERT, {"name": name})
    return str(result.scalar())


async def _seed_mandate(conn: AsyncConnection, speaker_id: str) -> str:
    result = await conn.execute(
        _MANDATE_INSERT,
        {
            "speaker_id": speaker_id,
            "role": "MdB",
            "parliament": "bundestag",
            "active_from": date(2021, 10, 1),
        },
    )
    return str(result.scalar())


async def _seed_span(
    conn: AsyncConnection,
    source_id: str,
    speaker_id: str,
    mandate_id: str,
    **overrides: object,
) -> str:
    params: dict[str, object] = {
        "source_id": source_id,
        "speaker_id": speaker_id,
        "mandate_id": mandate_id,
        "verbatim_text": "Testtext",
        "text_start": 0,
        "text_end": 10,
        "spoken_at": date(2023, 3, 15),
        "permalink": "https://example.test/span",
        "span_hash": "c" * 64,
    }
    params.update(overrides)
    result = await conn.execute(_SPAN_INSERT, params)
    return str(result.scalar())


async def _expect_violation(
    conn: AsyncConnection, stmt: TextClause, params: dict[str, object]
) -> None:
    """Erwartet eine Constraint-Verletzung, isoliert im SAVEPOINT.

    Ohne SAVEPOINT wäre die äußere Transaktion nach dem Fehler aborted und
    jedes Folge-Statement würde scheinbar (aber aus falschem Grund) werfen.
    """
    nested = await conn.begin_nested()
    try:
        with pytest.raises(DBAPIError):
            await conn.execute(stmt, params)
    finally:
        await nested.rollback()


# -- Seed bis span (vollständige Kette) --


async def seed_full_chain(conn: AsyncConnection) -> dict[str, str]:
    """Legt adapter → source → speaker → mandate → span an und gibt alle IDs zurück."""
    await _seed_adapter(conn)

    # source: ID ermitteln via content_hash
    src_params = _source()
    await conn.execute(_SOURCE_INSERT, src_params)
    result = await conn.scalar(
        text("SELECT id FROM source WHERE content_hash = :h"),
        {"h": src_params["content_hash"]},
    )
    source_id = str(result)

    speaker_id = await _seed_speaker(conn)
    mandate_id = await _seed_mandate(conn, speaker_id)
    span_id = await _seed_span(conn, source_id, speaker_id, mandate_id)

    return {
        "source_id": source_id,
        "speaker_id": speaker_id,
        "mandate_id": mandate_id,
        "span_id": span_id,
    }


# -- AC-Tests --


async def test_span_schema_objects_exist(conn: AsyncConnection) -> None:
    # AC1: Tabellen speaker/mandate/span/span_state existieren;
    # Enums verification/visibility_class existieren; idx_span_fts existiert.
    tables = await conn.scalar(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name IN ('speaker','mandate','span','span_state')"
        )
    )
    enums = await conn.scalar(
        text("SELECT count(*) FROM pg_type WHERE typname IN ('verification','visibility_class')")
    )
    fts_idx = await conn.scalar(
        text(
            "SELECT count(*) FROM pg_indexes "
            "WHERE tablename = 'span' AND indexname = 'idx_span_fts'"
        )
    )
    assert tables == 4
    assert enums == 2
    assert fts_idx == 1


async def test_span_update_forbidden(conn: AsyncConnection) -> None:
    # AC2: UPDATE auf span wirft (append-only Trigger).
    chain = await seed_full_chain(conn)
    stmt = text("UPDATE span SET permalink = 'x' WHERE id = :id")
    params = {"id": chain["span_id"]}
    with pytest.raises(DBAPIError):
        await conn.execute(stmt, params)


async def test_span_delete_forbidden(conn: AsyncConnection) -> None:
    # AC3: DELETE auf span wirft (append-only Trigger).
    chain = await seed_full_chain(conn)
    stmt = text("DELETE FROM span WHERE id = :id")
    params = {"id": chain["span_id"]}
    with pytest.raises(DBAPIError):
        await conn.execute(stmt, params)


async def test_span_offsets_check(conn: AsyncConnection) -> None:
    # AC4: text_end <= text_start ⇒ CHECK-Verletzung; 0/10 ⇒ ok.
    chain = await seed_full_chain(conn)
    source_id = chain["source_id"]
    speaker_id = chain["speaker_id"]
    mandate_id = chain["mandate_id"]

    # text_end == text_start (5/5) → Verletzung
    bad_params: dict[str, object] = {
        "source_id": source_id,
        "speaker_id": speaker_id,
        "mandate_id": mandate_id,
        "verbatim_text": "gleich",
        "text_start": 5,
        "text_end": 5,
        "spoken_at": date(2023, 1, 1),
        "permalink": "https://x.test",
        "span_hash": "c" * 64,
    }
    await _expect_violation(conn, _SPAN_INSERT, bad_params)

    # 0/10 → ok
    await _seed_span(conn, source_id, speaker_id, mandate_id, text_start=0, text_end=10)


async def test_span_and_state_fk_violations(conn: AsyncConnection) -> None:
    # AC5: FK-Verletzungen an allen Nähten; komplette Kette ⇒ ok.
    await _seed_adapter(conn)
    src_params = _source()
    await conn.execute(_SOURCE_INSERT, src_params)
    result = await conn.scalar(
        text("SELECT id FROM source WHERE content_hash = :h"),
        {"h": src_params["content_hash"]},
    )
    source_id = str(result)

    # span mit erfundener source_id → FK-Verletzung
    speaker_id = await _seed_speaker(conn)
    mandate_id = await _seed_mandate(conn, speaker_id)
    base_span: dict[str, object] = {
        "source_id": source_id,
        "speaker_id": speaker_id,
        "mandate_id": mandate_id,
        "verbatim_text": "x",
        "text_start": 0,
        "text_end": 10,
        "spoken_at": date(2023, 1, 1),
        "permalink": "https://x.test",
        "span_hash": "c" * 64,
    }
    bad_source = dict(base_span, source_id="00000000-0000-0000-0000-000000000000")
    await _expect_violation(conn, _SPAN_INSERT, bad_source)

    # span mit erfundener speaker_id → FK-Verletzung
    bad_speaker = dict(base_span, speaker_id="00000000-0000-0000-0000-000000000000")
    await _expect_violation(conn, _SPAN_INSERT, bad_speaker)

    # mandate mit erfundener speaker_id → FK-Verletzung
    bad_mandate: dict[str, object] = {
        "speaker_id": "00000000-0000-0000-0000-000000000000",
        "role": "MdB",
        "parliament": "bundestag",
        "active_from": date(2021, 1, 1),
    }
    await _expect_violation(conn, _MANDATE_INSERT, bad_mandate)

    # span_state mit erfundener span_id → FK-Verletzung
    bad_state: dict[str, object] = {
        "span_id": "00000000-0000-0000-0000-000000000000",
        "verification": "official",
        "visibility": "public",
    }
    await _expect_violation(conn, _SPAN_STATE_INSERT, bad_state)

    # Komplette gültige Kette ⇒ ok
    span_id = await _seed_span(conn, source_id, speaker_id, mandate_id)
    await conn.execute(
        _SPAN_STATE_INSERT,
        {"span_id": span_id, "verification": "official", "visibility": "public"},
    )


async def test_span_fts_generated_and_matches(conn: AsyncConnection) -> None:
    # AC6: fts wird automatisch generiert und ein deutscher Volltext-Match trifft.
    chain = await seed_full_chain(conn)
    span_id = chain["span_id"]

    # fts IS NOT NULL nach Generierung (Default-Span 'Testtext')
    is_not_null = await conn.scalar(
        text("SELECT fts IS NOT NULL FROM span WHERE id = CAST(:id AS uuid)"),
        {"id": span_id},
    )
    assert is_not_null is True

    # Span mit echtem Satz einfügen. Achtung Sprachfalle: 'Würde' ist im
    # german-Stemmer ein STOPPWORT (Konjunktiv von 'werden') und landet nie
    # im tsvector — daher wird auf 'unantastbar' gematcht.
    verbatim = "Die Würde des Menschen ist unantastbar"
    satz_span_id = await _seed_span(
        conn,
        chain["source_id"],
        chain["speaker_id"],
        chain["mandate_id"],
        verbatim_text=verbatim,
    )

    # FTS matcht 'unantastbar' (german-Konfiguration, GIN-Index vorhanden per AC1)
    match_count = await conn.scalar(
        text(
            "SELECT count(*) FROM span WHERE fts @@ to_tsquery('german', 'unantastbar') "
            "AND id = CAST(:id AS uuid)"
        ),
        {"id": satz_span_id},
    )
    assert match_count == 1


async def test_span_state_one_to_one_and_enums(conn: AsyncConnection) -> None:
    # AC7: Gültiger span_state ok; zweiter für gleiche span_id → UNIQUE;
    # ungültiger Enum-Wert → Fehlschlag.
    chain = await seed_full_chain(conn)
    span_id = chain["span_id"]

    # Erster span_state → ok
    await conn.execute(
        _SPAN_STATE_INSERT,
        {"span_id": span_id, "verification": "official", "visibility": "public"},
    )

    # Zweiter span_state für selbe span_id → PRIMARY KEY-Verletzung (1:1)
    duplicate_state: dict[str, object] = {
        "span_id": span_id,
        "verification": "machine",
        "visibility": "restricted",
    }
    await _expect_violation(conn, _SPAN_STATE_INSERT, duplicate_state)

    # Ungültiger Enum-Wert 'quatsch' → CAST-Fehler (vor jeder Constraint-Prüfung)
    invalid_enum: dict[str, object] = {
        "span_id": span_id,
        "verification": "quatsch",
        "visibility": "public",
    }
    await _expect_violation(conn, _SPAN_STATE_INSERT, invalid_enum)


async def test_migration_0003_downgrade_clean(fresh_pg_dsn: str) -> None:
    # AC8: upgrade → downgrade auf 0002 entfernt span-Tabellen + Enums;
    # source bleibt (0002 intakt).
    await upgrade_head(fresh_pg_dsn)
    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        async with engine.connect() as c:
            before = await c.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name IN ('speaker','mandate','span','span_state')"
                )
            )
        assert before == 4

        await downgrade_to(fresh_pg_dsn, "0002")

        async with engine.connect() as c:
            span_tables = await c.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name IN ('speaker','mandate','span','span_state')"
                )
            )
            enums = await c.scalar(
                text(
                    "SELECT count(*) FROM pg_type "
                    "WHERE typname IN ('verification','visibility_class')"
                )
            )
            # source existiert NOCH (0002 intakt)
            source_exists = await c.scalar(
                text("SELECT count(*) FROM information_schema.tables WHERE table_name = 'source'")
            )
        assert span_tables == 0
        assert enums == 0
        assert source_exists == 1
    finally:
        await engine.dispose()
