"""Integrationstests (#2) gegen echtes Postgres: Schema, Constraints, Immutability.

Beweist die Invarianten aus docs/datamodel.md §3.1/§3.2/§4:
Objekte existieren (AC1), source/ingest_adapter sind append-only (AC2/AC3/AC8),
Dedup via UNIQUE (AC4), Fremdarchiv-Pflicht (AC5), FK (AC6), rights_basis NOT NULL (AC7),
Migration reversibel (AC9). Isolation: jede Prüfung in einer zurückgerollten Transaktion.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from wortlaut.store.db import create_async_engine_from
from wortlaut.store.migrations import downgrade_to, upgrade_head
from wortlaut.store.settings import DbSettings

pytestmark = pytest.mark.integration

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


async def test_schema_objects_exist(conn: AsyncConnection) -> None:
    # AC1: Tabellen + die drei Enum-Typen existieren nach der Migration.
    tables = await conn.scalar(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name IN ('ingest_adapter','source')"
        )
    )
    enums = await conn.scalar(
        text(
            "SELECT count(*) FROM pg_type "
            "WHERE typname IN ('source_type','rights_basis','trust_level')"
        )
    )
    assert tables == 2
    assert enums == 3


async def test_source_update_forbidden(conn: AsyncConnection) -> None:
    # AC2: UPDATE auf source wirft (append-only Trigger).
    await conn.execute(_ADAPTER_INSERT, _ADAPTER)
    await conn.execute(_SOURCE_INSERT, _source())
    with pytest.raises(DBAPIError):
        await conn.execute(
            text("UPDATE source SET origin_url = 'y' WHERE content_hash = :h"),
            {"h": "a" * 64},
        )


async def test_source_delete_forbidden(conn: AsyncConnection) -> None:
    # AC3: DELETE auf source wirft (append-only Trigger).
    await conn.execute(_ADAPTER_INSERT, _ADAPTER)
    await conn.execute(_SOURCE_INSERT, _source())
    with pytest.raises(DBAPIError):
        await conn.execute(text("DELETE FROM source WHERE content_hash = :h"), {"h": "a" * 64})


async def test_source_content_hash_unique(conn: AsyncConnection) -> None:
    # AC4: zweiter Insert mit gleichem content_hash -> UNIQUE-Verletzung (Dedup).
    await conn.execute(_ADAPTER_INSERT, _ADAPTER)
    await conn.execute(_SOURCE_INSERT, _source())
    with pytest.raises(DBAPIError):
        await conn.execute(_SOURCE_INSERT, _source(origin_url="https://example.test/other"))


async def test_source_requires_archive(conn: AsyncConnection) -> None:
    # AC5: ohne Fremdarchiv -> chk_archive-Verletzung; mit einem -> ok.
    await conn.execute(_ADAPTER_INSERT, _ADAPTER)
    with pytest.raises(DBAPIError):
        await conn.execute(_SOURCE_INSERT, _source(archive_wayback=None, archive_today=None))


async def test_source_with_one_archive_ok(conn: AsyncConnection) -> None:
    # AC5 (Positivfall): genau ein Fremdarchiv reicht.
    await conn.execute(_ADAPTER_INSERT, _ADAPTER)
    await conn.execute(
        _SOURCE_INSERT,
        _source(archive_wayback=None, archive_today="https://archive.today/x"),
    )


async def test_source_fk_adapter_missing(conn: AsyncConnection) -> None:
    # AC6: unbekannter Adapter -> FK-Verletzung.
    with pytest.raises(DBAPIError):
        await conn.execute(_SOURCE_INSERT, _source(adapter_name="nope", adapter_version="9.9"))


async def test_source_fk_adapter_present_ok(conn: AsyncConnection) -> None:
    # AC6 (Positivfall): existierender Adapter -> Insert ok.
    await conn.execute(_ADAPTER_INSERT, _ADAPTER)
    await conn.execute(_SOURCE_INSERT, _source())


async def test_source_rights_basis_not_null(conn: AsyncConnection) -> None:
    # AC7: rights_basis Pflichtfeld (Legal §10).
    await conn.execute(_ADAPTER_INSERT, _ADAPTER)
    with pytest.raises(DBAPIError):
        await conn.execute(_SOURCE_INSERT, _source(rights_basis=None))


async def test_ingest_adapter_immutable(conn: AsyncConnection) -> None:
    # AC8: UPDATE auf ingest_adapter wirft (immutabel je Version).
    await conn.execute(_ADAPTER_INSERT, _ADAPTER)
    with pytest.raises(DBAPIError):
        await conn.execute(
            text("UPDATE ingest_adapter SET description = 'x' WHERE name = :n AND version = :v"),
            {"n": "dip-api", "v": "1.0.0"},
        )


async def test_migration_0002_downgrade_clean(fresh_pg_dsn: str) -> None:
    # AC9: upgrade -> downgrade auf frischer DB entfernt Tabellen, Enums, Function.
    await upgrade_head(fresh_pg_dsn)
    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        async with engine.connect() as c:
            before = await c.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name IN ('ingest_adapter','source')"
                )
            )
        assert before == 2

        await downgrade_to(fresh_pg_dsn, "0001")

        async with engine.connect() as c:
            tables = await c.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name IN ('ingest_adapter','source')"
                )
            )
            enums = await c.scalar(
                text(
                    "SELECT count(*) FROM pg_type "
                    "WHERE typname IN ('source_type','rights_basis','trust_level')"
                )
            )
            func_count = await c.scalar(
                text("SELECT count(*) FROM pg_proc WHERE proname = 'forbid_mutation'")
            )
        assert tables == 0
        assert enums == 0
        assert func_count == 0
    finally:
        await engine.dispose()
