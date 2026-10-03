# Increment-Spec: Adapter-Registry, Auswahl über `--adapter` und Rechtsgrundlage pro Quelle (#96, #97)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #96 und #97 · **Epic:** #94 (Quellen-Plugin-System, Schritte 2 und 3) ·
  **Status:** Reviewed (autonom; Durchsicht durch den Stakeholder steht aus — Punkte in §0c)
- **Baut auf:** #95 (Adapter-Vertrag, PR #137). Solange #137 nicht gemergt ist, liegt dieser Branch
  auf `feature/0095-adapter-vertrag`.
- **Phase/Layer:** `ingest` (Vertrag, Registry, DIP-Adapter), `cli`, `.importlinter`, Tests
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md) ·
  Recht: [../docs/legal.md](../docs/legal.md) §2, §10, §11

## 0. Ausgangslage

`cli.py` verdrahtet genau einen Adapter hart (`DipPlenarprotokollAdapter(DipSettings())`, im
`ingest` und im `reparse`) und kennt damit dessen Einstellungen. Die Rechtsgrundlage kommt aus einem
globalen Schalter `--rights-basis` mit Default `amtliches_werk_p5`.

### 0a. Warum #96 und #97 zusammen

Sobald es eine Auswahl gibt, kann ein zweiter Adapter laufen — und ohne #97 schriebe er jede Quelle
mit `amtliches_werk_p5` ins Ledger, also als gemeinfreies amtliches Werk (§ 5 UrhG), ohne dass
jemand das bestimmt hat. Das ist der Fehler, vor dem #97 warnt. Deshalb kommen beide in einem PR.

### 0b. Was schon da ist (gemessen)

- Die Lesepfade filtern `ungeklaert` bereits: `_PUBLIC_FILTER` und `_SOURCE_SQL` in
  `src/wortlaut/store/read.py` (`rights_basis <> 'ungeklaert'`). **Kein Test** beweist das bisher.
  #97 AC4 verlangt diesen Test; am Produktivcode der Lesepfade ändert sich nichts.
