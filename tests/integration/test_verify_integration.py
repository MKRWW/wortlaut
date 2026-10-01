"""Integration (#8, AC5): verify_source gegen echtes Postgres + MinIO.

Legt eine source mit WORM-Objekt an und rechnet die Integrität nach.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from wortlaut.evidence.hashing import content_hash
from wortlaut.pipeline.verify import verify_source
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.sources import NewSource, insert_source
from wortlaut.store.worm import WormStore

pytestmark = pytest.mark.integration

SeedAttestation = Callable[[AsyncSession | AsyncConnection, UUID | str], Awaitable[None]]

_MIME_PDF = "application/pdf"
_ADAPTER_SQL = (
    "INSERT INTO ingest_adapter (name, version, trust_level) "
    "VALUES (:n, :v, CAST(:t AS trust_level)) ON CONFLICT (name, version) DO NOTHING"
)
_ADAPTER_PARAMS = {"n": "dip-api", "v": "1.0.0", "t": "verified_primary"}


async def _fresh_sessions(
    dsn: str,
) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Migrierte, frische DB (``fresh_pg_dsn``): Sessionmaker + Engine (Dispose: Aufrufer)."""
    from wortlaut.store.db import create_async_engine_from, make_sessionmaker
    from wortlaut.store.settings import DbSettings

    await upgrade_head(dsn)
    engine = create_async_engine_from(DbSettings(dsn=dsn))
    return make_sessionmaker(engine), engine


async def _seed_adapter(session: AsyncSession) -> None:
    await session.execute(text(_ADAPTER_SQL), _ADAPTER_PARAMS)
    await session.commit()


def _new_source(content_hash: str, raw_bytes_ref: str, *, origin: str, byte_size: int) -> NewSource:
    return NewSource(
        content_hash=content_hash,
        raw_bytes_ref=raw_bytes_ref,
        archive_wayback="https://web.archive.org/snap",
        archive_today=None,
        origin_url=origin,
        source_type="plenarprotokoll",
        rights_basis="amtliches_werk_p5",
        adapter_name="dip-api",
        adapter_version="1.0.0",
        byte_size=byte_size,
        mime_type=_MIME_PDF,
        retrieved_at=datetime.now(UTC),
    )


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


# ── Spec 0128: verify_source weist die Attestierung aus ──────────────────


async def test_verify_reports_attestation(
    fresh_pg_dsn: str,
    worm_store: WormStore,
    seed_attestation: SeedAttestation,
) -> None:
    """AC2 (0128): attestierte Quelle, Hash passt → ``ok``/``status`` unverändert
    (``True``/``"ok"``) und die Attestierung vollständig: Status ``"ok"`` plus
    Archivar, Snapshot-URL, Snapshot-Zeitpunkt und geprüfter Hash."""
    raw = b"wortlaut-0128-verify-attested"
    expected = content_hash(raw)
    origin = "https://dserver.bundestag.de/0128-verify.pdf"
    sessions, engine = await _fresh_sessions(fresh_pg_dsn)
    try:
        ref = await worm_store.put(expected, raw, content_type=_MIME_PDF)
        async with sessions() as session:
            await _seed_adapter(session)
            source_id = await insert_source(
                session, _new_source(expected, ref, origin=origin, byte_size=len(raw))
            )
            await seed_attestation(session, source_id)
            await session.commit()

        async with sessions() as session:
            report = await verify_source(source_id, session=session, worm=worm_store)
    finally:
        await engine.dispose()

    assert report.ok is True
    assert report.status == "ok"
    assert report.attestation_status == "ok"
    assert report.attestation_archiver == "wayback"
    assert report.attestation_snapshot_url == "https://web.archive.org/web/20260101000000/" + origin
    assert report.attestation_verified_sha256 == expected
    assert report.attestation_snapshot_at is not None


async def test_verify_mismatch_keeps_attestation(
    fresh_pg_dsn: str,
    worm_store: WormStore,
    seed_attestation: SeedAttestation,
) -> None:
    """AC3 (0128): ein Fehlbefund ändert die Attestierung NICHT — bei
    ``hash_mismatch`` (WORM-Bytes ≠ ``content_hash``) und bei ``worm_missing``
    (Objekt fehlt) bleibt ``attestation_status == "ok"`` mit gesetzter
    Snapshot-URL, während ``ok`` ``False`` ist."""
    original = b"wortlaut-0128-original"
    expected = content_hash(original)
    tampered = b"wortlaut-0128-manipuliert"
    tampered_ref = await worm_store.put(content_hash(tampered), tampered, content_type=_MIME_PDF)
    missing_digest = content_hash(b"wortlaut-0128-fehlt-im-worm")
    sessions, engine = await _fresh_sessions(fresh_pg_dsn)
    try:
        async with sessions() as session:
            await _seed_adapter(session)
            mismatch_id = await insert_source(
                session,
                _new_source(
                    expected,
                    tampered_ref,
                    origin="https://dserver.bundestag.de/0128-mismatch.pdf",
                    byte_size=len(original),
                ),
            )
            missing_id = await insert_source(
                session,
                _new_source(
                    missing_digest,
                    "s3://wortlaut-worm/nicht-da?versionId=0000000000",
                    origin="https://dserver.bundestag.de/0128-missing.pdf",
                    byte_size=len(tampered),
                ),
            )
            await seed_attestation(session, mismatch_id)
            await seed_attestation(session, missing_id)
            await session.commit()

        async with sessions() as session:
            mismatch_report = await verify_source(mismatch_id, session=session, worm=worm_store)
        async with sessions() as session:
            missing_report = await verify_source(missing_id, session=session, worm=worm_store)
    finally:
        await engine.dispose()

    assert mismatch_report.ok is False
    assert mismatch_report.status == "hash_mismatch"
    assert mismatch_report.attestation_status == "ok"
    assert mismatch_report.attestation_snapshot_url is not None
    assert missing_report.ok is False
    assert missing_report.status == "worm_missing"
    assert missing_report.attestation_status == "ok"
    assert missing_report.attestation_snapshot_url is not None


async def test_verify_unattested_source(
    fresh_pg_dsn: str,
    worm_store: WormStore,
) -> None:
    """AC4 (0128): Quelle ohne Attestierung → ``attestation_status == "missing"``
    mit allen vier Feldern ``None``; ``ok``/``status`` unverändert gegenüber
    heute (hier: Hash passt → ``True``/``"ok"``)."""
    raw = b"wortlaut-0128-unattested"
    expected = content_hash(raw)
    sessions, engine = await _fresh_sessions(fresh_pg_dsn)
    try:
        ref = await worm_store.put(expected, raw, content_type=_MIME_PDF)
        async with sessions() as session:
            await _seed_adapter(session)
            source_id = await insert_source(
                session,
                _new_source(
                    expected,
                    ref,
                    origin="https://dserver.bundestag.de/0128-unattested.pdf",
                    byte_size=len(raw),
                ),
            )

        async with sessions() as session:
            report = await verify_source(source_id, session=session, worm=worm_store)
    finally:
        await engine.dispose()

    assert report.ok is True
    assert report.status == "ok"
    assert report.attestation_status == "missing"
    assert report.attestation_archiver is None
    assert report.attestation_snapshot_url is None
    assert report.attestation_snapshot_at is None
    assert report.attestation_verified_sha256 is None
