"""Unit: Landtag-Sachsen-Anhalt-Adapter (Spec 0145, AC1–AC6).

Rein: ``httpx.MockTransport`` hinter dem injizierten Client — kein Netz (R-TEST-03).
Der injizierte Client setzt den User-Agent selbst, weil die Tests den Client über
``_new_client(transport=...)`` bauen und in ``adapter._client`` einsetzen.
Der Anfrage-Zähler ist test-lokal (kein Modul-Zustand).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from wortlaut.ingest.adapter import AdapterError, RawSource, SourceRef
from wortlaut.ingest.landtag_st import (
    LandtagSachsenAnhaltAdapter,
    LandtagStDisabled,
    LandtagStError,
)
from wortlaut.ingest.settings import LandtagStSettings

_Handler = Callable[[httpx.Request], httpx.Response]

_BASE = "https://padoka.landtag.sachsen-anhalt.de/files/plenum"


def _settings(enabled: bool = True) -> LandtagStSettings:
    return LandtagStSettings(
        enabled=enabled,
        base_url=_BASE,
        wahlperiode=8,
        lookback=3,
        contact="https://github.com/MKRWW/wortlaut",
    )


def _adapter(handler: _Handler, *, enabled: bool = True) -> LandtagSachsenAnhaltAdapter:
    adapter = LandtagSachsenAnhaltAdapter(_settings(enabled))
    adapter._client = adapter._new_client(transport=httpx.MockTransport(handler))
    return adapter


def _since() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _ref(url: str) -> SourceRef:
    return SourceRef(origin_url=url, source_type="plenarprotokoll", hint={})


def _wp_number(request: httpx.Request) -> tuple[int, int]:
    """(wahlperiode, nummer) aus dem Pfad einer Anfrage, z. B. ``/wp8/116stzg.pdf``."""
    parts = request.url.path.split("/")
    return int(parts[-2].removeprefix("wp")), int(parts[-1][:3])


# ── AC1: Identity und Rechte-Basis ────────────────────────────────────────


def test_adapter_identity_and_rights() -> None:  # AC1
    adapter = LandtagSachsenAnhaltAdapter(_settings())
    assert adapter.name == "landtag-st"
    assert adapter.version == "1.0.0"
    assert adapter.trust_level == "secondary"
    assert adapter.rights_basis == "amtliches_werk_p5"
    assert adapter.parliament == "landtag-sachsen-anhalt"
    assert adapter.mandate_role == "MdL"


# ── AC2: disabled → Fehler ohne jede Anfrage ──────────────────────────────


async def test_disabled_raises_without_request() -> None:  # AC2
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    since = _since()
    adapter = _adapter(handler, enabled=False)

    with pytest.raises(LandtagStDisabled, match="freigegeben") as excinfo:
        await adapter.discover(since)

    assert isinstance(excinfo.value, AdapterError)
    assert len(requests) == 0


# ── AC3: lookback-Refs, Anfrage-Limit, User-Agent ─────────────────────────


async def test_discover_lookback_refs_and_user_agent() -> None:  # AC3
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        wp, number = _wp_number(request)
        if wp == 8 and number <= 118:
            return httpx.Response(200)
        return httpx.Response(404)

    since = _since()
    adapter = _adapter(handler)

    refs = await adapter.discover(since)

    assert len(requests) <= 30
    assert len(refs) == 3
    assert refs[0].origin_url == f"{_BASE}/wp8/116stzg.pdf"
    assert refs[1].origin_url == f"{_BASE}/wp8/117stzg.pdf"
    assert refs[2].origin_url == f"{_BASE}/wp8/118stzg.pdf"
    for request in requests:
        assert "wortlaut" in request.headers["user-agent"]


# ── AC4: folgende Wahlperiode wird geprüft ────────────────────────────────


async def test_discover_next_wahlperiode_refs() -> None:  # AC4
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        wp, number = _wp_number(request)
        if wp == 8 and number <= 118:
            return httpx.Response(200)
        if wp == 9 and number <= 2:
            return httpx.Response(200)
        return httpx.Response(404)

    since = _since()
    adapter = _adapter(handler)

    refs = await adapter.discover(since)

    assert len(refs) == 5
    assert refs[3].origin_url == f"{_BASE}/wp9/001stzg.pdf"
    assert refs[4].origin_url == f"{_BASE}/wp9/002stzg.pdf"
    assert refs[3].hint["wahlperiode"] == "9"
    assert refs[4].hint["sitzung"] == "2"


# ── AC5: HEAD 500 → LandtagStError ────────────────────────────────────────


async def test_discover_status_500_raises() -> None:  # AC5
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, content=b"ANTWORTTEXT-DARF-NIE-LECKEN-500")

    since = _since()
    adapter = _adapter(handler)

    with pytest.raises(LandtagStError, match="unexpected status 500") as excinfo:
        await adapter.discover(since)

    assert len(requests) == 1
    assert "ANTWORTTEXT" not in str(excinfo.value)


# ── AC6: fetch ────────────────────────────────────────────────────────────


async def test_fetch_foreign_host_without_request() -> None:  # AC6
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    ref = _ref("https://evil.example.com/x.pdf")
    adapter = _adapter(handler)

    with pytest.raises(LandtagStError, match="Host"):
        await adapter.fetch(ref)

    assert len(requests) == 0


async def test_fetch_redirect_raises() -> None:  # AC6
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": "https://evil.example.com/y.pdf"})

    ref = _ref(f"{_BASE}/wp8/118stzg.pdf")
    adapter = _adapter(handler)

    with pytest.raises(LandtagStError, match="redirect"):
        await adapter.fetch(ref)


async def test_fetch_html_instead_of_pdf_raises() -> None:  # AC6
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html>Captcha-Seite, kein PDF</html>",
        )

    ref = _ref(f"{_BASE}/wp8/118stzg.pdf")
    adapter = _adapter(handler)

    with pytest.raises(LandtagStError, match="not a PDF"):
        await adapter.fetch(ref)


async def test_fetch_valid_pdf_returns_rawsource() -> None:  # AC6
    pdf = b"%PDF-1.4\nsynthetische Fixture-Bytes\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=pdf,
        )

    ref = _ref(f"{_BASE}/wp8/118stzg.pdf")
    adapter = _adapter(handler)

    raw = await adapter.fetch(ref)

    assert isinstance(raw, RawSource)
    assert raw.raw_bytes == pdf
    assert raw.mime_type == "application/pdf"
    assert raw.origin_url == ref.origin_url
    assert raw.retrieved_at.tzinfo is not None


# ── Rolle und Partei im Speaker-Hint ────────────────────────────────────────


def test_parse_sets_role_and_party_in_hint() -> None:
    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "landtag_st" / "protokoll.pdf"

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("handler must never be called")

    adapter = _adapter(handler)
    origin_url = f"{_BASE}/wp8/118stzg.pdf"
    raw = RawSource(
        origin_url=origin_url,
        source_type="plenarprotokoll",
        raw_bytes=fixture.read_bytes(),
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )

    normalized = adapter.normalize(raw)
    drafts = adapter.parse(raw, normalized)

    by_name = {draft.speaker_hint["name"]: draft for draft in drafts}

    anna = by_name["Dr. Anna Beispielhaft"]
    assert anna.speaker_hint["role"] == "Ministerin für Inneres und Sport"
    assert anna.speaker_hint["party"] is None

    erika = by_name["Erika Musterfrau"]
    assert erika.speaker_hint["party"] == "AfD"
    assert "role" not in erika.speaker_hint

    for draft in drafts:
        assert draft.spoken_at == "2026-06-26"
        assert draft.permalink == origin_url
