# Increment-Spec: Adapter aus fremden Paketen — Entry Points mit Freigabe und gedeckeltem Vertrauen (#141)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #141 · **Epic:** #94 (Schritt 5) · **Status:** Reviewed (autonom; Sicherheitsrahmen
  vom Stakeholder entschieden, Details in §0b zur Durchsicht)
- **Phase/Layer:** `ingest` (neues Modul, Registry), `cli`, Paket-Marker, Doku, Tests

## 0. Ausgangslage

Seit #96 wählt der Kern Adapter über `wortlaut.ingest.registry.default_registry()`, die nur
eingebaute Adapter kennt. Ein Adapter im eigenen Paket muss heute in den Kern.

### 0a. Gemessen

- **Einziger Veröffentlichungsschalter ist `trust_level`:** `pipeline/spans.py:47` setzt Spans eines
  Adapters mit `verified_primary` auf `official`, alle anderen auf `machine`; `store/read.py` spielt
  nur `official`/`human_verified` aus. Die Pipeline liest `trust_level` von der **Adapter-Instanz**,
  `cli.py` schreibt es von der Instanz in `ingest_adapter`.
- **`importlib.metadata.entry_points(group=…)` findet ein Paket, das nur als `*.dist-info` in einem
  Verzeichnis auf `sys.path` liegt** (Probe 03.10.2026: `entry_points.txt` + `METADATA` in
  `tmp`, Eintrag gefunden und geladen). Damit ist ein Test über den echten Weg möglich (AC6).
- **wortlaut hat keinen `py.typed`-Marker:** Ein Plugin-Autor, der gegen `wortlaut.ingest.adapter`
  typprüft, bekommt `import-untyped` und nur `Any` (Probe). `hatchling` packt alles unter
  `src/wortlaut` ein; eine leere `src/wortlaut/py.typed` genügt.
- Ein Deckel-Wrapper mit Instanzattributen `name`/`version`/`trust_level` und Property
  `rights_basis` erfüllt das Protocol unter mypy strict (Probe im Paketkontext).

### 0b. Entscheidungen

**Vom Stakeholder entschieden (03.10.2026):** Plugins laufen höchstens als `secondary`;
`verified_primary` nur nach ausdrücklicher Freigabe durch den Betreiber.

**Zur Durchsicht (selbst entschieden):**
1. Betreiber-Variablen `WORTLAUT_ADAPTER_PLUGINS` und `WORTLAUT_ADAPTER_VERIFIED`, je kommagetrennte
   Namen. Nicht freigegebene Entry Points werden **nie** geladen (kein Import, kein Code).
2. **Jedes** Plugin wird in einen Wrapper gelegt, der `name`, `version`, `trust_level` und
   `rights_basis` auf die geprüften Werte des Registry-Eintrags pinnt — auch wenn nicht gedeckelt
   wird. Sonst könnte die Instanz etwas anderes behaupten als der Eintrag.
3. Der Entry Point zeigt auf ein **`AdapterEntry`-Objekt** (Modulkonstante), nicht auf eine Klasse.
4. Kollision mit einem eingebauten Namen wird **vor** dem Laden erkannt (kein Code läuft).
5. Fehler der Plugin-Konfiguration → Exit 2 mit Meldung (wie Konfigurationsfehler).
6. Die CLI ruft künftig `registry_from_env()` (eingebaute + freigegebene Plugins);
   `default_registry()` bleibt die reine Liste der eingebauten Adapter.

## 1. Ziel

Ein Adapter in einem fremden Paket ist nach Freigabe durch den Betreiber per `--adapter` wählbar,
ohne den Kern zu ändern — und kann sich nicht selbst zum Veröffentlichen berechtigen.

## 2. Nicht-Ziele

Kein zweiter echter Adapter, keine Vorlage, kein Installieren von Plugins ins Docker-Image, keine
Migration, keine Änderung an Pipeline, Store oder Serving.

## 3. Öffentliche Signaturen

```python
# src/wortlaut/ingest/registry.py  (Ergänzung, abwärtskompatibel)
@dataclass(frozen=True)
class AdapterEntry:
    name: str
    version: str
    trust_level: str
    rights_basis: str | None
    create: Callable[[], IngestAdapter]
    declared_trust_level: str | None = None   # NEU: was das Plugin deklariert hat (nur bei Plugins)
    plugin: bool = False                      # NEU

# src/wortlaut/ingest/plugins.py  (NEU)
PLUGIN_GROUP = "wortlaut.adapters"

class PluginError(Exception): ...             # Fehlkonfiguration; Meldung ohne Fremdtext

class PluginSettings(BaseSettings):           # env_prefix "WORTLAUT_ADAPTER_"
    plugins: str = ""
    verified: str = ""

class EntryPointLike(Protocol):
    name: str
    def load(self) -> object: ...

def load_plugins(
    registry: AdapterRegistry,
    *,
    allowed: Sequence[str],
    verified: Sequence[str],
    entry_points: Iterable[EntryPointLike],
) -> None: ...

def registry_from_env() -> AdapterRegistry: ...   # default_registry() + freigegebene Plugins
```

