"""Erzeugt die Kontext-Fixture (#51): 2 Tagesordnungspunkte + ein inline-Zwischenruf.

Einspaltig (x=60), deterministisch. Enthält: Kopf, TOP 3 mit einem AfD-Beitrag
(mit inline-Zwischenruf `(Zuruf des Abg. … [SPD]: …)`), TOP 4 mit einem SPD-Beitrag,
plus einen Präsidiums-Marker (→ kein Span). Neu bauen:
    python tests/fixtures/dip/_make_kontext_protokoll.py <ausgabe.pdf>
"""

from __future__ import annotations

import sys

import pymupdf

LINES: list[str] = [
    "Deutscher Bundestag",
    "Stenografischer Bericht",
    "88. Sitzung",
    "Berlin, Freitag, den 5. Juli 2024",
    "Plenarprotokoll 20/88",
    "",
    "Tagesordnungspunkt 3",
    "",
    "Präsident Dr. Wolfgang Beispiel:",
    "Ich rufe Tagesordnungspunkt 3 auf. Das Wort",
    "hat der Abgeordnete Mustermann.",
    "",
    "Abg. Dr. Max Mustermann (AfD):",
    "Sehr geehrter Herr Präsident, wir lehnen",
    "diesen Entwurf ab.",
    "(Zuruf des Abg. Erika Musterfrau [SPD]: Das ist falsch!)",
    "Die Digitalisierung darf so nicht kommen.",
    "",
    "Tagesordnungspunkt 4",
    "",
    "Abg. Erika Musterfrau (SPD):",
    "Frau Präsidentin, dieser Antrag verdient",
    "die Unterstützung des Hauses.",
]


def build(path: str) -> None:
    """Schreibt die Kontext-Fixture nach ``path``."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 55.0
    for line in LINES:
        if line:
            page.insert_text((60, y), line, fontsize=10, fontname="helv")
        y += 15.0
    doc.save(path, deflate=True, garbage=0)
    doc.close()


if __name__ == "__main__":
    build(sys.argv[1])
    print(f"wrote {sys.argv[1]}")
