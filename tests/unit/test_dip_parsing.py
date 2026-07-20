"""Unit: DIP Span-Parsing — normalize (PDF→Text) + parse (Text→SpanDraft) (#41).

Rein: gegen eine committete Zweispalten-Fixture, KEIN Live-Call (R-TEST-03).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from wortlaut.ingest.adapter import RawSource
from wortlaut.ingest.dip import DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings

_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "dip" / "plenarprotokoll_zweispaltig.pdf"
)


def _adapter() -> DipPlenarprotokollAdapter:
    return DipPlenarprotokollAdapter(
        DipSettings(
            api_key="test-key",
            api_base_url="https://search.dip.bundestag.de/api/v1",
            pdf_host="dserver.bundestag.de",
        )
    )


def _raw() -> RawSource:
    return RawSource(
        origin_url="https://dserver.bundestag.de/btp/21/21042/2104200.pdf",
        source_type="plenarprotokoll",
        raw_bytes=_FIXTURE.read_bytes(),
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )


def test_normalize_deterministic() -> None:  # AC1
    adapter = _adapter()
    raw = _raw()
    first = adapter.normalize(raw)
    second = adapter.normalize(raw)
    assert first == second  # deterministisch: gleiche Bytes → gleicher Text
    assert "Abg. Dr. Max Mustermann (AfD):" in first


def test_normalize_two_column_order() -> None:  # AC2
    text = _adapter().normalize(_raw())
    i_left = text.index("Mustermann (AfD)")
    i_right = text.index("Musterfrau (SPD)")
    assert 0 < i_left < i_right
    assert text.index("Plenarprotokoll 21/42") < i_left


def test_parse_segments_speeches_excludes_praesidium() -> None:  # AC3
    raw = _raw()
    adapter = _adapter()
    drafts = adapter.parse(raw, adapter.normalize(raw))
    assert len(drafts) == 2
    assert {d.speaker_hint["party"] for d in drafts} == {"AfD", "SPD"}


def test_span_offsets_match_verbatim() -> None:  # AC4 — Kern-Invariante
    raw = _raw()
    adapter = _adapter()
    normalized = adapter.normalize(raw)
    drafts = adapter.parse(raw, normalized)
    assert drafts
    for d in drafts:
        assert normalized[d.text_start : d.text_end] == d.verbatim_text


def test_speaker_hint_name_and_party() -> None:  # AC5
    raw = _raw()
    adapter = _adapter()
    drafts = adapter.parse(raw, adapter.normalize(raw))
    afd = next(d for d in drafts if d.speaker_hint["party"] == "AfD")
    assert afd.speaker_hint["name"] == "Dr. Max Mustermann"
    assert "Gesetzentwurf entschieden ab." in afd.verbatim_text


def test_spoken_at_locator_permalink() -> None:  # AC6
    raw = _raw()
    adapter = _adapter()
    drafts = adapter.parse(raw, adapter.normalize(raw))
    d = drafts[0]
    assert d.spoken_at == "2023-03-15"
    assert d.locator["protokoll"] == "21/42"
    assert d.locator["sitzung"] == "42"
    assert d.permalink == raw.origin_url


def test_normalize_parse_no_longer_notimplemented() -> None:  # AC7
    raw = _raw()
    adapter = _adapter()
    normalized = adapter.normalize(raw)
    drafts = adapter.parse(raw, normalized)
    assert isinstance(normalized, str)
    assert list(drafts)


def test_oversized_pdf_rejected() -> None:  # R-SEC-06
    from wortlaut.ingest.protokoll_parse import MAX_PDF_BYTES

    raw = RawSource(
        origin_url="https://dserver.bundestag.de/x.pdf",
        source_type="plenarprotokoll",
        raw_bytes=b"%PDF-" + b"0" * (MAX_PDF_BYTES + 1),
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )
    adapter = _adapter()
    with pytest.raises(ValueError, match="exceeds"):
        adapter.normalize(raw)


def test_too_many_pages_rejected(monkeypatch: pytest.MonkeyPatch) -> None:  # R-SEC-06
    import wortlaut.ingest.protokoll_parse as pp

    monkeypatch.setattr(pp, "MAX_PDF_PAGES", 0)
    adapter = _adapter()
    raw = _raw()
    with pytest.raises(ValueError, match="pages"):
        adapter.normalize(raw)
