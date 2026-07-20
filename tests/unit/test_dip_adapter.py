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
from wortlaut.ingest.dip import DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "dip"


def _get_settings() -> DipSettings:
    return DipSettings(
        api_key="test-key",
        api_base_url="https://search.dip.bundestag.de/api/v1",
        pdf_host="dserver.bundestag.de",
    )


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

    mock_response = MagicMock()
    mock_response.json.return_value = payload
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        MockClient.return_value = client_instance
        client_instance.get.return_value = mock_response

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

    # Verifiziere, dass der richtige Endpoint aufgerufen wurde
    client_instance.get.assert_called_once()
    call_args = client_instance.get.call_args
    assert "/plenarprotokoll" in call_args.args[0]


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
    mock_response.content = pdf_bytes
    mock_response.raise_for_status = MagicMock()

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


# ── AC5: normalize und parse werfen NotImplementedError ─────────────────


def test_normalize_and_parse_not_implemented_phase0() -> None:
    settings = _get_settings()
    adapter = DipPlenarprotokollAdapter(settings)

    raw = RawSource(
        origin_url="https://dserver.bundestag.de/btp/21/21800/21800.pdf",
        source_type="plenarprotokoll",
        raw_bytes=b"%PDF-1.0",
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )

    with pytest.raises(NotImplementedError, match="Phase 1"):
        adapter.normalize(raw)

    with pytest.raises(NotImplementedError, match="Phase 1"):
        adapter.parse(raw, "")


# ── AC7: trust_level und Identity ───────────────────────────────────────


def test_trust_level_and_identity() -> None:
    settings = _get_settings()
    adapter = DipPlenarprotokollAdapter(settings)

    assert adapter.trust_level == "verified_primary"
    assert adapter.name == "dip-api"
    assert adapter.version == "1.0.0"
