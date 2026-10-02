"""Integration (Spec 0130): capture_request-Tabelle und Capture-Selection
gegen echtes Postgres; AC11 mit echtem MinIO.

AC1 (append-only + ``chk_capture_outcome``), AC2 (attestierte Quellen nie),
AC3 (Abkühlzeiten, jeweils die LETZTE Anfrage maßgeblich) und AC11 (Capture
schreibt nie in ``source_archive``). Quellen und Anfragen entstehen per
rohem SQL bzw. ``ingest_source`` mit Fixture-Adaptern — ``fetch`` bleibt
hermetisch (R-TEST-03); der Lookup ist ein Fake ohne Live-Call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from wortlaut.archive.wayback_lookup import SnapshotCandidate
from wortlaut.evidence.hashing import content_hash
from wortlaut.ingest.adapter import RawSource, SourceRef, SpanDraft
from wortlaut.ingest.dip import DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings
from wortlaut.pipeline.capture import capture_source
from wortlaut.pipeline.ingest import IngestOutcome, PipelineDeps, ingest_source
from wortlaut.store.captures import CaptureCandidate, list_sources_needing_capture
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.worm import WormStore

pytestmark = pytest.mark.integration

SeedAttestation = Callable[[AsyncSession | AsyncConnection, UUID | str], Awaitable[None]]

_NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
_CAPTURED_COOLDOWN = timedelta(hours=72)
_FAILED_COOLDOWN = timedelta(hours=6)


def _snapshot_url(origin: str) -> str:
    return f"https://web.archive.org/web/20260805170741/{origin}"


_ADAPTER_INSERT = text(
    "INSERT INTO ingest_adapter (name, version, trust_level) "
    "VALUES ('dip-api', '1.0.0', CAST('verified_primary' AS trust_level)) "
    "ON CONFLICT (name, version) DO NOTHING"
)

_SOURCE_INSERT = text(
    "INSERT INTO source (source_type, rights_basis, adapter_name, adapter_version, "
    "origin_url, content_hash, byte_size, mime_type, retrieved_at, raw_bytes_ref, "
    "archive_wayback) VALUES ("
    "CAST(:source_type AS source_type), CAST(:rights_basis AS rights_basis), 'dip-api', "
    "'1.0.0', :origin_url, :content_hash, :byte_size, 'application/pdf', :retrieved_at, "
    "'s3://bucket/raw?versionId=1', :archive_wayback) RETURNING id"
)

_REQUEST_INSERT = text(
    "INSERT INTO capture_request (source_id, archiver, outcome, snapshot_url, reason, "
    "requested_at) VALUES (CAST(:s AS uuid), 'wayback', :o, :u, :r, :t)"
)


def _request_params(
    source_id: UUID,
    outcome: str,
    requested_at: datetime,
    *,
    snapshot_url: str | None = None,
    reason: str | None = None,
) -> dict[str, object]:
    return {
        "s": str(source_id),
        "o": outcome,
        "u": snapshot_url,
        "r": reason,
        "t": requested_at,
    }


def _dip_settings() -> DipSettings:
    return DipSettings(
        api_key="test-key",
        api_base_url="https://search.dip.bundestag.de/api/v1",
        pdf_host="dserver.bundestag.de",
    )


class _FixtureDipAdapter(DipPlenarprotokollAdapter):
    """Realer DIP-Adapter (echtes normalize/parse aus #41), nur ``fetch`` liefert
    die Fixture-Bytes — kein Netz."""

    def __init__(self, raw: RawSource) -> None:
        super().__init__(_dip_settings())
        self._fixture = raw

    async def fetch(self, ref: SourceRef) -> RawSource:
        return self._fixture


class _EmptyDipAdapter(_FixtureDipAdapter):
    """Wie ``_FixtureDipAdapter``, aber ``parse`` liefert ``[]``."""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return []


class _OkArchiver:
    """Archiver-Fake: liefert eine feste Snapshot-URL (kein Live-Call)."""

    def __init__(self, url: str) -> None:
        self._url = url
        self.archive_calls: list[str] = []

    async def archive(self, origin_url: str) -> str:
        self.archive_calls.append(origin_url)
        return self._url


class _NoCandidateLookup:
    """WaybackLookup-Fake: kein byte-gleicher Snapshot im CDX-Index (kein
    Live-Call, R-TEST-03)."""

    def __init__(self) -> None:
        self.candidates_calls = 0

    async def candidates(self, origin_url: str, *, sha1_b32: str) -> list[SnapshotCandidate]:
        self.candidates_calls += 1
        return []

    async def fetch(self, candidate: SnapshotCandidate) -> bytes | None:
        raise AssertionError("not used (capture liest nur candidates)")

    async def aclose(self) -> None:
        pass


def _raw(raw_bytes: bytes, origin: str) -> RawSource:
    return RawSource(
        origin_url=origin,
        source_type="plenarprotokoll",
        raw_bytes=raw_bytes,
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )


async def _fresh(dsn: str) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Migrierte, frische DB: Sessionmaker + Engine (Dispose obliegt dem Aufrufer)."""
    from wortlaut.store.db import create_async_engine_from, make_sessionmaker
    from wortlaut.store.settings import DbSettings

    await upgrade_head(dsn)
    engine = create_async_engine_from(DbSettings(dsn=dsn))
    return make_sessionmaker(engine), engine


async def _seed_source(sessions: async_sessionmaker[AsyncSession], origin: str) -> UUID:
    """Quelle per rohem SQL (ausreicht für Selection; kein WORM-Objekt nötig)."""
    async with sessions() as session:
        await session.execute(_ADAPTER_INSERT)
        source_id: UUID = (
            await session.execute(
                _SOURCE_INSERT,
                {
                    "source_type": "plenarprotokoll",
                    "rights_basis": "amtliches_werk_p5",
                    "origin_url": origin,
                    "content_hash": content_hash(origin.encode("utf-8")),
                    "byte_size": len(origin),
                    "retrieved_at": _NOW,
                    "archive_wayback": _snapshot_url(origin),
                },
            )
        ).scalar_one()
        await session.commit()
    return source_id


async def _ingest(
    sessions: async_sessionmaker[AsyncSession],
    worm: WormStore,
    raw_bytes: bytes,
    *,
    origin: str,
) -> IngestOutcome:
    """Quelle per ``ingest_source`` mit Fixture-Adaptern (Muster ``test_reparse``)."""
    adapter = _EmptyDipAdapter(_raw(raw_bytes, origin))
    deps = PipelineDeps(
        adapter=adapter,
        wayback=_OkArchiver(_snapshot_url(origin)),
        archive_today=_OkArchiver("https://archive.ph/snap"),
        worm=worm,
    )
    ref = SourceRef(origin_url=origin, source_type="plenarprotokoll", hint={})
    # SSRF-Check gemockt: keine echte DNS-Auflösung im Test (R-TEST-03, hermetisch).
    with patch("wortlaut.archive.archiver.assert_url_allowed"):
        async with sessions() as session:
            await session.execute(_ADAPTER_INSERT)
            await session.commit()
            return await ingest_source(
                ref,
                deps=deps,
                session=session,
                rights_basis="amtliches_werk_p5",
            )


async def _selection(
    sessions: async_sessionmaker[AsyncSession],
) -> list[CaptureCandidate]:
    async with sessions() as session:
        return await list_sources_needing_capture(
            session,
            now=_NOW,
            captured_cooldown=_CAPTURED_COOLDOWN,
            failed_cooldown=_FAILED_COOLDOWN,
        )


# ── AC1: append-only (UPDATE/DELETE scheitern am Trigger) ────────────────


async def test_capture_request_append_only(fresh_pg_dsn: str) -> None:
    """AC1: UPDATE und DELETE auf ``capture_request`` scheitern am
    Immutability-Trigger; die Zeile bleibt unverändert."""
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        origin = "https://dserver.bundestag.de/0130-ac1.pdf"
        source_id = await _seed_source(sessions, origin)
        async with sessions() as session:
            await session.execute(
                _REQUEST_INSERT,
                _request_params(
                    source_id,
                    "captured",
                    _NOW - _CAPTURED_COOLDOWN - timedelta(hours=1),
                    snapshot_url=_snapshot_url(origin),
                ),
            )
            await session.commit()

        update_sql = text("UPDATE capture_request SET reason = 'x' WHERE source_id = :s")
        delete_sql = text("DELETE FROM capture_request WHERE source_id = :s")
        id_params = {"s": str(source_id)}
        async with sessions() as session:
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(update_sql, id_params)
            await session.rollback()
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(delete_sql, id_params)
            await session.rollback()

        async with sessions() as session:
            count = await session.scalar(
                text("SELECT count(*) FROM capture_request WHERE source_id = :s"),
                {"s": str(source_id)},
            )
    finally:
        await engine.dispose()
    assert count == 1


# ── AC1: chk_capture_outcome (captured ⇒ URL, failed ⇒ reason) ───────────


async def test_capture_request_outcome_check(fresh_pg_dsn: str) -> None:
    """AC1: ein ``captured`` ohne ``snapshot_url`` und ein ``failed`` ohne
    ``reason`` scheitern am Check; die gültigen Kombinationen gelingen."""
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        origin = "https://dserver.bundestag.de/0130-ac1b.pdf"
        source_id = await _seed_source(sessions, origin)
        an_hour_ago = _NOW - timedelta(hours=1)
        bad_captured = _request_params(source_id, "captured", an_hour_ago)
        bad_failed = _request_params(source_id, "failed", an_hour_ago)
        async with sessions() as session:
            with pytest.raises(DBAPIError, match="chk_capture_outcome"):
                await session.execute(_REQUEST_INSERT, bad_captured)
            await session.rollback()
            with pytest.raises(DBAPIError, match="chk_capture_outcome"):
                await session.execute(_REQUEST_INSERT, bad_failed)
            await session.rollback()

            good_captured = _request_params(
                source_id,
                "captured",
                _NOW - _CAPTURED_COOLDOWN - timedelta(hours=1),
                snapshot_url=_snapshot_url(origin),
            )
            good_failed = _request_params(
                source_id,
                "failed",
                _NOW - _FAILED_COOLDOWN - timedelta(hours=1),
                reason="wayback:http_status_429",
            )
            await session.execute(_REQUEST_INSERT, good_captured)
            await session.execute(_REQUEST_INSERT, good_failed)
            await session.commit()

        async with sessions() as session:
            count = await session.scalar(
                text("SELECT count(*) FROM capture_request WHERE source_id = :s"),
                {"s": str(source_id)},
            )
    finally:
        await engine.dispose()
    assert count == 2


# ── AC2: attestierte Quellen werden nie gewählt ──────────────────────────


async def test_selection_skips_attested(
    fresh_pg_dsn: str, seed_attestation: SeedAttestation
) -> None:
    """AC2: eine attestierte Quelle wird nie gewählt, gleich welche
    (abgekühlten) Anfragen es gibt; die unattestierte Quelle wird gewählt."""
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        unattested = await _seed_source(
            sessions, "https://dserver.bundestag.de/0130-ac2-unattested.pdf"
        )
        attested_origin = "https://dserver.bundestag.de/0130-ac2-attested.pdf"
        attested = await _seed_source(sessions, attested_origin)
        async with sessions() as session:
            await seed_attestation(session, attested)
            # Abgekühlte Anfrage auf der attestierten Quelle — irrelevant.
            await session.execute(
                _REQUEST_INSERT,
                _request_params(
                    attested,
                    "captured",
                    _NOW - _CAPTURED_COOLDOWN - timedelta(hours=1),
                    snapshot_url=_snapshot_url(attested_origin),
                ),
            )
            await session.commit()

        chosen = await _selection(sessions)
    finally:
        await engine.dispose()
    assert [c.source_id for c in chosen] == [unattested]


# ── AC3: Abkühlzeiten (letzte Anfrage maßgeblich) ────────────────────────


@pytest.mark.parametrize(
    ("outcome", "age_hours", "chosen"),
    [
        (None, None, True),
        ("captured", 1.0, False),
        ("captured", 73.0, True),
        ("failed", 1.0, False),
        ("failed", 7.0, True),
    ],
)
async def test_selection_cooldowns(
    fresh_pg_dsn: str,
    outcome: str | None,
    age_hours: float | None,
    chosen: bool,
) -> None:
    """AC3: mit festem ``now`` — keine Anfrage → gewählt; ``captured`` vor
    1 h (Abkühlung 72 h) → nicht, vor 73 h → gewählt; ``failed`` vor 1 h
    (Abkühlung 6 h) → nicht, vor 7 h → gewählt."""
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        label = outcome if outcome is not None else "none"
        origin = f"https://dserver.bundestag.de/0130-ac3-{label}.pdf"
        source_id = await _seed_source(sessions, origin)
        async with sessions() as session:
            if outcome is not None and age_hours is not None:
                params = _request_params(
                    source_id,
                    outcome,
                    _NOW - timedelta(hours=age_hours),
                    snapshot_url=_snapshot_url(origin) if outcome == "captured" else None,
                    reason=None if outcome == "captured" else "wayback:http_status_429",
                )
                await session.execute(_REQUEST_INSERT, params)
            await session.commit()

        chosen_now = await _selection(sessions)
    finally:
        await engine.dispose()
    assert [c.source_id for c in chosen_now] == ([source_id] if chosen else [])


# ── AC3: maßgeblich ist die LETZTE Anfrage ────────────────────────────────


async def test_selection_uses_latest_request(fresh_pg_dsn: str) -> None:
    """AC3: alte ``failed`` (7 h, abgekühlt) + neuere ``captured`` (1 h,
    noch nicht) → nicht gewählt; die jüngste Anfrage entscheidet."""
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        origin = "https://dserver.bundestag.de/0130-ac3-latest.pdf"
        source_id = await _seed_source(sessions, origin)
        async with sessions() as session:
            await session.execute(
                _REQUEST_INSERT,
                _request_params(
                    source_id,
                    "failed",
                    _NOW - timedelta(hours=7),
                    reason="wayback:http_status_429",
                ),
            )
            await session.execute(
                _REQUEST_INSERT,
                _request_params(
                    source_id,
                    "captured",
                    _NOW - timedelta(hours=1),
                    snapshot_url=_snapshot_url(origin),
                ),
            )
            await session.commit()

        chosen = await _selection(sessions)
    finally:
        await engine.dispose()
    assert chosen == []


# ── AC11: capture attestiert nie ─────────────────────────────────────────


async def test_capture_never_attests(fresh_pg_dsn: str, worm_store: WormStore) -> None:
    """AC11: der Capture-Pfad schreibt nie in ``source_archive``: nach einem
    ``captured`` ist die Quelle weiter unattestiert; das Ergebnis ist nur die
    ``capture_request``-Zeile (Protokoll, kein Beweis)."""
    raw = b"0130 rohbytes der quelle"
    origin = "https://dserver.bundestag.de/0130-ac11.pdf"
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        outcome = await _ingest(sessions, worm_store, raw, origin=origin)
        assert outcome.status == "inserted"
        source_id = outcome.source_id
        assert source_id is not None

        pending = await _selection(sessions)
        assert [c.source_id for c in pending] == [source_id]

        lookup = _NoCandidateLookup()
        wayback = _OkArchiver(_snapshot_url(origin))
        async with sessions() as session:
            result = await capture_source(
                pending[0], session=session, worm=worm_store, lookup=lookup, wayback=wayback
            )
        assert result.status == "captured"
        assert result.snapshot_url == _snapshot_url(origin)
        assert lookup.candidates_calls == 1
        assert wayback.archive_calls == [origin]

        async with sessions() as session:
            requests = (
                await session.execute(
                    text(
                        "SELECT archiver, outcome, snapshot_url, reason "
                        "FROM capture_request WHERE source_id = :s"
                    ),
                    {"s": str(source_id)},
                )
            ).all()
            attestations = await session.scalar(
                text("SELECT count(*) FROM source_archive WHERE source_id = :s"),
                {"s": str(source_id)},
            )
    finally:
        await engine.dispose()
    assert len(requests) == 1
    assert requests[0].archiver == "wayback"
    assert requests[0].outcome == "captured"
    assert requests[0].snapshot_url == _snapshot_url(origin)
    assert requests[0].reason is None
    assert attestations == 0