## 4. Design

### 4.1 `load_plugins` — Reihenfolge (jede Verletzung → `PluginError`, Registry bleibt unverändert
für dieses Plugin; der erste Fehler bricht ab)

1. `verified` ⊆ `allowed`, sonst Fehler `freigegeben als verified, aber nicht in WORTLAUT_ADAPTER_PLUGINS: <namen>`.
2. Für jeden Namen in `allowed` (Reihenfolge erhalten, Doppelte ignorieren):
   1. Kollision: `registry.get(name)` existiert → Fehler `'<name>' ist ein eingebauter Adapter`.
      **Vor** dem Laden.
   2. Passende Entry Points (`ep.name == name`): keiner → Fehler
      `kein Entry Point '<name>' in Gruppe wortlaut.adapters`; mehr als einer → Fehler
      `mehrdeutig: <n> Entry Points '<name>'`.
   3. `obj = ep.load()`; jede Ausnahme → Fehler `'<name>' laden fehlgeschlagen: <Ausnahmetyp>`
      (**nie** den Ausnahmetext).
   4. `obj` kein `AdapterEntry` → Fehler; `obj.name != name` → Fehler.
   5. `obj.trust_level` nicht in `TRUST_LEVELS` (aus `wortlaut.ingest.conformance`) oder
      `obj.rights_basis` weder `None` noch in `RIGHTS_BASES` → Fehler.
   6. Wirksames Vertrauen: `obj.trust_level`, außer es ist `verified_primary` und `name` nicht in
      `verified` → `secondary`.
   7. Registrieren: `AdapterEntry(name, obj.version, wirksam, obj.rights_basis,
      create=<gepinnte Fabrik>, declared_trust_level=obj.trust_level, plugin=True)`.
Nicht freigegebene Entry Points werden nicht geladen (kein `ep.load()`).

### 4.2 Gepinnte Fabrik

`_PinnedAdapter(inner, name, version, trust_level, rights_basis)`: setzt die vier Werte als
Attribute bzw. Property und delegiert `discover`, `fetch`, `normalize`, `parse`, `aclose` an
`inner`. Die Fabrik ruft `obj.create()` und legt das Ergebnis in den Wrapper. Ausnahmen aus
`obj.create()` laufen unverändert durch (die CLI meldet sie als Konfigurationsfehler, wie bei
eingebauten Adaptern).

### 4.3 `registry_from_env`

`PluginSettings()` lesen, Listen per `[s.strip() for s in x.split(",") if s.strip()]`, dann
`registry = default_registry()` und `load_plugins(registry, allowed=…, verified=…,
entry_points=importlib.metadata.entry_points(group=PLUGIN_GROUP))`. Leere Variable ⇒ es wird gar
nicht nach Entry Points gesucht (Aufruf nur, wenn `allowed` nicht leer).

### 4.4 CLI

- Import `registry_from_env` und `PluginError` aus `wortlaut.ingest.plugins`; `default_registry` wird
  in `cli.py` nicht mehr importiert.
- `_load_run_setup`: `registry = registry_from_env()` in `try/except PluginError as e` →
  `Plugin-Konfiguration fehlgeschlagen: {e}` auf stderr, `return None`.
- `_run_adapters`: ebenso, dann `return 2`. Zeile je Eintrag wie bisher, zusätzlich:
  `trust_level=<wirksam>`, und wenn `declared_trust_level` gesetzt ist und abweicht, direkt dahinter
  ` (gedeckelt, deklariert <declared>)`; am Zeilenende `\t(plugin)` für Plugins (vor `(default)`,
  das ein Plugin nie trägt).

## 5. Testbare Akzeptanzkriterien

- **AC1 — Laden nach Freigabe.** Mit Fake-Entry-Points: freigegebener Name wird geladen, registriert
  (`plugin=True`) und ist über die Registry abrufbar; `registry_from_env` mit gesetzter Variable
  liefert eingebaute + Plugin.
- **AC2 — Ohne Freigabe kein Code.** Nicht freigegebener Entry Point: `load()` wird **nie** gerufen.
  Leere Variablen: `registry_from_env().names() == default_registry().names()` und
  `importlib.metadata.entry_points` wird nicht aufgerufen.
- **AC3 — Deckel.** Plugin deklariert `verified_primary`, nicht in `verified` → Eintrag und
  **Instanz** (`entry.create().trust_level`) sind `secondary`, `declared_trust_level ==
  "verified_primary"`. In `verified` → `verified_primary`. Eine Instanz, die selbst
  `verified_primary` behauptet, während der Eintrag `secondary` sagt → Instanz gepinnt auf
  `secondary`. Ebenso `name`, `version`, `rights_basis` gepinnt.