- Die Datenbank kennt den Enum `rights_basis` mit genau fünf Werten (`store/models.py`,
  `_RIGHTS_BASIS`; Migration 0002). Keine Migration nötig (#97 AC6).
- **mypy-Probe** (vorab gemessen): `rights_basis` als **schreibbares** Attribut `str | None` im
  Protocol macht auch den DIP-Adapter inkompatibel (Protocol-Attribute sind invariant, `str` passt
  nicht auf `str | None`). Als **Read-only-Property** im Protocol erfüllt der DIP-Adapter es mit
  einem Klassenattribut `rights_basis = "amtliches_werk_p5"`; dann bleiben **14 mypy-Fehler in vier
  Testdateien** — die Fakes `FakeIngestAdapter`, `FakeAdapter` (`test_pipeline_order.py`),
  `_FakeAdapter`, `_RaisingAdapter` ohne das Attribut. Genau dieselben vier wie bei #95.

### 0c. Entscheidungen, die zur Durchsicht stehen

1. **Registry im Code, nicht über Entry Points.** `default_registry()` in `wortlaut.ingest.registry`
   ist die **eine** Stelle, die konkrete Adapter kennt. Fremdpakete kommen in einer späteren Story
   (Entry Points, Epic-Schritt 5); die Naht dafür ist `AdapterRegistry.register`.
2. **Einstellungen über eine Fabrik.** Jeder registrierte Adapter liefert eine parameterlose Fabrik
   (`create`), die seine Einstellungen selbst aus der Umgebung liest — beim DIP-Adapter
   `DipPlenarprotokollAdapter.from_env()`. Der Kern ruft nur `create()` und kennt keinen
   Variablennamen.
3. **Listen-Befehl `python -m wortlaut adapters`** (eigenes Subcommand, liest keine Umgebung).
4. **`--adapter` auch an `reparse`.** `reparse` braucht den Adapter, der die Quellen geparst hat;
   ohne die Option bliebe dort die harte Verdrahtung stehen.
5. **Rechtsgrundlage zweistufig:** Der Adapter deklariert einen Default (`rights_basis: str | None`,
   `None` = „nur je Quelle"), und jede `SourceRef` kann ihn überschreiben (`SourceRef.rights_basis`).
   Sie hängt an `SourceRef` (Ergebnis von `discover`), nicht an `RawSource`, damit die Prüfung
   **vor** dem ersten `fetch` und vor jedem Insert steht.
6. **Vorrang:** ausdrückliches `--rights-basis` > Angabe der Quelle > Default des Adapters. Der
   Schalter bleibt als bewusste Übersteuerung erhalten (Issue #97 AC2: „wird nichts bestimmt, gilt
   die Angabe des Adapters"), hat aber **keinen** Default mehr. ⚠️ Rechtlich heikel: Wer ihn setzt,
   übersteuert auch eine Quelle, die der Adapter als `ungeklaert` meldet. Alternative wäre, ihn ganz
   zu streichen oder `ungeklaert` nicht übersteuerbar zu machen. **Entschieden (Stakeholder, 03.10.2026): Der Schalter darf übersteuern, auch `ungeklaert`.**
7. **Alles oder nichts:** Fehlt die Rechtsgrundlage für **irgendeine** entdeckte Quelle, oder ist
   ein Wert nicht im Enum, endet der Lauf mit Exit 2, **bevor** irgendetwas erfasst wird — auch im
   Dry-Run.

## 1. Ziel

1. `ingest` und `reparse` wählen ihren Adapter über eine Registry per `--adapter <name>`; ohne
   Angabe ist es der DIP-Adapter (`dip-api`).
2. `cli.py` importiert keine konkrete Adapter-Implementierung und kennt deren Einstellungen nicht.
3. Die Rechtsgrundlage ist Teil des Adapter-Vertrags, je Quelle angebbar; es gibt keinen
   stillschweigenden Default mehr.

## 2. Nicht-Ziele

- **Keine** Adapter aus fremden Paketen (Entry Points), **kein** zweiter echter Adapter.
- **Kein** Konformitäts-Testkit (#98).
- **Keine** Migration, **keine** Änderung an Store, Pipeline, Serving oder an den Lesefiltern.
- **Keine** Änderung an `timestamp`, `attest`, `capture`, `status`, `serve`.

## 3. Betroffene Interfaces / Öffentliche Signaturen

```python
# src/wortlaut/ingest/adapter.py  (weiterhin nur stdlib)
@dataclass(frozen=True)
class SourceRef:
    origin_url: str
    source_type: str
    hint: dict[str, object]
    rights_basis: str | None = None        # NEU: Rechtsgrundlage dieser Quelle (übersteuert den Adapter-Default)

class IngestAdapter(Protocol):
    ...                                     # bestehende Mitglieder unverändert
    @property
    def rights_basis(self) -> str | None: ...   # NEU: Default für alle Quellen; None = nur je Quelle

# src/wortlaut/ingest/rights.py  (NEU, nur stdlib)
RIGHTS_BASES: tuple[str, ...]               # die fünf Werte des DB-Enums, gleiche Reihenfolge
def resolve_rights_basis(
    *, override: str | None, per_source: str | None, adapter_default: str | None
) -> str | None: ...

# src/wortlaut/ingest/registry.py  (NEU)
DEFAULT_ADAPTER: str                        # = "dip-api"
@dataclass(frozen=True)
class AdapterEntry:
    name: str
    version: str
    trust_level: str
    rights_basis: str | None
    create: Callable[[], IngestAdapter]
class AdapterRegistry:
    def register(self, entry: AdapterEntry) -> None: ...   # doppelter Name → ValueError
    def get(self, name: str) -> AdapterEntry | None: ...
    def names(self) -> list[str]: ...                      # sortiert
    def entries(self) -> list[AdapterEntry]: ...           # nach Name sortiert
def default_registry() -> AdapterRegistry: ...

# src/wortlaut/ingest/dip.py
class DipPlenarprotokollAdapter:
    rights_basis = "amtliches_werk_p5"       # NEU
    @classmethod
    def from_env(cls) -> DipPlenarprotokollAdapter: ...    # NEU: cls(DipSettings())
```

CLI:

```
python -m wortlaut ingest  --since … [--adapter NAME] [--rights-basis {fünf Werte}] [--limit N] [--no-migrate] [--dry-run]
python -m wortlaut reparse [--adapter NAME] [--limit N] [--no-migrate] [--dry-run]
python -m wortlaut adapters
```

## 4. Design

### 4.1 Registry

`AdapterRegistry` hält `dict[str, AdapterEntry]`. `default_registry()` legt bei **jedem Aufruf** eine
neue Registry an und registriert den DIP-Adapter mit den Klassenattributen
(`name`, `version`, `trust_level`, `rights_basis`) und `create=DipPlenarprotokollAdapter.from_env`.
Kein Modul-Zustand.

### 4.2 Composition-Root

Ein gemeinsamer Helfer `_load_run_setup(adapter_name) -> _RunSetup | None` für `ingest` und
`reparse`:

1. Registry holen, Eintrag suchen. Unbekannt → Meldung
   `Unbekannter Adapter '<name>' — verfuegbar: <namen, kommagetrennt>` auf stderr, `None`.
   Das geschieht **vor** jedem Lesen der Umgebung.
2. In **einem** `try`: `DbSettings()`, `WormSettings()`, `entry.create()`. Jede Ausnahme →
   `Konfiguration fehlgeschlagen: {_config_error(e)}` auf stderr, `None`.

Der Aufrufer hat damit genau **einen** frühen `return 2` (Sonar S3516, siehe #131).
`_load_settings` entfällt.

### 4.3 Rechtsgrundlage auflösen

`resolve_rights_basis` gibt die erste nicht-`None`-Angabe in der Reihenfolge
`override` → `per_source` → `adapter_default` zurück, sonst `None`. Keine Validierung darin.

`cli._assign_rights(refs, adapter, override)` läuft in `_run` **nach** `discover` und `--limit`,
**vor** dem Dry-Run und vor der Schleife:

- `adapter_default = getattr(adapter, "rights_basis", None)` — ein fremder Adapter ohne das
  Attribut gilt als „keine Angabe", nicht als Absturz.
- Für jede Ref: aufgelöster Wert `None` → „fehlt"; Wert nicht in `RIGHTS_BASES` → „ungültig".
- Gibt es fehlende oder ungültige: Meldung(en) auf stderr, Rückgabe `None` ⇒ `_run` gibt 2 zurück.
  Meldungen (ASCII wie die übrigen CLI-Texte):
  - `rights_basis fehlt fuer <n> Quelle(n), z. B. <origin_url> — Adapter '<name>' deklariert keine Rechtsgrundlage; nichts erfasst`
  - `rights_basis ungueltig fuer <n> Quelle(n), z. B. <origin_url>; erlaubt: <werte, kommagetrennt>; nichts erfasst`
- Sonst Liste `(ref, wert)`; die Schleife übergibt `rights_basis=wert` an `ingest_source`.

`--rights-basis` bekommt `choices=RIGHTS_BASES` und `default=None` (argparse beendet bei einem
anderen Wert selbst mit Exit 2).

### 4.4 Architektur

Neuer import-linter-Vertrag: `wortlaut.cli` darf `wortlaut.ingest.dip` und
`wortlaut.ingest.settings` nicht **direkt** importieren (`allow_indirect_imports = True`, denn
`cli → ingest.registry → ingest.dip` ist gewollt). `ingest/adapter.py` und `ingest/rights.py`
importieren nur stdlib.

### 4.5 Listen-Befehl

`_run_adapters()` (synchron) druckt je Eintrag aus `default_registry().entries()` eine Zeile auf
stdout, Felder mit Tabulator getrennt:

```
dip-api	version=1.0.0	trust_level=verified_primary	rights_basis=amtliches_werk_p5	(default)
```

`rights_basis=je Quelle`, wenn der Eintrag `None` trägt; `(default)` nur beim `DEFAULT_ADAPTER`.
Exit 0. Liest keine Settings, ruft kein `create()`.

## 5. Testbare Akzeptanzkriterien

**#96 — Registry und Auswahl**

- **AC1 — Auswahl.** `main(["ingest", "--since", "2024-01-01", "--adapter", "test-quelle", "--no-migrate"])`
  mit einer Registry, die `test-quelle` enthält, ruft `discover` **dieses** Adapters auf; Exit 0.
- **AC2 — Default.** Ohne `--adapter` wird der Eintrag `dip-api` gewählt (Test: Registry mit
  **zwei** Einträgen, nur der unter `dip-api` wird aufgerufen). `default_registry()` enthält
  `dip-api` mit den Klassenattributen des DIP-Adapters.
- **AC3 — Unbekannter Name.** `--adapter gibtsnicht` → Exit 2 bei `ingest` und `reparse`; stderr
  enthält `Unbekannter Adapter` und `dip-api`; keine Settings gelesen, kein Traceback.
- **AC4 — Liste.** `main(["adapters"])` → Exit 0; die Zeile für `dip-api` enthält `version=1.0.0`,
  `trust_level=verified_primary`, `rights_basis=amtliches_werk_p5` und `(default)`; ein Eintrag mit
  `rights_basis=None` erscheint als `rights_basis=je Quelle`. Funktioniert ohne
  `WORTLAUT_DIP_API_KEY` in der Umgebung.
- **AC5 — Fremder Adapter ohne Kern-Import.** Ein Test-Adapter wird im Test registriert und über die
  CLI gewählt (AC1); `cli.py` importiert nichts aus `wortlaut.ingest.dip` oder
  `wortlaut.ingest.settings` (AST-Test) — der bestehende Test `test_cli_catches_only_adapter_error`
  prüft künftig „keine Importe aus `wortlaut.ingest.dip`".
- **AC6 — Eigene Einstellungen.** Mit der **echten** `default_registry()` und ohne
  `WORTLAUT_DIP_API_KEY` endet `ingest` mit Exit 2, stderr enthält `Konfiguration fehlgeschlagen`
  und den Feldnamen `api_key`, aber keinen Wert. Der Text `WORTLAUT_DIP` kommt in `cli.py` nicht vor.
- **AC7 — Architektur.** import-linter grün inklusive des neuen Vertrags aus §4.4.

**#97 — Rechtsgrundlage pro Quelle**

- **AC8 — Vertrag.** `IngestAdapter` deklariert `rights_basis`; `SourceRef` hat `rights_basis`
  mit Default `None`; der DIP-Adapter deklariert `amtliches_werk_p5`.
- **AC9 — Kein CLI-Default.** Ein Adapter mit `rights_basis = "lizenz"`, aufgerufen **ohne**
  `--rights-basis`, übergibt `rights_basis="lizenz"` an `ingest_source` (**nicht**
  `amtliches_werk_p5`). Mit `--rights-basis zitat_p51` wird `zitat_p51` übergeben.
- **AC10 — Keine Angabe ⇒ Exit 2.** Adapter mit `rights_basis = None` (und, zweiter Test, ein
  Adapter **ohne** das Attribut), Refs ohne eigene Angabe, kein `--rights-basis` → Exit 2,
  `ingest_source` **nie** aufgerufen, stderr enthält `rights_basis fehlt`; `aclose` genau einmal.
  Gilt auch mit `--dry-run`. Fehlt die Angabe nur bei **einer** von zwei Refs → ebenfalls Exit 2,
  `ingest_source` nie aufgerufen.
- **AC11 — Ungültiger Wert.** Eine Ref mit `rights_basis="gemeinfrei"` → Exit 2, nichts erfasst,
  stderr enthält `rights_basis ungueltig`. `--rights-basis gemeinfrei` → `SystemExit` mit Code 2.
- **AC12 — Je Quelle.** Adapter-Default `None`, zwei Refs mit `amtliches_werk_p5` bzw. `lizenz` →
  `ingest_source` erhält je Ref den eigenen Wert. Adapter-Default `amtliches_werk_p5`, Ref mit
  `ungeklaert` → `ungeklaert`. Integrationstest gegen echtes Postgres: die zwei Quellen stehen mit
  `lizenz` bzw. `ungeklaert` in der Tabelle `source`.
- **AC13 — `ungeklaert` nie ausgespielt.** Integrationstest gegen echtes Postgres + MinIO: eine
  **attestierte** Quelle mit einem `official`/`public`-Span, parametrisiert über `rights_basis`:
  mit `lizenz` wird der Span von `/v1/search` gefunden, `/v1/spans/{id}`,
  `/v1/spans/{id}/verify` und `/v1/sources/{id}` antworten 200; mit `ungeklaert` findet die Suche
  ihn nicht und alle drei Detail-Endpunkte antworten 404.
- **AC14 — Konsistenz mit der Datenbank.** `RIGHTS_BASES == tuple(_RIGHTS_BASIS.enums)` aus
  `wortlaut.store.models`.
- **AC15 — Bestand.** Keine Migration; alle bestehenden Tests grün, geändert nur wie in §11
  beschrieben. CI grün, 0 neue Sonar-Issues.

## 6. Testplan

| AC | Test | Datei | Art |
|---|---|---|---|
| AC2 (Registry-Teil), AC14, §4.1, §4.3 | `test_register_and_get` · `test_duplicate_name_rejected` · `test_names_sorted` · `test_default_registry_has_dip` · `test_rights_bases_match_db_enum` · `test_resolve_order` (parametrisiert) | `tests/unit/test_adapter_registry.py` (neu) | Unit |
| AC1–AC6, AC9–AC12 (Unit-Teil) | siehe §11 | `tests/unit/test_cli_registry.py` (neu) | Unit |
| AC5 | `test_cli_catches_only_adapter_error` (angepasst) | `tests/unit/test_adapter_contract.py` | Unit |
| AC8 | `test_protocol_declares_rights_basis` | `tests/unit/test_adapter_contract.py` | Unit |
| AC12 (DB) | `test_rights_basis_per_source_end_to_end` | `tests/integration/test_cli_ingest.py` | Integration |
| AC13 | `test_rights_basis_gates_serving` (parametrisiert) | `tests/integration/test_serving_api.py` | Integration |
| AC7, AC15 | import-linter, bestehende Tests | — | — |

## 7. Recht / Security

- R-DATA-03: `rights_basis` bleibt Pflicht; es gibt keinen Pfad mehr, auf dem eine Quelle ohne
  bewusste Angabe als `amtliches_werk_p5` landet. `ungeklaert` wird geschrieben, nie ausgespielt.
- R-SEC-01: Konfigurationsfehler nennen nur Feldnamen (`_config_error`), nie Werte.
- Die Übersteuerung per `--rights-basis` ist eine bewusste Bedienerhandlung und vom Stakeholder so entschieden (§0c Punkt 6).

## 8. Risiken

- Ein künftiger Adapter für eine nicht-amtliche Quelle, dessen Autor `rights_basis = "amtliches_werk_p5"`
  aus dem DIP-Adapter abschreibt, ist durch Code nicht zu verhindern. Das Konformitäts-Testkit (#98)
  und die Anleitung (Epic-Schritt 7) sollen das ansprechen.
- Bestehende Aufrufe im Betrieb (`ingest --since …`) laufen unverändert, weil der DIP-Adapter
  `amtliches_werk_p5` selbst deklariert.

## 9. Definition of Done

Siehe `docs/engineering.md`. Kein Betriebsschritt nötig; nach dem Merge ist `python -m wortlaut
adapters` auf dem Dedicated eine einfache Sichtprobe.

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu:**
- `src/wortlaut/ingest/rights.py`
- `src/wortlaut/ingest/registry.py`
- `tests/unit/test_adapter_registry.py`
- `tests/unit/test_cli_registry.py`

**Ändern:**
- `src/wortlaut/ingest/adapter.py`
- `src/wortlaut/ingest/dip.py`
- `src/wortlaut/cli.py`
- `.importlinter`
- `tests/unit/test_adapter_contract.py`
- `tests/unit/test_cli.py`, `tests/unit/test_cli_adapter_contract.py`, `tests/unit/test_cli_reparse.py`
- `tests/integration/test_cli_ingest.py`, `tests/integration/test_serving_api.py`
- `tests/integration/test_pipeline_ingest.py`, `tests/unit/test_pipeline_order.py`,
  `tests/unit/test_reparse_pipeline.py`, `tests/unit/test_span_hash.py` — **nur** das Klassenattribut
  aus §11

## 11. Umsetzungsdetails je Datei

### `src/wortlaut/ingest/rights.py` (neu)

Modul-Docstring: Rechtsgrundlage je Quelle (docs/legal.md §2, §10; R-DATA-03); nur stdlib.

```python
RIGHTS_BASES: tuple[str, ...] = (
    "amtliches_werk_p5",
    "oeffentlich_gemacht_art9e",
    "zitat_p51",
    "lizenz",
    "ungeklaert",
)


def resolve_rights_basis(
    *, override: str | None, per_source: str | None, adapter_default: str | None
) -> str | None:
    """Erste gesetzte Angabe: ausdrückliche Übersteuerung, dann die Quelle, dann der Adapter."""
    for candidate in (override, per_source, adapter_default):
        if candidate is not None:
            return candidate
    return None
```

### `src/wortlaut/ingest/adapter.py`

- `SourceRef`: als **letztes** Feld `rights_basis: str | None = None`, mit Kommentar
  „Rechtsgrundlage dieser Quelle; übersteuert ``IngestAdapter.rights_basis`` (#97)".
- `IngestAdapter`: direkt nach `trust_level` einfügen:
  ```python
      @property
      def rights_basis(self) -> str | None:
          """Rechtsgrundlage aller Quellen dieses Adapters (Wert aus ``RIGHTS_BASES``);
          ``None`` heißt: jede ``SourceRef`` bringt ihre eigene mit. Ohne beides
          verweigert der Kern die Erfassung (#97)."""
          ...
  ```
- Modul-Docstring: Satz ergänzen, dass der Adapter die Rechtsgrundlage seiner Quellen deklariert.
  Weiterhin nur stdlib-Importe.

### `src/wortlaut/ingest/dip.py`

- Nach `trust_level = "verified_primary"`: `rights_basis = "amtliches_werk_p5"` mit Kommentar
  `# Plenarprotokolle: amtliches Werk, § 5 UrhG (docs/legal.md §2)`.
- Nach `__init__`:
  ```python
      @classmethod
      def from_env(cls) -> DipPlenarprotokollAdapter:
          """Baut den Adapter aus seinen eigenen ENV-Einstellungen (``WORTLAUT_DIP_*``)."""
          return cls(DipSettings())
  ```
  `DipSettings` muss dabei der Modul-Name aus dem bestehenden Import sein (Tests patchen
  `wortlaut.ingest.dip.DipSettings`).

### `src/wortlaut/ingest/registry.py` (neu)

Modul-Docstring: Adapter-Registry (#96) — die eine Stelle, die konkrete Adapter kennt; der
Composition-Root wählt per Name. Inhalt nach §3 und §4.1:

```python
DEFAULT_ADAPTER = "dip-api"


@dataclass(frozen=True)
class AdapterEntry:
    """Ein registrierter Adapter: Metadaten für die Liste plus Fabrik.

    ``create`` liest die Einstellungen des Adapters selbst aus der Umgebung —
    der Kern kennt deren Namen nicht.
    """

    name: str
    version: str
    trust_level: str
    rights_basis: str | None
    create: Callable[[], IngestAdapter]


class AdapterRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, AdapterEntry] = {}

    def register(self, entry: AdapterEntry) -> None:
        if entry.name in self._entries:
            raise ValueError(f"Adapter '{entry.name}' ist bereits registriert")
        self._entries[entry.name] = entry

    def get(self, name: str) -> AdapterEntry | None:
        return self._entries.get(name)

    def names(self) -> list[str]:
        return sorted(self._entries)

    def entries(self) -> list[AdapterEntry]:
        return [self._entries[n] for n in self.names()]


def default_registry() -> AdapterRegistry:
    """Neue Registry mit allen eingebauten Adaptern (kein Modul-Zustand)."""
    registry = AdapterRegistry()
    registry.register(
        AdapterEntry(
            name=DipPlenarprotokollAdapter.name,
            version=DipPlenarprotokollAdapter.version,
            trust_level=DipPlenarprotokollAdapter.trust_level,
            rights_basis=DipPlenarprotokollAdapter.rights_basis,
            create=DipPlenarprotokollAdapter.from_env,
        )
    )
    return registry
```

### `src/wortlaut/cli.py`

- Importe: `DipPlenarprotokollAdapter` und `DipSettings` **entfernen**. Neu:
  `from wortlaut.ingest.adapter import AdapterError, IngestAdapter, SourceRef`,
  `from wortlaut.ingest.registry import DEFAULT_ADAPTER, default_registry`,
  `from wortlaut.ingest.rights import RIGHTS_BASES, resolve_rights_basis`.
- `main`: am `ingest`-Parser `--rights-basis` → `default=None, choices=RIGHTS_BASES`; neu
  `p_ingest.add_argument("--adapter", default=DEFAULT_ADAPTER)`. Am `reparse`-Parser ebenso
  `--adapter`. Neues Subcommand `subparsers.add_parser("adapters")`. Die Liste der gültigen
  Subcommands und die Fehlermeldung um `'adapters'` ergänzen; Dispatch
  `if subcommand == "adapters": return _run_adapters()`.
- `@dataclass(frozen=True) class _RunSetup` mit `db: DbSettings`, `worm: WormSettings`,
  `adapter: IngestAdapter`; Helfer `_load_run_setup(adapter_name: str) -> _RunSetup | None` nach
  §4.2. `_load_settings` löschen.
- `_run`: `setup = _load_run_setup(args.adapter)`; `if setup is None: return 2`. Danach wie bisher,
  mit `setup.db`, `setup.worm`, `adapter = setup.adapter`. Nach dem `--limit`-Kappen:
  ```python
          assigned = _assign_rights(refs, adapter, args.rights_basis)
          if assigned is None:
              return 2
  ```
  Dann Dry-Run wie bisher (`discovered={len(refs)} dry_run=True`), Schleife über
  `for ref, rights_basis in assigned:` mit `rights_basis=rights_basis`. Docstring: Exit 2 auch bei
  fehlender oder ungültiger Rechtsgrundlage.
- `_assign_rights(refs: list[SourceRef], adapter: IngestAdapter, override: str | None)
  -> list[tuple[SourceRef, str]] | None` nach §4.3.
- `_run_reparse`: den Konfigurationsblock (`DbSettings()`, `WormSettings()`, `DipSettings()` +
  `except`) durch `setup = _load_run_setup(args.adapter)` / `if setup is None: return 2` ersetzen;
  `adapter = setup.adapter`, sonst unverändert.
- `_run_adapters() -> int` nach §4.5.

### `.importlinter`

Am Ende ergänzen:

```ini
# Spec 0096 §4.4: Der Composition-Root waehlt Adapter ueber die Registry. Er importiert keine
# konkrete Adapter-Implementierung und kennt deren Einstellungen nicht. Der indirekte Weg
# cli -> ingest.registry -> ingest.dip ist gewollt: die Registry ist die eine Stelle, die Adapter kennt.
[importlinter:contract:cli-kennt-keinen-konkreten-adapter]
name = CLI importiert keine konkrete Adapter-Implementierung
type = forbidden
source_modules =
    wortlaut.cli
forbidden_modules =
    wortlaut.ingest.dip
    wortlaut.ingest.settings
allow_indirect_imports = True
```

### Bestehende Tests — genau diese Änderungen, sonst nichts

1. **Klassenattribut** `rights_basis = "amtliches_werk_p5"` direkt unter `trust_level` in:
   `FakeIngestAdapter` (`tests/integration/test_pipeline_ingest.py`), `FakeAdapter`
   (`tests/unit/test_pipeline_order.py`), `_FakeAdapter` (`tests/unit/test_reparse_pipeline.py`),
   `_RaisingAdapter` (`tests/unit/test_span_hash.py`), `FakeAdapter` (`tests/unit/test_cli.py`),
   `MinimalAdapter` (`tests/unit/test_cli_adapter_contract.py`), `FakeDipAdapter`
   (`tests/unit/test_cli_reparse.py`), `_FakeCliAdapter` (`tests/integration/test_cli_ingest.py`).
2. **Namespace-Helfer:** `"adapter": "dip-api"` ergänzen in `_ns` von `tests/unit/test_cli.py`,
   `tests/unit/test_cli_adapter_contract.py`, `tests/unit/test_cli_reparse.py`; `adapter="dip-api"`
   in `_ingest_args` von `tests/integration/test_cli_ingest.py`. `rights_basis` dort **nicht**
   ändern.
3. **Registry-Helfer:** in `tests/unit/test_cli.py`, `tests/unit/test_cli_adapter_contract.py`,
   `tests/unit/test_cli_reparse.py` und `tests/integration/test_cli_ingest.py` je diese Funktion
   einfügen (Importe `cast` aus `typing`, `IngestAdapter` aus `wortlaut.ingest.adapter`,
   `AdapterEntry`, `AdapterRegistry`, `DEFAULT_ADAPTER` aus `wortlaut.ingest.registry`):
   ```python
   def _registry_with(adapter: object) -> AdapterRegistry:
       """Registry, deren Default-Eintrag genau ``adapter`` liefert."""
       typed = cast(IngestAdapter, adapter)
       registry = AdapterRegistry()
       registry.register(
           AdapterEntry(
               name=DEFAULT_ADAPTER,
               version=typed.version,
               trust_level=typed.trust_level,
               rights_basis=getattr(adapter, "rights_basis", None),
               create=lambda: typed,
           )
       )
       return registry
   ```
4. **Patch-Ziele:**
   - Zeilen `patch("wortlaut.cli.DipSettings", return_value=…)` in den `wired`-Fixtures **löschen**.
   - `patch("wortlaut.cli.DipPlenarprotokollAdapter", return_value=adapter)` →
     `patch("wortlaut.cli.default_registry", return_value=_registry_with(adapter))`.
   - `tests/integration/test_cli_ingest.py`: `patch("wortlaut.cli.DipPlenarprotokollAdapter", _FakeCliAdapter)`
     → `patch("wortlaut.cli.default_registry", return_value=_registry_with(_FakeCliAdapter()))`.
   - Die zwei Konfigurationsfehler-Tests (`test_missing_env_exits_nonzero` in `test_cli.py`,
     `test_config_error_exits_two` in `test_cli_reparse.py`): statt
     `patch("wortlaut.cli.DipSettings", side_effect=X)` jetzt **zwei** Patches im selben `with`:
     `patch("wortlaut.cli.default_registry", new=registry_module.default_registry)` (stellt die
     echte Registry wieder her; `from wortlaut.ingest import registry as registry_module`) und
     `patch("wortlaut.ingest.dip.DipSettings", side_effect=X)`. Asserts unverändert.
5. **`tests/unit/test_adapter_contract.py`:** in `test_cli_catches_only_adapter_error` die Zeile
   `assert set(dip_imports) == {"DipPlenarprotokollAdapter"}` → `assert dip_imports == []`; Docstring
   entsprechend („importiert nichts aus `wortlaut.ingest.dip`, #96"). Neuer Test
   `test_protocol_declares_rights_basis`: `"rights_basis" in IngestAdapter.__protocol_attrs__`
   (falls das Attribut auf der Python-Version fehlt: `hasattr(IngestAdapter, "rights_basis")`),
   `SourceRef("u", "t", {}).rights_basis is None`,
   `DipPlenarprotokollAdapter.rights_basis == "amtliches_werk_p5"`.

### Neue Tests

**`tests/unit/test_adapter_registry.py`:** Registry-Verhalten (§4.1), `default_registry()` enthält
`DEFAULT_ADAPTER` mit `version`, `trust_level`, `rights_basis` gleich den Klassenattributen des
DIP-Adapters und `create is DipPlenarprotokollAdapter.from_env` (bzw. `==`), AC14,
`resolve_rights_basis` parametrisiert — mindestens: nur `adapter_default="lizenz"` → `lizenz`;
`per_source="zitat_p51"` schlägt `adapter_default="lizenz"`; `override="oeffentlich_gemacht_art9e"`
schlägt beide; alles `None` → `None`. **Nie `amtliches_werk_p5` als erwarteten Wert eines
Vorrang-Tests verwenden** — er ist der alte Default und kann eine Verwechslung nicht aufdecken.

**`tests/unit/test_cli_registry.py`:** eigene schlichte Fakes nach dem Muster von
`tests/unit/test_cli.py` (Fixture `wired`, die `DbSettings`, `WormSettings`,
`create_async_engine_from`, `make_sessionmaker`, `MinioWormStore`, `upgrade_head`,
`ensure_ingest_adapter`, `ingest_source` patcht). Ein Fake-Adapter mit Konstruktor-Parametern
`refs` und `rights_basis` (Typ `str | None`), zählt `discover`- und `aclose`-Aufrufe. Registry je
Test selbst bauen (`AdapterRegistry()` + `register`), per
`patch("wortlaut.cli.default_registry", return_value=…)`. Abdecken: AC1 (über `main([...])` in
einem **synchronen** Test), AC2, AC3 (ingest und reparse; die **echte** Registry, keine Settings
gepatcht — `DbSettings` als `MagicMock` patchen und `assert_not_called()`), AC4 (inkl.
`monkeypatch.delenv("WORTLAUT_DIP_API_KEY", raising=False)`), AC6 (echte Registry,
`monkeypatch.delenv("WORTLAUT_DIP_API_KEY", raising=False)`, Db/Worm gepatcht; außerdem
`"WORTLAUT_DIP" not in` Quelltext von `cli.py`), AC9 bis AC12 (Unit-Teil, über
`await _run(_ns(...))`; den übergebenen Wert über `wired.ingest.call_args_list[i].kwargs["rights_basis"]`
prüfen). Für den Adapter **ohne** Attribut eine eigene Klasse ohne `rights_basis`.

**`tests/integration/test_cli_ingest.py`** — `test_rights_basis_per_source_end_to_end`: ein
zweiter Fake-Adapter (`rights_basis = None`), `discover` liefert zwei `SourceRef` mit
`rights_basis="lizenz"` bzw. `"ungeklaert"`, `fetch` liefert je **verschiedene** Rohbytes;
`_ingest_args()` mit `rights_basis=None`; danach
`SELECT origin_url, rights_basis FROM source` → genau diese zwei Zuordnungen.

**`tests/integration/test_serving_api.py`** — `_source` bekommt den Parameter
`rights_basis: str = "amtliches_werk_p5"` (an `NewSource` durchreichen; bestehende Aufrufe
unverändert). Neuer Test `test_rights_basis_gates_serving`, parametrisiert über
`("lizenz", True)` und `("ungeklaert", False)`: `_client(...)` wie die anderen Tests, dann in
eigener Session eine Quelle D über `_source(..., rights_basis=…)` mit eigenen Rohbytes im WORM
(`worm.put`), `seed_attestation` für D, Sprecher/Mandat, ein Span über `_span` mit einem
`verbatim`, das das Wort `Rechtsgrundlagenprobe` enthält (sonst nirgends). Erwartung siehe AC13
(Suche `q=Rechtsgrundlagenprobe`: `total` 1 bzw. 0; Detail, Verify, Quelle: 200 bzw. 404).

**Sonar-Muster (alle neuen und geänderten Tests):** genau **ein** Aufruf je `pytest.raises`-Block
(auch keine Hilfsaufrufe als Argument — `main(argv)` mit vorher gebautem `argv`); keine
zusammengesetzten Asserts (`assert a and b`); kein Lambda in `patch()`; ein String-Literal, das
dreimal oder öfter in einer Datei vorkommt (z. B. `"dip-api"`, `"lizenz"`, `"2024-01-01"`), als
Modulkonstante; `async def` nur, wo awaited wird; keine Fixture mit `yield` ohne Teardown.

## 12. Do-NOT (hart)

- **Keine** Migration, **keine** Änderung an `store/`, `pipeline/`, `serving/`.
- **Kein** Default `amtliches_werk_p5` mehr an `--rights-basis`; **kein** stilles Zurückfallen
  auf irgendeinen Wert.
- **Kein** Import aus `wortlaut.ingest.dip` oder `wortlaut.ingest.settings` in `cli.py`.
- **Kein** Modul-Zustand in der Registry (keine globale Instanz).
- **Kein** wortlaut-Eigenimport in `ingest/adapter.py` und `ingest/rights.py`.
- **Keine** ENV-Werte in Meldungen (immer `_config_error`).
- Bestehende Tests **nur** wie in §11 ändern; **keine** neuen Abhängigkeiten, **keine** Dateien
  außerhalb von §10.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate fährt der Reviewer selbst.
