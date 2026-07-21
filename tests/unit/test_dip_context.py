"""Unit (#51): Kontext-Verankerung — Tagesordnungspunkt pro Span + Zwischerufe-inline.

Rein: gegen die committete Kontext-Fixture (2 TOPs + inline-Zwischenruf), kein Live-Call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from wortlaut.ingest.adapter import RawSource
from wortlaut.ingest.dip import DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings

_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "dip" / "plenarprotokoll_kontext.pdf"
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
        origin_url="https://dserver.bundestag.de/btp/20/2008800/2008800.pdf",
        source_type="plenarprotokoll",
        raw_bytes=_FIXTURE.read_bytes(),
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )


def test_tagesordnungspunkt_per_span() -> None:  # AC1
    raw = _raw()
    adapter = _adapter()
    drafts = adapter.parse(raw, adapter.normalize(raw))
    assert len(drafts) == 2
    afd = next(d for d in drafts if d.speaker_hint["party"] == "AfD")
    spd = next(d for d in drafts if d.speaker_hint["party"] == "SPD")
    # Verschiedene Beiträge → korrekt verschiedene TOPs.
    assert afd.locator["tagesordnungspunkt"] == "3"
    assert spd.locator["tagesordnungspunkt"] == "4"


def test_inline_zwischenruf_bleibt_im_span() -> None:  # AC2
    raw = _raw()
    adapter = _adapter()
    drafts = adapter.parse(raw, adapter.normalize(raw))
    # Kein zusätzlicher Sprecher-Span durch den inline-Zuruf (Anzahl unverändert).
    assert len(drafts) == 2
    afd = next(d for d in drafts if d.speaker_hint["party"] == "AfD")
    # Der Zuruf bleibt Teil des verbatim_text desselben Beitrags.
    assert "(Zuruf des Abg. Erika Musterfrau [SPD]: Das ist falsch!)" in afd.verbatim_text


def test_locator_keeps_header_fields() -> None:  # AC3: bestehende Felder + neu TOP
    raw = _raw()
    adapter = _adapter()
    drafts = adapter.parse(raw, adapter.normalize(raw))
    loc = drafts[0].locator
    assert loc["protokoll"] == "20/88"
    assert loc["sitzung"] == "88"
    assert "tagesordnungspunkt" in loc


def test_offset_invariant_with_interjection() -> None:  # AC4 (beweiskritisch)
    raw = _raw()
    adapter = _adapter()
    normalized = adapter.normalize(raw)
    drafts = adapter.parse(raw, normalized)
    assert drafts
    for d in drafts:
        assert normalized[d.text_start : d.text_end] == d.verbatim_text