- **AC4 — Fehlkonfiguration.** Jeder Fall aus §4.1 wirft `PluginError` mit der genannten Meldung;
  bei Kollision und bei „verified ⊄ allowed" wird `load()` nicht gerufen; beim Ladefehler steht
  der Ausnahmetext **nicht** in der Meldung. Über die CLI: `ingest` und `adapters` → Exit 2,
  stderr enthält `Plugin-Konfiguration fehlgeschlagen`.
- **AC5 — Liste.** `adapters` zeigt ein gedeckeltes Plugin mit `trust_level=secondary (gedeckelt,
  deklariert verified_primary)` und `(plugin)`.
- **AC6 — Echter Weg.** Test mit einem Paket als `*.dist-info` + Modul in `tmp_path`
  (`monkeypatch.syspath_prepend`), Variable gesetzt: `main(["adapters"])` zeigt das Plugin;
  `ingest` mit `--adapter <plugin>` (Kern-Abhängigkeiten gepatcht wie in `tests/unit/test_cli.py`)
  ruft `ensure_ingest_adapter` mit `trust_level="secondary"` auf, obwohl das Plugin
  `verified_primary` deklariert.
- **AC7 — Typen und Doku.** `src/wortlaut/py.typed` existiert. `docs/adapter-plugins.md`: Paket
  anmelden (`[project.entry-points."wortlaut.adapters"]`), `AdapterEntry` als Modulkonstante,
  Betreiber-Variablen, Deckelung und warum, Verweis auf das Konformitäts-Testkit. CI grün, 0 neue
  Sonar-Issues, alle bestehenden Tests grün (nur die Umbenennung aus §11 geändert).

## 6. Testplan

| AC | Datei | Art |
|---|---|---|
| AC1–AC4 (Kern) | `tests/unit/test_plugins.py` (neu) | Unit, Fake-Entry-Points |
| AC4 (CLI), AC5, AC6 | `tests/unit/test_cli_plugins.py` (neu) | Unit, echtes `importlib.metadata` in `tmp_path` |

## 7. Recht / Security

- Fremdcode läuft nur nach Freigabe; kein Selbst-Freischalten zum Veröffentlichen (R-SEC: Ingest
  ist Daten, Plugin-Code ist Betreiberentscheidung).
- Meldungen enthalten nie Ausnahmetexte aus Plugin-Code (R-SEC-07).
- `rights_basis` und `trust_level` werden gegen die Enums geprüft, bevor ein Plugin registriert wird.

## 8. Risiken

- Ein freigegebenes Plugin führt beliebigen Code beim Laden aus — das ist die Natur von Plugins;
  die Freigabe ist die Betreiberentscheidung. Die Doku sagt das deutlich.
- `secondary`-Spans sind `machine` und werden ohne menschliche Prüfung nie ausgespielt; das ist
  gewollt.

## 9. Definition of Done

Siehe `docs/engineering.md`. Kein Betriebsschritt (ohne gesetzte Variable ändert sich nichts).

## 10. Files

**Neu:** `src/wortlaut/ingest/plugins.py` · `src/wortlaut/py.typed` (leer) ·
`tests/unit/test_plugins.py` · `tests/unit/test_cli_plugins.py` · `docs/adapter-plugins.md`

**Ändern:** `src/wortlaut/ingest/registry.py` (zwei Felder) · `src/wortlaut/cli.py` (§4.4) ·
Tests, die `wortlaut.cli.default_registry` patchen (nur Umbenennung, §11)

## 11. Umsetzungsdetails

- `plugins.py` importiert: `importlib.metadata`, `collections.abc` (`Iterable`, `Sequence`),
  `datetime`, `typing.Protocol`, `pydantic_settings` (`BaseSettings`, `SettingsConfigDict`), aus
  `wortlaut.ingest.adapter` (`IngestAdapter`, `RawSource`, `SourceRef`, `SpanDraft`), aus
  `wortlaut.ingest.registry` (`AdapterEntry`, `AdapterRegistry`, `default_registry`), aus
  `wortlaut.ingest.rights` `RIGHTS_BASES`, aus `wortlaut.ingest.conformance` `TRUST_LEVELS`.
- Gepinnte Fabrik als verschachtelte `def` (kein Lambda), die `obj` und die Werte per Closure hält.
- **Umbenennung in Tests:** jedes `"wortlaut.cli.default_registry"` → `"wortlaut.cli.registry_from_env"`
  (nur der Patch-Zielstring; `registry_module.default_registry` als Ersatzwert bleibt).
- Sonar-Muster: ein Aufruf je `pytest.raises`-Block, ein Assert je Zeile, keine Lambdas in
  `patch()`, Literale ≥ 3× als Konstante, Funktionen kurz halten.

## 12. Do-NOT

- **Kein** `ep.load()` für nicht freigegebene oder kollidierende Namen.
- **Kein** Ausnahmetext aus Plugin-Code in Meldungen.
- **Keine** Änderung an Pipeline, Store, Serving, Migrationen, `dip.py`.
- **Kein** Modul-Zustand (keine globale Registry, kein Cache der Plugins).

## 13. Abschluss

- `git status --porcelain` ausgeben. **Sonst nichts.**
