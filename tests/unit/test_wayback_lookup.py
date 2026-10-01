"""Unit (Spec 0124): ``HttpWaybackLookup`` — CDX-Filter, exakter Abruf, Größengrenze.

Rein: ``httpx.MockTransport`` hinter dem injizierten Client (Test-Seam) —
kein Netz, kein DNS (R-TEST-03). AC4 (Revisits zählen, Rest gefiltert),
AC5 (keine Umleitung, genau eine Anfrage), AC6 (Memento-Datetime),
AC7 (Größengrenze, höchstens ``max_bytes + 1`` Bytes gelesen).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.wayback_lookup import HttpWaybackLookup, SnapshotCandidate, snapshot_url
from wortlaut.evidence.hashing import sha1_base32

_ORIGIN = "https://dserver.bundestag.de/btp/21/21090.pdf"
_HTTP_VARIANT = "http://dserver.bundestag.de/btp/21/21090.pdf"
_OTHER_PATH = "https://dserver.bundestag.de/btp/21/21091.pdf"
_TS = "20260805170741"
_MEMENTO = "Wed, 05 Aug 2026 17:07:41 GMT"

_Handler = Callable[[httpx.Request], httpx.Response]


def _candidate() -> SnapshotCandidate:
    return SnapshotCandidate(_TS, _ORIGIN)


def _client(handler: _Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _digest(data: bytes) -> str:
    return sha1_base32(data)


async def test_candidates_include_revisit_and_filter_rest() -> None:
    """AC4: Zeile mit Status '-' (Revisit) und passendem Digest ist Kandidat;
    anderer Digest, Status 404/302 oder anderer Pfad sind es nicht. Schema-
    Abweichung (http/https) zählt; die Query-Parameter sind wie in §11."""
    fixture = b"rohbytes 21/90"
    digest = _digest(fixture)
    rows = [
        [_TS, _ORIGIN, "-", digest, "warc/revisit"],
        ["20260805170700", _ORIGIN, "200", "A" * 32, "application/pdf"],
        ["20260804170741", _ORIGIN, "404", digest, "application/pdf"],
        ["20260803170741", _ORIGIN, "302", digest, "application/pdf"],
        ["20260802170741", _OTHER_PATH, "200", digest, "application/pdf"],
        ["20260801170741", _HTTP_VARIANT, "200", digest, "application/pdf"],
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["url"] == _ORIGIN
        assert request.url.params["output"] == "json"
        assert request.url.params["fl"] == "timestamp,original,statuscode,digest,mimetype"
        return httpx.Response(
            200, json=[["timestamp", "original", "statuscode", "digest", "mimetype"]] + rows
        )

    lookup = HttpWaybackLookup(client=_client(handler))
    try:
        candidates = await lookup.candidates(_ORIGIN, sha1_b32=digest)
    finally:
        await lookup.aclose()

    # Revisit und http-Variante bleiben, in CDX-Reihenfolge; der Rest wird verworfen.
    assert [c.timestamp for c in candidates] == [_TS, "20260801170741"]
    assert candidates[0].original == _ORIGIN
    assert candidates[1].original == _HTTP_VARIANT


async def test_candidates_empty_response() -> None:
    """Leere Antwort oder nur Kopfzeile → [] (keine Kandidaten, kein Fehler)."""

    def handler_empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    def handler_header_only(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[["timestamp", "original", "statuscode", "digest", "mimetype"]]
        )

    for handler in (handler_empty, handler_header_only):
        lookup = HttpWaybackLookup(client=_client(handler))
        try:
            assert await lookup.candidates(_ORIGIN, sha1_b32="A" * 32) == []
        finally:
            await lookup.aclose()


async def test_candidates_non_json_is_permanent_error() -> None:
    """Nicht-JSON-Antwort → ArchiveError, permanent, reason='invalid_response'."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>kein json</html>")

    lookup = HttpWaybackLookup(client=_client(handler), attempts=1)
    try:
        with pytest.raises(ArchiveError) as exc_info:
            await lookup.candidates(_ORIGIN, sha1_b32="A" * 32)
    finally:
        await lookup.aclose()

    assert exc_info.value.service == "wayback"
    assert exc_info.value.reason == "invalid_response"
    assert exc_info.value.transient is False


