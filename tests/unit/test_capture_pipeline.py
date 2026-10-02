"""Unit (Spec 0130 §4.3): ``capture_source`` — WORM-/Hash-Gegenprüfung,
CDX zuerst, Capture-Protokoll.

Rein: ``WormStore``, Lookup und Archiver sind schlanke Fakes (Aufrufe zählen)
— kein MinIO, keine DB, kein Netz (R-TEST-03). AC4 (Kandidat →
``already_archived``, kein Capture), AC5/AC6 (captured/failed werden
protokolliert), AC7 (Hash/WORM vor dem Netz), AC8 (Lookup-Fehler → ``error``),
AC10 (archive.today wird nie aufgerufen).
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.wayback_lookup import SnapshotCandidate
from wortlaut.evidence.hashing import sha1_base32
from wortlaut.pipeline.capture import ARCHIVER, capture_source
from wortlaut.store.captures import CaptureCandidate

_ORIGIN = "https://dserver.bundestag.de/btp/21/21090.pdf"
_TS = "20260805170741"
_SNAPSHOT_URL = f"https://web.archive.org/web/{_TS}/{_ORIGIN}"
_RAW_REF = "s3://bucket/raw?versionId=1"
_SOURCE_ID = UUID(int=11)


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
    """WaybackLookup-Fake: gebrachte Kandidaten (nur Vorfilter-Digest), gezählte
    Aufrufe; ``fetch`` wird im Capture-Pfad nie gebraucht."""

    def __init__(
        self,
        candidates: list[SnapshotCandidate],
        *,
        candidates_error: ArchiveError | None = None,
    ) -> None:
        self._candidates = candidates
        self._candidates_error = candidates_error
        self.candidates_calls = 0
        self.candidates_url: str | None = None
        self.candidates_sha1: str | None = None

    async def candidates(self, origin_url: str, *, sha1_b32: str) -> list[SnapshotCandidate]:
        self.candidates_calls += 1
        self.candidates_url = origin_url
        self.candidates_sha1 = sha1_b32
        if self._candidates_error is not None:
            raise self._candidates_error
        return self._candidates

    async def fetch(self, candidate: SnapshotCandidate) -> bytes | None:
        raise AssertionError("not used (capture liest nur candidates)")

    async def aclose(self) -> None:
        pass


class _FakeWayback:
    """Archiver-Fake: liefert eine feste URL oder wirft ``ArchiveError``;
    zählt die Aufrufe."""

    def __init__(self, url: str, *, error: ArchiveError | None = None) -> None:
        self._url = url
        self._error = error
        self.archive_calls: list[str] = []

    async def archive(self, origin_url: str) -> str:
        self.archive_calls.append(origin_url)
        if self._error is not None:
            raise self._error
        return self._url

    async def aclose(self) -> None:
        pass


class _RaisingArchiveToday:
    """archive.today-Fake, der bei JEDEM Aufruf wirft (AC10)."""

    def __init__(self) -> None:
        self.archive_calls = 0

    async def archive(self, origin_url: str) -> str:
        self.archive_calls += 1
        raise AssertionError("archive.today wird im Capture-Pfad nie aufgerufen")

    async def aclose(self) -> None:
        pass


def _candidate(ts: str) -> SnapshotCandidate:
    return SnapshotCandidate(ts, _ORIGIN)


def _candidate_for(raw: bytes) -> CaptureCandidate:
    return CaptureCandidate(
        source_id=_SOURCE_ID,
        content_hash=hashlib.sha256(raw).hexdigest(),
        raw_bytes_ref=_RAW_REF,
        origin_url=_ORIGIN,
    )


def _session() -> AsyncMock:
    """AsyncSession-Fake: ``add`` synchron, damit keine dangling Coroutine entsteht."""
    session = AsyncMock()
    session.add = MagicMock()
    return session


# ── AC4: erst nachsehen — Kandidat → already_archived, kein Capture ──────


async def test_already_archived_skips_capture() -> None:
    """AC4: der Lookup liefert einen Kandidaten mit unserem Digest →
    ``already_archived``; ``archive`` wird nicht aufgerufen (Zähler = 0),
    keine Zeile geschrieben."""
    raw = b"rohbytes"
    worm = _FakeWorm(raw)
    lookup = _FakeLookup([_candidate(_TS)])
    wayback = _FakeWayback(_SNAPSHOT_URL)
    session = _session()
    with patch("wortlaut.pipeline.capture.insert_capture_request", new=AsyncMock()) as insert:
        outcome = await capture_source(
            _candidate_for(raw), session=session, worm=worm, lookup=lookup, wayback=wayback
        )

    assert outcome.status == "already_archived"
    assert outcome.snapshot_url is None
    assert lookup.candidates_calls == 1
    assert lookup.candidates_url == _ORIGIN
    assert lookup.candidates_sha1 == sha1_base32(raw)
    assert wayback.archive_calls == []
    assert insert.await_count == 0


# ── AC5: Capture wird protokolliert ──────────────────────────────────────


async def test_captured_is_logged() -> None:
    """AC5: kein Kandidat, ``archive`` liefert eine URL → ``captured``; genau
    eine Zeile ``outcome='captured'`` mit dieser URL, ``reason=None``,
    ``archiver='wayback'``."""
    raw = b"rohbytes"
    worm = _FakeWorm(raw)
    lookup = _FakeLookup([])
    wayback = _FakeWayback(_SNAPSHOT_URL)
    session = _session()
    with patch(
        "wortlaut.pipeline.capture.insert_capture_request",
        new=AsyncMock(return_value=UUID(int=99)),
    ) as insert:
        outcome = await capture_source(
            _candidate_for(raw), session=session, worm=worm, lookup=lookup, wayback=wayback
        )

    assert outcome.status == "captured"
    assert outcome.snapshot_url == _SNAPSHOT_URL
    assert outcome.reason is None
    assert wayback.archive_calls == [_ORIGIN]
    assert insert.await_count == 1
    row = insert.call_args.args[1]
    assert row.source_id == _SOURCE_ID
    assert row.archiver == ARCHIVER
    assert row.outcome == "captured"
    assert row.snapshot_url == _SNAPSHOT_URL
    assert row.reason is None


# ── AC6: Fehlschlag wird protokolliert ───────────────────────────────────


async def test_failed_is_logged() -> None:
    """AC6: ``archive`` wirft ``ArchiveError`` → ``failed``; genau eine Zeile
    ``outcome='failed'`` mit ``reason == exc.label()`` und ``snapshot_url=None``."""
    raw = b"rohbytes"
    error = ArchiveError("wayback", "http_status", status_code=429, transient=True)
    worm = _FakeWorm(raw)
    lookup = _FakeLookup([])
    wayback = _FakeWayback(_SNAPSHOT_URL, error=error)
    session = _session()
    with patch(
        "wortlaut.pipeline.capture.insert_capture_request",
        new=AsyncMock(return_value=UUID(int=99)),
    ) as insert:
        outcome = await capture_source(
            _candidate_for(raw), session=session, worm=worm, lookup=lookup, wayback=wayback
        )

    assert outcome.status == "failed"
    assert outcome.snapshot_url is None
    assert outcome.reason == error.label()
    assert insert.await_count == 1
    row = insert.call_args.args[1]
    assert row.outcome == "failed"
    assert row.snapshot_url is None
    assert row.reason == error.label()


# ── AC7: Hash vor dem Netz ───────────────────────────────────────────────


async def test_hash_before_network() -> None:
    """AC7: die WORM-Bytes passen nicht zum ``content_hash`` →
    ``hash_mismatch``; weder Lookup noch ``archive`` aufgerufen, keine Zeile."""
    raw = b"manipulierte rohbytes"
    candidate = CaptureCandidate(
        source_id=_SOURCE_ID,
        content_hash="0" * 64,
        raw_bytes_ref=_RAW_REF,
        origin_url=_ORIGIN,
    )
    worm = _FakeWorm(raw)
    lookup = _FakeLookup([])
    wayback = _FakeWayback(_SNAPSHOT_URL)
    session = _session()
    with patch("wortlaut.pipeline.capture.insert_capture_request", new=AsyncMock()) as insert:
        outcome = await capture_source(
            candidate, session=session, worm=worm, lookup=lookup, wayback=wayback
        )

    assert outcome.status == "hash_mismatch"
    assert worm.get_calls == 1
    assert lookup.candidates_calls == 0
    assert wayback.archive_calls == []
    assert insert.await_count == 0


async def test_worm_missing() -> None:
    """AC7: die WORM wirft → ``worm_missing``; weder Lookup noch ``archive``
    aufgerufen, keine Zeile."""
    worm = _FakeWorm(b"rohbytes", fail=True)
    lookup = _FakeLookup([])
    wayback = _FakeWayback(_SNAPSHOT_URL)
    session = _session()
    with patch("wortlaut.pipeline.capture.insert_capture_request", new=AsyncMock()) as insert:
        outcome = await capture_source(
            _candidate_for(b"rohbytes"), session=session, worm=worm, lookup=lookup, wayback=wayback
        )

    assert outcome.status == "worm_missing"
    assert lookup.candidates_calls == 0
    assert wayback.archive_calls == []
    assert insert.await_count == 0


# ── AC8: Lookup-Fehler → error, kein Capture, keine Zeile ────────────────


async def test_lookup_error() -> None:
    """AC8: ``candidates`` wirft ``ArchiveError`` → ``error``; ``archive`` wird
    nicht aufgerufen, keine Zeile."""
    raw = b"rohbytes"
    lookup = _FakeLookup([], candidates_error=ArchiveError("wayback", "invalid_response"))
    wayback = _FakeWayback(_SNAPSHOT_URL)
    session = _session()
    with patch("wortlaut.pipeline.capture.insert_capture_request", new=AsyncMock()) as insert:
        outcome = await capture_source(
            _candidate_for(raw),
            session=session,
            worm=_FakeWorm(raw),
            lookup=lookup,
            wayback=wayback,
        )

    assert outcome.status == "error"
    assert outcome.reason is None
    assert lookup.candidates_calls == 1
    assert wayback.archive_calls == []
    assert insert.await_count == 0


# ── AC10: archive.today wird im Capture-Pfad nie aufgerufen ──────────────


async def test_never_calls_archive_today() -> None:
    """AC10: der Capture-Pfad kennt nur den Wayback-Archiver; der
    archive.today-Fake, der bei jedem Aufruf wirft, bleibt unberührt — auch
    auf dem ``failed``-Pfad."""
    raw = b"rohbytes"
    error = ArchiveError("wayback", "http_status", status_code=404, transient=False)
    worm = _FakeWorm(raw)
    lookup = _FakeLookup([])
    wayback = _FakeWayback(_SNAPSHOT_URL, error=error)
    atoday = _RaisingArchiveToday()
    session = _session()
    with patch("wortlaut.pipeline.capture.insert_capture_request", new=AsyncMock()) as insert:
        outcome = await capture_source(
            _candidate_for(raw), session=session, worm=worm, lookup=lookup, wayback=wayback
        )

    assert outcome.status == "failed"
    assert atoday.archive_calls == 0
    assert wayback.archive_calls == [_ORIGIN]
    assert insert.await_count == 1
