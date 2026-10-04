"""Erzeugt die deterministische Protokoll-PDF-Fixture des Landtags Sachsen-Anhalt (#145).

Einspaltig: alle Zeilen bei x=60, Zeilenabstand 14, Seite A4, bei Bedarf zweite Seite.
Zeilen exakt wie in Spec 0145 §11; synthetisch, keine echten Personennamen (§7).
Enthält: Kopf (Protokoll-Nr. + Datum), einen TOP-Header, einen Präsidiums-Marker
(→ kein SpanDraft), vier Sprecher-Marker, davon einer mit umbrochener
Amtsbezeichnung (AC8) und einen Zwischenruf (AC9). Neu bauen:
    python tests/fixtures/landtag_st/_make_protokoll.py <ausgabe.pdf>
"""

from __future__ import annotations

import sys

import pymupdf

LINES: list[str] = [
    "LANDTAG VON SACHSEN-ANHALT",
    "Stenografischer Bericht 8/118",
    "118. Sitzung, Freitag, 26.06.2026",
    "Tagesordnungspunkt 27",
    "Präsident Dr. Paul Beispiel:",
    "Ich eröffne die Sitzung.",
    "Max Mustermann (Berichterstatter):",
    "Der Ausschuss empfiehlt die Annahme.",
    "Erika Musterfrau (AfD):",
    "Wir lehnen den Antrag ab.",
    "(Beifall bei der AfD)",
    "Das ist unsere Haltung.",
    "Dr. Anna Beispielhaft (Ministerin für Inneres",
    "und Sport):",
    "Die Landesregierung sieht das anders.",
    "Karl Probe (Minister für Finanzen):",
    "Der Haushalt trägt das.",
]


def build(path: str) -> None:
    """Schreibt die einstielige Protokoll-Fixture nach ``path``."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4
    y = 60.0
    for line in LINES:
        if y > 800.0:  # bei Bedarf zweite Seite
            page = doc.new_page(width=595, height=842)
            y = 60.0
        page.insert_text((60.0, y), line, fontsize=10, fontname="helv")
        y += 14.0
    doc.save(path, deflate=True, garbage=0)
    doc.close()


if __name__ == "__main__":
    build(sys.argv[1])
    print(f"wrote {sys.argv[1]}")
