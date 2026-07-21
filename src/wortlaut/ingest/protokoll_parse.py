"""Reine Parse-Helfer für Bundestag-Plenarprotokolle (PDF→Text + Segmentierung).

Kein wortlaut-Import; einzige I/O ist das PDF-Byte-Parsing über pymupdf,
gehärtet mit Größen-/Seitenlimit (R-SEC-06). DETERMINISTISCH: gleiche Bytes →
gleicher Text → stabile Offsets (Grundlage der Offset-Invariante, #41).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Any

import pymupdf

MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MiB — Zip-Bomb-/DoS-Schranke
MAX_PDF_PAGES = 500  # Plenarprotokolle sind < 300 Seiten

# 'Abg. <Name> (<Fraktion>):' ODER Präsidiums-Marker '<Präsident...>:'.
# ^ + MULTILINE (#51): Marker beginnen eine Zeile — inline-Zwischenrufe
# '(Zuruf des Abg. X [SPD]: …)' stehen mitten in der Zeile und werden dadurch
# NIE als neuer Sprecher-Turn gelesen (bleiben Teil des verbatim_text, AC2).
SPEAKER_MARKER = re.compile(
    r"^(?:Abg\.\s+(?P<name>[^(\n]+?)\s+\((?P<party>[^)\n]+)\):"
    r"|(?P<pres>Vizepräsident(?:in)?|Präsident(?:in)?)\b[^:\n]*:)",
    re.MULTILINE,
)

_MONTHS = {
    "Januar": 1,
    "Februar": 2,
    "März": 3,
    "April": 4,
    "Mai": 5,
    "Juni": 6,
    "Juli": 7,
    "August": 8,
    "September": 9,
    "Oktober": 10,
    "November": 11,
    "Dezember": 12,
}
_DATE_RE = re.compile(r"den (\d{1,2})\. (\w+) (\d{4})")
# Ziffern-Quantoren begrenzt: unbegrenztes \d+ vor einem Nicht-Ziffer-Literal
# backtrackt sonst quadratisch (S8786). Sitzungs-/Protokollnummern sind klein.
_PROTOKOLL_RE = re.compile(r"Plenarprotokoll (\d{1,3}/\d{1,4})")
_SITZUNG_RE = re.compile(r"(\d{1,4})\. Sitzung")
# Tagesordnungspunkt-/Zusatzpunkt-Header beginnen eine Zeile (Struktur des Protokolls).
_TOP_HEADER = re.compile(r"^(?:Tagesordnungspunkt|Zusatzpunkt)\s+(\d{1,3})", re.MULTILINE)
_HYPHEN_BREAK = re.compile(r"-\n(\w)")
_PAGENUM_LINE = re.compile(r"\s*\d+\s*")
_INLINE_WS = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class SpeechSegment:
    """Ein Redebeitrag als Substring von ``normalized`` (Offset-invariant)."""

    verbatim_text: str
    text_start: int
    text_end: int
    name: str
    party: str
    tagesordnungspunkt: str | None  # aktueller TOP für die Position dieses Beitrags (#51)


def extract_text(raw_bytes: bytes) -> str:
    """PDF-Bytes → deterministischer, spaltenbewusster kanonischer Klartext.

    Gehärtet (R-SEC-06): Byte- und Seitenlimit, kein Ausführen aktiver Inhalte.
    """
    if len(raw_bytes) > MAX_PDF_BYTES:
        raise ValueError(f"PDF exceeds {MAX_PDF_BYTES} bytes — refusing to parse")
    # pymupdf-Objekte am Rand als Any behandeln (unvollständige Upstream-Stubs).
    doc: Any = pymupdf.open(stream=raw_bytes, filetype="pdf")
    try:
        if doc.page_count > MAX_PDF_PAGES:
            raise ValueError(f"PDF has {doc.page_count} pages (> {MAX_PDF_PAGES})")
        pages = [_page_text_columnwise(page) for page in doc]
    finally:
        doc.close()
    return _canonicalize("\n".join(pages))


def _page_text_columnwise(page: Any) -> str:
    """Eine Seite in Spalten-Lesereihenfolge (linke Spalte komplett, dann rechte)."""
    blocks = page.get_text("blocks")  # (x0, y0, x1, y1, text, no, type)
    text_blocks = [b for b in blocks if b[6] == 0]
    mid = page.rect.width / 2
    left = sorted((b for b in text_blocks if b[0] < mid), key=lambda b: b[1])
    right = sorted((b for b in text_blocks if b[0] >= mid), key=lambda b: b[1])
    return "".join(b[4] for b in (*left, *right))


def _canonicalize(text: str) -> str:
    """Silbentrennung zusammenführen, Seitenzahlen weg, Whitespace vereinheitlichen."""
    text = _HYPHEN_BREAK.sub(r"\1", text)
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [ln for ln in lines if not _PAGENUM_LINE.fullmatch(ln)]
    out = _INLINE_WS.sub(" ", "\n".join(lines))
    out = _BLANKS.sub("\n\n", out)
    return out.strip() + "\n"


def parse_header(normalized: str) -> tuple[str, dict[str, object]]:
    """Datum (ISO-String) + locator (Protokoll-/Sitzungsnr.) aus dem Protokoll-Kopf."""
    spoken_at = ""
    m = _DATE_RE.search(normalized)
    if m and m.group(2) in _MONTHS:
        spoken_at = datetime.date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1))).isoformat()
    locator: dict[str, object] = {}
    mp = _PROTOKOLL_RE.search(normalized)
    if mp:
        locator["protokoll"] = mp.group(1)
    ms = _SITZUNG_RE.search(normalized)
    if ms:
        locator["sitzung"] = ms.group(1)
    return spoken_at, locator


def _top_before(offset: int, tops: list[tuple[int, str]]) -> str | None:
    """Der letzte TOP-Header VOR ``offset`` (die Debatte, unter der der Beitrag steht)."""
    current: str | None = None
    for pos, label in tops:
        if pos >= offset:
            break
        current = label
    return current


def segment_speeches(normalized: str) -> list[SpeechSegment]:
    """Segmentiert an Redner-Markern; Präsidium ausgeschlossen (MVP).

    Für jeden Beitrag gilt strikt ``normalized[text_start:text_end] == verbatim_text``.
    Jeder Beitrag trägt den Tagesordnungspunkt seiner Position (#51).
    """
    tops = [(m.start(), m.group(1)) for m in _TOP_HEADER.finditer(normalized)]
    markers = list(SPEAKER_MARKER.finditer(normalized))
    segments: list[SpeechSegment] = []
    for i, m in enumerate(markers):
        if m.group("pres"):
            continue
        start = m.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(normalized)
        while start < end and normalized[start].isspace():
            start += 1
        while end > start and normalized[end - 1].isspace():
            end -= 1
        segments.append(
            SpeechSegment(
                verbatim_text=normalized[start:end],
                text_start=start,
                text_end=end,
                name=(m.group("name") or "").strip(),
                party=(m.group("party") or "").strip(),
                tagesordnungspunkt=_top_before(start, tops),
            )
        )
    return segments
