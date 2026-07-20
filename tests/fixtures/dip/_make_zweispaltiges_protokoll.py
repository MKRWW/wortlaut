"""Erzeugt die deterministische, ZWEISPALTIGE Plenarprotokoll-PDF-Fixture (#41).

Legt Text an festen Koordinaten in zwei Spalten ab (linke x=60, rechte x=320) →
reproduzierbar. Die *korrekte* Lesereihenfolge ist links-komplett-dann-rechts;
eine naiv y-sortierte Extraktion würde die Spalten interleaven (AC2).

Enthält: Protokoll-Kopf (Datum + Nr.), einen Präsidiums-Marker (→ kein SpanDraft),
zwei Abg.-Marker (AfD + SPD). Neu bauen:
    python tests/fixtures/dip/_make_zweispaltiges_protokoll.py <ausgabe.pdf>
"""

from __future__ import annotations

import sys

import pymupdf

# Reihenfolge, in der der Extraktor lesen MUSS (linke Spalte zuerst).
LEFT: list[str] = [
    "Deutscher Bundestag",
    "Stenografischer Bericht",
    "42. Sitzung",
    "Berlin, Donnerstag, den 15. März 2023",
    "Plenarprotokoll 21/42",
    "",
    "Präsident Dr. Wolfgang Beispiel:",
    "Ich eröffne die Sitzung. Das Wort",
    "hat die Abgeordnete Musterfrau.",
    "",
    "Abg. Dr. Max Mustermann (AfD):",
    "Sehr geehrter Herr Präsident, meine",
    "Damen und Herren, wir lehnen diesen",
    "Gesetzentwurf entschieden ab.",
]
RIGHT: list[str] = [
    "Abg. Erika Musterfrau (SPD):",
    "Frau Präsidentin, der vorliegende",
    "Antrag verdient die Unterstützung",
    "dieses hohen Hauses ohne Vorbehalt.",
    "",
    "Es ist ein guter Tag für die",
    "parlamentarische Demokratie.",
]


def build(path: str) -> None:
    """Schreibt die Zweispalten-Fixture nach ``path``."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # A4
    for column, x in ((LEFT, 60.0), (RIGHT, 320.0)):
        y = 60.0
        for line in column:
            if line:
                page.insert_text((x, y), line, fontsize=10, fontname="helv")
            y += 16.0
    doc.save(path, deflate=True, garbage=0)
    doc.close()


if __name__ == "__main__":
    build(sys.argv[1])
    print(f"wrote {sys.argv[1]}")
