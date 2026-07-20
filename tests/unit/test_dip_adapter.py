"""Unit: DIP-Plenarprotokoll-Adapter (AC1–AC5, AC7).

Rein: httpx wird gemockt, keine Live-Calls.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wortlaut.ingest.adapter import IngestAdapter, RawSource, SourceRef
from wortlaut.ingest.dip import DipFetchError, DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "dip"


def _get_settings() -> DipSettings:
    return DipSettings(
        api_key="test-key",
        api_base_url="https://search.dip.bundestag.de/api/v1",
        pdf_host="dserver.bundestag.de",
    )


def _page_response(payload: dict[str, object]) -> MagicMock:
    """Mock-Response für eine discover-Seite."""
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


# ── AC1: Adapter erfüllt IngestAdapter ──────────────────────────────────


def test_adapter_satisfies_protocol() -> None:
    settings = _get_settings()
    adapter = DipPlenarprotokollAdapter(settings)
    assert isinstance(adapter, IngestAdapter) is True
    assert hasattr(adapter, "name")
    assert hasattr(adapter, "version")
    assert hasattr(adapter, "trust_level")


# ── AC2: discover liefert SourceRefs mit pdf_url ────────────────────────


@pytest.mark.asyncio
async def test_discover_yields_pdf_source_refs() -> None:
    with open(FIXTURES / "discover_plenarprotokoll.json", encoding="utf-8") as f:
        payload = json.load(f)

    # Seite 1 = Fixture-Payload (cursor "cursor-next-12345"),
    # Seite 2 = keine Docs + gleicher Cursor → Stopp
    page2_payload: dict[str, object] = {"documents": [], "cursor": "cursor-next-12345"}

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        MockClient.return_value = client_instance
        client_instance.get.side_effect = [
            _page_response(payload),
            _page_response(page2_payload),
        ]

        settings = _get_settings()
        adapter = DipPlenarprotokollAdapter(settings)
        since = datetime(2023, 10, 1, tzinfo=UTC)

        refs = await adapter.discover(since)

    assert len(refs) == 2
    assert all(isinstance(r, SourceRef) for r in refs)
    assert refs[0].source_type == "plenarprotokoll"
    assert refs[0].origin_url == "https://dserver.bundestag.de/btp/21/21800/21800.pdf"
    assert refs[1].origin_url == "https://dserver.bundestag.de/btp/21/21800/218002.pdf"
    assert refs[0].hint["dokumentnummer"] == "21/8001"
    assert refs[0].hint["dip_id"] == "btp-21-8001"

    assert client_instance.get.call_count == 2
    # Erster Call: richtiger Endpoint + #26-Header-Assertions
    first_call = client_instance.get.call_args_list[0]
    assert "/plenarprotokoll" in first_call.args[0]
    assert "apikey" not in first_call.args[0]
    assert "apikey" not in first_call.kwargs.get("params", {})
    assert first_call.kwargs["headers"]["Authorization"] == "ApiKey test-key"
    # Zweiter Call: Cursor mitgegeben
    second_call = client_instance.get.call_args_list[1]
    assert second_call.kwargs["params"]["cursor"] == "cursor-next-12345"


# ── AC8: API-Key nie in URL/Query (R-SEC-01) ──────────────────────────


@pytest.mark.asyncio
async def test_discover_key_never_in_url_or_query() -> None:
    """#26: Key geht als Authorization-Header, taucht nirgends in URL/Query auf (R-SEC-01)."""
    with open(FIXTURES / "discover_plenarprotokoll.json", encoding="utf-8") as f:
        payload = json.load(f)

    page2_payload: dict[str, object] = {"documents": [], "cursor": "cursor-next-12345"}

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        MockClient.return_value = client_instance
        client_instance.get.side_effect = [
            _page_response(payload),
            _page_response(page2_payload),
        ]

        settings = _get_settings()
        adapter = DipPlenarprotokollAdapter(settings)
        since = datetime(2023, 10, 1, tzinfo=UTC)

        await adapter.discover(since)

    for call_args in client_instance.get.call_args_list:
        # Key nicht im URL-Argument
        assert "test-key" not in call_args.args[0]
        # Key nicht in irgendeinem Query-Parameter-Wert
        for value in call_args.kwargs.get("params", {}).values():
            assert "test-key" not in str(value)
        # Header exakt gesetzt
        assert call_args.kwargs["headers"]["Authorization"] == "ApiKey test-key"


# ── AC3: fetch liefert PDF-Bytes ────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_returns_pdf_bytes() -> None:
    with open(FIXTURES / "sample.pdf", "rb") as f:
        pdf_bytes = f.read()

    ref = SourceRef(
        origin_url="https://dserver.bundestag.de/btp/21/21800/21800.pdf",
        source_type="plenarprotokoll",
        hint={},
    )

    mock_response = MagicMock()
    mock_response.is_redirect = False
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/pdf"}
    mock_response.content = pdf_bytes

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        MockClient.return_value = client_instance
        client_instance.get.return_value = mock_response

        settings = _get_settings()
        adapter = DipPlenarprotokollAdapter(settings)

        raw = await adapter.fetch(ref)

    assert isinstance(raw, RawSource)
    assert raw.raw_bytes == pdf_bytes
    assert raw.mime_type == "application/pdf"
    assert raw.origin_url == ref.origin_url
    assert raw.retrieved_at is not None
    assert raw.retrieved_at.tzinfo is not None


