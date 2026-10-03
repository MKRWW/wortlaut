# Adapter aus eigenen Paketen

## Wozu

Ein Adapter in einem eigenen Paket wird über Entry Points angemeldet und ist nach Freigabe durch
den Betreiber per `--adapter` wählbar, ohne dass er in den Kern muss. Ein Plugin kann sich nicht
selbst zum Veröffentlichen berechtigen — das entscheidet der Betreiber.

## Paket anmelden

Im `pyproject.toml` des eigenen Pakets:

```toml
[project.entry-points."wortlaut.adapters"]
mein-landtag = "mein_paket.adapter:ENTRY"
```

Der Entry Point zeigt auf eine Modulkonstante, nicht auf eine Klasse:

```python
from wortlaut.ingest.adapter import IngestAdapter
from wortlaut.ingest.registry import AdapterEntry


def _create() -> IngestAdapter:
    return MeinAdapter()


ENTRY = AdapterEntry(
    name="mein-landtag",
    version="0.1.0",
    trust_level="secondary",
    rights_basis="zitat_p51",
    create=_create,
)
```

Der Name des Entry Points muss zum `name` des `AdapterEntry` passen.

## Freigabe durch den Betreiber

Zwei Umgebungsvariablen, je kommagetrennte Namen:

- `WORTLAUT_ADAPTER_PLUGINS` — welche Plugins geladen werden dürfen
- `WORTLAUT_ADAPTER_VERIFIED` — welche Plugins als `verified_primary` wirken dürfen; muss eine
  Teilmenge von `WORTLAUT_ADAPTER_PLUGINS` sein

Ohne Freigabe wird nichts geladen: für nicht freigegebene Entry Points gibt es keinen Import, es
läuft kein Code.

## Vertrauen und Deckel

Nur `verified_primary` wird direkt veröffentlicht. Plugins laufen sonst als `secondary`; ihre
Spans sind `machine` und werden ohne menschliche Prüfung nicht ausgespielt. Name, Version,
Vertrauen und Rechtsgrundlage werden auf den geprüften Eintrag gepinnt — die Adapter-Instanz kann
nicht etwas anderes behaupten als der Registry-Eintrag.

## Fehlermeldungen

Jeder Fall führt als Konfigurationsfehler zu Exit 2; der erste Fehler bricht ab:

- `freigegeben als verified, aber nicht in WORTLAUT_ADAPTER_PLUGINS: <namen>`
- `'<name>' ist ein eingebauter Adapter` — Kollision, erkannt vor dem Laden
- `kein Entry Point '<name>' in Gruppe wortlaut.adapters`
- `mehrdeutig: <n> Entry Points '<name>'`
- `'<name>' laden fehlgeschlagen: <Ausnahmetyp>` — nie der Ausnahmetext
- Entry Point ist kein `AdapterEntry` oder sein `name` weicht vom freigegebenen Namen ab
- `trust_level` ist kein Wert aus `TRUST_LEVELS`, oder `rights_basis` ist weder `None` noch ein
  Wert aus `RIGHTS_BASES`

## Warnhinweis

Ein freigegebenes Plugin führt beim Laden Code aus — nur Pakete freigeben, denen man vertraut.

## Verweis

Das Testkit für Adapter-Instanzen: [Konformitäts-Testkit für Adapter](adapter-konformitaet.md).