async def test_fetch_never_follows_redirect() -> None:
    """AC5: 302 → None; die Location wird NICHT angefragt (Mock-Transport = 1 Call)."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"location": f"https://web.archive.org/web/20260928123406id_/{_ORIGIN}"},
        )

    lookup = HttpWaybackLookup(client=_client(handler))
    try:
        assert await lookup.fetch(_candidate()) is None
    finally:
        await lookup.aclose()
    assert calls == 1


async def test_fetch_rejects_wrong_memento_datetime() -> None:
    """AC6: 200 mit abweichendem oder fehlendem Memento-Datetime → None."""

    def handler_wrong(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"rohbytes",
            headers={"memento-datetime": "Wed, 04 Aug 2026 17:07:41 GMT"},
        )

    def handler_missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"rohbytes")

    for handler in (handler_wrong, handler_missing):
        lookup = HttpWaybackLookup(client=_client(handler))
        try:
            assert await lookup.fetch(_candidate()) is None
        finally:
            await lookup.aclose()


async def test_fetch_returns_bytes_for_exact_memento() -> None:
    """200 + exakt passendes Memento-Datetime → Rohbytes; die Anfrage ist ein
    exakter ``id_``-Abruf (Pfad ``/web/<ts>id_/<original>``)."""
    fixture = b"rohbytes 21/90"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"https://web.archive.org/web/{_TS}id_/{_ORIGIN}"
        # stream= (nicht content=): in httpx 0.28 ist content= bereits
        # "consumed" — aiter_raw() im Streaming-Pfad würde StreamConsumed werfen.
        memento = {"memento-datetime": _MEMENTO}
        return httpx.Response(200, stream=httpx.ByteStream(fixture), headers=memento)

    lookup = HttpWaybackLookup(client=_client(handler))
    try:
        assert await lookup.fetch(_candidate()) == fixture
    finally:
        await lookup.aclose()


class _CountingStream(httpx.AsyncByteStream):
    """Byte-Stream, der die ausgegebenen Bytes zählt (AC7)."""

    def __init__(self, data: bytes, chunk: int, handed: list[int]) -> None:
        self._data = data
        self._chunk = chunk
        self._pos = 0
        self.handed = handed

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        if self._pos >= len(self._data):
            raise StopAsyncIteration
        part = self._data[self._pos : self._pos + self._chunk]
        self._pos += len(part)
        self.handed.append(len(part))
        return part

    async def aclose(self) -> None:
        pass


async def test_fetch_size_limit() -> None:
    """AC7: Antwort über ``max_bytes`` → ArchiveError ``reason='too_large'``
    (permanent); ausgegeben wurden höchstens ``max_bytes + 1`` Bytes."""
    max_bytes = 100
    handed: list[int] = []
    body = bytes(range(256)) * 2

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_CountingStream(body, chunk=1, handed=handed),
            headers={"memento-datetime": _MEMENTO},
        )

    lookup = HttpWaybackLookup(client=_client(handler), max_bytes=max_bytes)
    candidate = _candidate()
    try:
        with pytest.raises(ArchiveError) as exc_info:
            await lookup.fetch(candidate)
    finally:
        await lookup.aclose()

    assert exc_info.value.service == "wayback"
    assert exc_info.value.reason == "too_large"
    assert exc_info.value.transient is False
    assert sum(handed) <= max_bytes + 1


def test_snapshot_url_is_human_address() -> None:
    """Die festgehaltene Adresse ist die ``id_``-freie URL aus §3."""
    candidate = _candidate()
    assert snapshot_url(candidate) == f"https://web.archive.org/web/{_TS}/{_ORIGIN}"
    assert "id_" not in snapshot_url(candidate)