# ── AC4: fetch lehnt fremde Hosts ab ────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_rejects_offhost_ref() -> None:
    ref = SourceRef(
        origin_url="https://evil.example.com/x.pdf",
        source_type="plenarprotokoll",
        hint={},
    )

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        MockClient.return_value = client_instance

        settings = _get_settings()
        adapter = DipPlenarprotokollAdapter(settings)

        with pytest.raises(ValueError, match="not in the allowed set"):
            await adapter.fetch(ref)

        # Kein HTTP-Request wurde gesetzt
        client_instance.get.assert_not_called()


# ── normalize/parse sind ab #41 implementiert (Detail-ACs in test_dip_parsing) ──


def test_normalize_parse_implemented_since_0041() -> None:
    """Beide Methoden werfen kein NotImplementedError mehr; parse ist rein textbasiert."""
    settings = _get_settings()
    adapter = DipPlenarprotokollAdapter(settings)

    raw = RawSource(
        origin_url="https://dserver.bundestag.de/btp/21/21800/21800.pdf",
        source_type="plenarprotokoll",
        raw_bytes=b"%PDF-1.0",
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )

    # parse arbeitet auf bereits normalisiertem Text — kein Marker → keine Drafts.
    assert adapter.parse(raw, "kein Redner-Marker hier") == []


# ── AC7: trust_level und Identity ───────────────────────────────────────


def test_trust_level_and_identity() -> None:
    settings = _get_settings()
    adapter = DipPlenarprotokollAdapter(settings)

    assert adapter.trust_level == "verified_primary"
    assert adapter.name == "dip-api"
    assert adapter.version == "1.0.0"


# ── AC2b: Cursor-Pagination: alle Seiten werden gefolgt ────────────────


@pytest.mark.asyncio
async def test_discover_follows_cursor_all_pages() -> None:
    """Seite 1 = Fixture (2 Docs), Seite 2 = 1 Doc + gleicher Cursor ⇒ Stopp."""
    with open(FIXTURES / "discover_plenarprotokoll.json", encoding="utf-8") as f:
        payload = json.load(f)

    page2_payload: dict[str, object] = {
        "documents": [
            {
                "dokumentnummer": "21/8003",
                "datum": "2023-10-14",
                "wahlperiode": 21,
                "id": "btp-21-8003",
                "fundstelle": {"pdf_url": "https://dserver.bundestag.de/btp/21/21800/218003.pdf"},
            }
        ],
        "cursor": "cursor-next-12345",
    }

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        MockClient.return_value = client_instance
        client_instance.get.side_effect = [
            _page_response(payload),
            _page_response(page2_payload),
        ]

        settings = _get_settings()
        adapter = DipPlenarprotokollAdapter(settings)
        since = datetime(2023, 10, 1, tzinfo=UTC)

        refs = await adapter.discover(since)

    assert len(refs) == 3
    assert refs[2].origin_url == "https://dserver.bundestag.de/btp/21/21800/218003.pdf"
    assert client_instance.get.call_count == 2


# ── AC2c: Cursor-Pagination: Stopp bei unverändertem Cursor ────────────


@pytest.mark.asyncio
async def test_discover_stops_on_stable_cursor() -> None:
    """Seite 1 mit 1 Doc + cursor 'c-stable', Seite 2 = leer + gleicher Cursor."""
    page1_payload: dict[str, object] = {
        "documents": [
            {
                "dokumentnummer": "21/9001",
                "datum": "2024-01-01",
                "wahlperiode": 21,
                "id": "btp-21-9001",
                "fundstelle": {"pdf_url": "https://dserver.bundestag.de/btp/21/21900/21900.pdf"},
            }
        ],
        "cursor": "c-stable",
    }
    page2_payload: dict[str, object] = {"documents": [], "cursor": "c-stable"}

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        MockClient.return_value = client_instance
        client_instance.get.side_effect = [
            _page_response(page1_payload),
            _page_response(page2_payload),
        ]

        settings = _get_settings()
        adapter = DipPlenarprotokollAdapter(settings)
        since = datetime(2024, 1, 1, tzinfo=UTC)

        refs = await adapter.discover(since)

    assert len(refs) == 1
    assert client_instance.get.call_count == 2


# ── AC2d: Fail-loud bei nicht-terminierender Pagination ────────────────


@pytest.mark.asyncio
async def test_discover_raises_on_runaway_pagination() -> None:
    """_MAX_PAGES=5; jeder Call liefert einen neuen Cursor ⇒ DipFetchError."""
    call_count: list[int] = [0]

    def _runaway_response(*_args: object, **_kwargs: object) -> MagicMock:
        call_count[0] += 1
        return _page_response({"documents": [], "cursor": f"c-{call_count[0]}"})

    with (
        patch("wortlaut.ingest.dip._MAX_PAGES", 5),
        patch("httpx.AsyncClient") as MockClient,
    ):
        client_instance = AsyncMock()
        MockClient.return_value = client_instance
        client_instance.get.side_effect = _runaway_response

        settings = _get_settings()
        adapter = DipPlenarprotokollAdapter(settings)
        since = datetime(2024, 1, 1, tzinfo=UTC)

        with pytest.raises(DipFetchError, match="did not terminate"):
            await adapter.discover(since)

    assert client_instance.get.call_count == 5
