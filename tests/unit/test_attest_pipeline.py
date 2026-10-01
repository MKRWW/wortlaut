"""Unit (Spec 0124): ``attest_source`` — WORM-/Hash-Gegenprüfung, Kandidaten,
Ergebnistypen.

Rein: ``WormStore``, Lookup und Session sind schlanke Fakes (Aufrufe zählen)
— kein MinIO, keine DB, kein Netz (R-TEST-03). AC8 (SHA-1 passt, SHA-256 nicht
→ ``bytes_mismatch``), AC9 (hash_mismatch/worm_missing → Lookup NICHT
aufgerufen), AC10 (Sortierung nach Abstand, Obergrenze, erster Treffer),
AC11 (Ergebnistypen), §4.4 (8) (UNIQUE-Race → Rollback, trotzdem attested).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.wayback_lookup import SnapshotCandidate, snapshot_url
from wortlaut.pipeline.attest import attest_source
from wortlaut.store.attestations import PendingAttestation

_ORIGIN = "https://dserver.bundestag.de/btp/21/21090.pdf"
_TS = "20260805170741"


class _FakeWorm:
    """WormStore-Fake: get liefert feste Bytes oder wirft; put wird nie gebraucht."""

    def __init__(self, data: bytes, *, fail: bool = False) -> None:
        self._data = data
        self._fail = fail
        self.get_calls = 0

    async def ensure_bucket(self) -> None:
        raise AssertionError("not used")

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        raise AssertionError("not used")

    async def get(self, ref: str) -> bytes:
        self.get_calls += 1
        if self._fail:
            raise RuntimeError("WORM nicht erreichbar")
        return self._data


class _FakeLookup:
    """WaybackLookup-Fake: gebrachte Kandidaten/Bytes, gezählte Aufrufe."""

    def __init__(
        self,
        candidates: list[SnapshotCandidate],
        *,
        bytes_by_ts: dict[str, bytes | None] | None = None,
        candidates_error: ArchiveError | None = None,
        fetch_error: ArchiveError | None = None,
    ) -> None:
        self._candidates = candidates
        self._bytes_by_ts = bytes_by_ts if bytes_by_ts is not None else {}
        self._candidates_error = candidates_error
        self._fetch_error = fetch_error
        self.candidates_calls = 0
        self.candidates_url: str | None = None
        self.candidates_sha1: str | None = None
        self.fetch_calls: list[SnapshotCandidate] = []

    async def candidates(self, origin_url: str, *, sha1_b32: str) -> list[SnapshotCandidate]:
        self.candidates_calls += 1
        self.candidates_url = origin_url
        self.candidates_sha1 = sha1_b32
        if self._candidates_error is not None:
            raise self._candidates_error
        return self._candidates

    async def fetch(self, candidate: SnapshotCandidate) -> bytes | None:
        self.fetch_calls.append(candidate)
        if self._fetch_error is not None:
            raise self._fetch_error
        return self._bytes_by_ts.get(candidate.timestamp)

    async def aclose(self) -> None:
        pass


def _candidate(ts: str) -> SnapshotCandidate:
    return SnapshotCandidate(ts, _ORIGIN)


def _pending(content_hash: str) -> PendingAttestation:
    return PendingAttestation(
        source_id=UUID(int=11),
        content_hash=content_hash,
        raw_bytes_ref="s3://bucket/raw?versionId=1",
        origin_url=_ORIGIN,
        retrieved_at=datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC),
    )


def _session() -> AsyncMock:
    """AsyncSession-Fake: ``add`` synchron, damit keine dangling Coroutine entsteht."""
    session = AsyncMock()
    session.add = MagicMock()
    return session


# ── AC8: SHA-1 passt, SHA-256 nicht → bytes_mismatch ─────────────────────


async def test_sha1_match_sha256_mismatch_is_bytes_mismatch() -> None:
    """AC8: Kandidat mit passendem SHA-1, dessen Bytes einen anderen SHA-256
    haben → keine Zeile, Status bytes_mismatch."""
    raw = b"ledger-bytes"
    worm = _FakeWorm(raw)
    lookup = _FakeLookup([_candidate(_TS)], bytes_by_ts={_TS: b"andere bytes"})
    session = _session()
    with patch("wortlaut.pipeline.attest.insert_source_archive", new=AsyncMock()) as insert:
        outcome = await attest_source(
            _pending(hashlib.sha256(raw).hexdigest()),
            session=session,
            worm=worm,
            lookup=lookup,
        )

    assert outcome.status == "bytes_mismatch"
    assert outcome.snapshot_url is None
    assert insert.await_count == 0
    assert lookup.fetch_calls == [_candidate(_TS)]


# ── AC9: Hash-Gegenprüfung VOR dem Netzaufruf ────────────────────────────


async def test_hash_mismatch_never_calls_lookup() -> None:
    """AC9: WORM-Bytes passen NICHT zum content_hash → hash_mismatch,
    Lookup NICHT aufgerufen (Zähler = 0), keine Zeile."""
    raw = b"manipulierte rohbytes"
    worm = _FakeWorm(raw)
    lookup = _FakeLookup([])
    session = _session()
    with patch("wortlaut.pipeline.attest.insert_source_archive", new=AsyncMock()) as insert:
        outcome = await attest_source(_pending("0" * 64), session=session, worm=worm, lookup=lookup)

    assert outcome.status == "hash_mismatch"
    assert lookup.candidates_calls == 0
    assert lookup.fetch_calls == []
    assert insert.await_count == 0
    assert worm.get_calls == 1


async def test_worm_missing() -> None:
    """AC9: WORM wirft → worm_missing, Lookup NICHT aufgerufen, keine Zeile."""
    worm = _FakeWorm(b"rohbytes", fail=True)
    lookup = _FakeLookup([])
    session = _session()
    with patch("wortlaut.pipeline.attest.insert_source_archive", new=AsyncMock()) as insert:
        outcome = await attest_source(
            _pending(hashlib.sha256(b"rohbytes").hexdigest()),
            session=session,
            worm=worm,
            lookup=lookup,
        )

    assert outcome.status == "worm_missing"
    assert lookup.candidates_calls == 0
    assert lookup.fetch_calls == []
    assert insert.await_count == 0


# ── AC10: Sortierung nach Abstand, Obergrenze, erster Treffer ────────────


async def test_candidates_ordered_and_capped() -> None:
    """AC10: Kandidaten werden nach Abstand zu ``retrieved_at`` geprüft
    (nächster zuerst), nach ``max_candidates`` Abrufen ist Schluss, der erste
    passende wird festgehalten (SHA-256-Gleichheit)."""
    raw = b"ledger-bytes"
    worm = _FakeWorm(raw)
    digest = hashlib.sha256(raw).hexdigest()

    # retrieved_at = 12:00; Abstände: b 30 min, c 1 h, d 1 h, a 2 h —
    # übergeben in einer NICHT sortierten Reihenfolge.
    a = _candidate("20260805100000")
    b = _candidate("20260805113000")
    c = _candidate("20260805110000")
    d = _candidate("20260805130000")

    # (1) Sortierung + erster Treffer: b liefert None, c liefert die
    #     passenden Bytes → attested; d und a werden nicht angefragt.
    lookup = _FakeLookup([a, b, c, d], bytes_by_ts={b.timestamp: None, c.timestamp: raw})
    session = _session()
    with patch(
        "wortlaut.pipeline.attest.insert_source_archive",
        new=AsyncMock(return_value=UUID(int=99)),
    ) as insert:
        outcome = await attest_source(_pending(digest), session=session, worm=worm, lookup=lookup)

    assert outcome.status == "attested"
    assert outcome.snapshot_url == snapshot_url(c)
    assert lookup.fetch_calls == [b, c]
    assert insert.await_count == 1
    row = insert.call_args.args[1]
    assert row.source_id == _pending(digest).source_id
    assert row.archiver == "wayback"
    assert row.snapshot_url == snapshot_url(c)
    assert row.snapshot_at == datetime(2026, 8, 5, 11, 0, 0, tzinfo=UTC)
    assert row.verified_sha256 == digest

    # (2) Obergrenze: alle Kandidaten im Limit liefern None →
    #     snapshot_unavailable; der vierte (weiteste) wird nicht angefragt.
    lookup2 = _FakeLookup([a, b, c, d], bytes_by_ts={})
    session2 = _session()
    with patch(
        "wortlaut.pipeline.attest.insert_source_archive",
        new=AsyncMock(return_value=UUID(int=99)),
    ) as insert2:
        outcome2 = await attest_source(
            _pending(digest),
            session=session2,
            worm=worm,
            lookup=lookup2,
            max_candidates=3,
        )

    assert outcome2.status == "snapshot_unavailable"
    assert [x.timestamp for x in lookup2.fetch_calls] == [
        b.timestamp,
        c.timestamp,
        d.timestamp,
    ]
    assert a not in lookup2.fetch_calls
    assert insert2.await_count == 0


# ── AC11: Ergebnisarten ──────────────────────────────────────────────────


async def test_outcomes_no_match_unavailable_error() -> None:
    """AC11: keine Kandidaten → no_matching_snapshot; alle fetch → None →
    snapshot_unavailable; ArchiveError aus candidates bzw. fetch → error."""
    raw = b"rohbytes"
    digest = hashlib.sha256(raw).hexdigest()
    session = _session()

    # (1) Keine Kandidaten — Befund über die Quelle, kein Fehler.
    lookup = _FakeLookup([])
    with patch("wortlaut.pipeline.attest.insert_source_archive", new=AsyncMock()):
        outcome = await attest_source(
            _pending(digest), session=session, worm=_FakeWorm(raw), lookup=lookup
        )
    assert outcome.status == "no_matching_snapshot"
    assert lookup.fetch_calls == []

    # (2) Alle fetch → None.
    candidate = _candidate(_TS)
    lookup2 = _FakeLookup([candidate], bytes_by_ts={candidate.timestamp: None})
    with patch("wortlaut.pipeline.attest.insert_source_archive", new=AsyncMock()):
        outcome2 = await attest_source(
            _pending(digest), session=session, worm=_FakeWorm(raw), lookup=lookup2
        )
    assert outcome2.status == "snapshot_unavailable"
    assert lookup2.fetch_calls == [candidate]

    # (3) ArchiveError aus dem fetch → error.
    http_503 = ArchiveError("wayback", "http_status", status_code=503, transient=True)
    lookup3 = _FakeLookup([candidate], fetch_error=http_503)
    with patch("wortlaut.pipeline.attest.insert_source_archive", new=AsyncMock()):
        outcome3 = await attest_source(
            _pending(digest), session=session, worm=_FakeWorm(raw), lookup=lookup3
        )
    assert outcome3.status == "error"

    # (4) ArchiveError aus der candidates-Suche → error, kein fetch.
    lookup4 = _FakeLookup([candidate], candidates_error=ArchiveError("wayback", "invalid_response"))
    with patch("wortlaut.pipeline.attest.insert_source_archive", new=AsyncMock()):
        outcome4 = await attest_source(
            _pending(digest), session=session, worm=_FakeWorm(raw), lookup=lookup4
        )
    assert outcome4.status == "error"
    assert lookup4.fetch_calls == []


# ── §4.4 (8): UNIQUE-Race → Rollback, trotzdem attested ─────────────────


async def test_unique_race_rolls_back_and_still_attested() -> None:
    """Ein paralleler Lauf war schneller: IntegrityError (UNIQUE) beim Insert →
    Rollback der eigenen Zeile, das Ergebnis bleibt attested."""
    raw = b"rohbytes"
    digest = hashlib.sha256(raw).hexdigest()
    candidate = _candidate(_TS)
    worm = _FakeWorm(raw)
    lookup = _FakeLookup([candidate], bytes_by_ts={candidate.timestamp: raw})
    session = _session()
    duplicate_key = RuntimeError("duplicate key")
    with patch(
        "wortlaut.pipeline.attest.insert_source_archive",
        new=AsyncMock(side_effect=IntegrityError("INSERT INTO source_archive", {}, duplicate_key)),
    ):
        outcome = await attest_source(_pending(digest), session=session, worm=worm, lookup=lookup)

    assert outcome.status == "attested"
    assert outcome.snapshot_url == snapshot_url(candidate)
    assert session.rollback.await_count == 1
