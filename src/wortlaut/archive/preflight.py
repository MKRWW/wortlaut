"""Pre-Flight-Archiv-Health-Check vor einem Backfill (Spec 0077, #77).

Ein einziger echter Archivierungs-Versuch gegen eine neutrale URL, abgesetzt
durch den **bereits injizierten** Archiver — kein eigener HTTP-Call, kein
eigenes Status-Gate, keine zweite Wahrheit (§0a, §4.1).

Importiert nur stdlib + ``wortlaut.archive`` (R-ARCH-02) — der
Contract ``archive-ist-unabhaengig`` bleibt damit erfüllt.
"""

from __future__ import annotations

from wortlaut.archive.archiver import Archiver

PROBE_URL = "https://example.com/"  # IANA-reservierte Beispiel-Domain, keine echte Quelle


async def probe_archive(wayback: Archiver, *, probe_url: str = PROBE_URL) -> str:
    """Ein einziger Archivierungs-Versuch gegen eine neutrale URL.

    Liefert die Snapshot-URL, wenn der Dienst funktionsfähig ist. Wirft den
    ``ArchiveError`` des Archivers unverändert weiter, wenn nicht — Grund und
    Statuscode bleiben damit bis in die Abbruchmeldung erhalten.

    **Warum genau dieser Call und kein anderer:** Der Probe ist bewusst *kein*
    eigener HTTP-Pfad, sondern ein ganz normaler ``wayback.archive(<neutrale
    URL>)``-Aufruf über den injizierten Archiver. Damit erbt er Status-Gate,
    Snapshot-Validierung, Drosselung und Retry aus #73 — und trifft exakt den
    Endpunkt ``/save/``, von dem der laufende Backfill abhängt.

    Die naheliegenden Alternativen sind beide falsch (§0a, gemessen):
      * ``GET web.archive.org/`` (Site-Root) lieferte 200, während ``/save/``
        tot war — *falsch grün*, hätte den Ausfall durchgewinkt.
      * ``GET archive.org/wayback/available`` liefert dauerhaft 502 —
        *falsch rot*, würde jeden Ingest blockieren, obwohl ``/save/``
        funktioniert.

    Ein Health-Check, der etwas anderes misst als das, was gleich benutzt wird,
    ist kein Health-Check, sondern eine zweite Fehlerquelle.
    """
    return await wayback.archive(probe_url)
