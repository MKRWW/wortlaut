"""Parser für Stenografische Berichte des Landtags Sachsen-Anhalt (#145).

Rednerzeile, Kopf und Klassifikation (Fraktion vs. Amtsbezeichnung) nach Spec 0145 §0
und §4.2; Vorlage ist Protokoll 8/118. Importiert nur stdlib und
``wortlaut.ingest.protokoll_parse`` (``segment_speeches``).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from wortlaut.ingest.protokoll_parse import segment_speeches

# Rednerzeile (#145): endet auf '):' bzw. ':' und erlaubt EINEN Zeilenumbruch in der
# Klammer (lange Amtsbezeichnungen brechen real um, Spec 0145 §0). Der Bundestag-Marker
# bleibt davon unberührt (Do-NOT).
SPEAKER_MARKER_ST: re.Pattern[str] = re.compile(
    r"^(?:(?P<name>[^(\n]+?)\s+\((?P<party>[^)\n]+(?:\n[^)\n]+)?)\):$"
    r"|(?P<pres>Vizepräsident(?:in)?|Präsident(?:in)?)\b[^:\n]*:$)",
    re.MULTILINE,
)
# Ziffern-Quantoren begrenzt (S8786), wie in protokoll_parse.py.
_SITZUNG_RE_ST = re.compile(r"(\d{1,4})\. Sitzung, \w+, (\d{2})\.(\d{2})\.(\d{4})")
_BERICHT_RE_ST = re.compile(r"Stenografischer Bericht (\d{1,2}/\d{1,4})")


@dataclass(frozen=True)
class StSpeech:
    """Ein Redebeitrag des Landtags mit Rolle (Amtsbezeichnung) je Beitrag (#145)."""

    verbatim_text: str
    text_start: int
    text_end: int
    name: str
    party: str | None
    role: str | None
    tagesordnungspunkt: str | None


def classify(paren: str) -> tuple[str | None, str | None]:
    """Klammerinhalt → (party, role): Amtsbezeichnung oder Fraktion (Spec 0145 §4.2)."""
    text = " ".join(paren.split())
    lower = text.lower()
    if "minister" in lower or "staatssekret" in lower:
        return None, text
    if text in ("Berichterstatter", "Berichterstatterin"):
        return None, None
    return text, None


def parse_header_st(normalized: str) -> tuple[str, dict[str, object]]:
    """Datum (ISO-String) + locator (Protokoll-/Sitzungsnr.) aus dem Protokoll-Kopf."""
    spoken_at = ""
    locator: dict[str, object] = {}
    m = _SITZUNG_RE_ST.search(normalized)
    if m:
        try:
            day, month, year = int(m.group(2)), int(m.group(3)), int(m.group(4))
            spoken_at = datetime.date(year, month, day).isoformat()
        except ValueError:
            spoken_at = ""
        locator["sitzung"] = m.group(1)
    mb = _BERICHT_RE_ST.search(normalized)
    if mb:
        locator["protokoll"] = mb.group(1)
    return spoken_at, locator


def segment_speeches_st(normalized: str) -> list[StSpeech]:
    """Segmentiert mit ``SPEAKER_MARKER_ST``; je Segment ``classify`` für party/role."""
    speeches: list[StSpeech] = []
    for seg in segment_speeches(normalized, marker=SPEAKER_MARKER_ST):
        party, role = classify(seg.party)
        speeches.append(
            StSpeech(
                verbatim_text=seg.verbatim_text,
                text_start=seg.text_start,
                text_end=seg.text_end,
                name=" ".join(seg.name.split()),
                party=party,
                role=role,
                tagesordnungspunkt=seg.tagesordnungspunkt,
            )
        )
    return speeches
