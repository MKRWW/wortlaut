"""Pre-Flight-Archiv-Health-Check vor einem Backfill (#77, überarbeitet in #108).

Ein einziger echter Call, abgesetzt durch den **bereits injizierten** Archiver
— kein eigener HTTP-Pfad, kein eigenes Status-Gate, keine zweite Wahrheit.

Importiert nur stdlib + ``wortlaut.archive`` (R-ARCH-02) — der Contract
``archive-ist-unabhaengig`` bleibt damit erfüllt.
"""

from __future__ import annotations

from typing import Protocol


class ArchiveHealth(Protocol):
    """Nur das, was der Pre-Flight braucht: ein User-Status-Call ohne Capture."""

    async def user_status(self) -> str: ...


async def probe_archive(wayback: ArchiveHealth) -> str:
    """Ein einziger echter Call, der die zwei systematischen Startfehler belegt.

    Die Regel aus #77 gilt unverändert: Ein Health-Check, der etwas anderes
    misst als das, was gleich benutzt wird, ist kein Health-Check, sondern
    eine zweite Fehlerquelle. Was sich seit SPN2 (#108 §0b) geändert hat, ist
    *was* gemessen wird:

    Die frühere Capture-Probe hatte einen URL-abhängigen Falsch-Rot-Modus,
    der nichts mit dem Dienst zu tun hatte, sondern mit der Probe-URL:
    das Tageslimit (5 Captures/URL/Tag, global über alle Nutzer) der am
    meisten genutzten Test-URL der Welt ist dauerhaft ausgeschöpft. Eine
    Probe, die rot wird, obwohl der Dienst gesund ist, ist wertlos — sie
    hätte den Backfill dauerhaft blockiert.

    ``GET /save/status/user`` belegt in einem einzigen Call genau das, was
    am Laufbeginn systematisch kaputt sein kann: die Zugangsdaten werden
    akzeptiert und der Dienst antwortet. Er verbraucht kein
    Capture-Kontingent und antwortet sofort. Er belegt *nicht*, dass ein
    konkreter Capture gelingt — der bewusst gezahlte Preis: Ein
    fehlgeschlagener Einzel-Capture ist ohnehin kein Lauf-Abbruch, sondern
    ein ``archive_failed``-Outcome pro Quelle; dafür ist der Circuit-Breaker
    aus #73 zuständig, nicht der Pre-Flight.

    Die beiden alten Gegenbeispiele bleiben gültig:
      * ``GET web.archive.org/`` (Site-Root) lieferte 200, während ``/save/``
        tot war — *falsch grün*, hätte den Ausfall durchgewinkt.
      * ``GET archive.org/wayback/available`` liefert dauerhaft 502 —
        *falsch rot*, würde jeden Ingest blockieren, obwohl ``/save/``
        funktioniert.
    """
    return await wayback.user_status()
