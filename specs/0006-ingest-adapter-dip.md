# Increment-Spec: IngestAdapter-Interface + DIP-Plenarprotokoll-Adapter (#6)

- **Story/Issue:** #6 · **Status:** Draft · **Phase/Layer:** phase/0 · `ingest`
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **nichts** (unabhängiger Baustein); Referenz: [datamodel §7](../docs/datamodel.md).

## 1. Ziel
Die **Erweiterbarkeits-Naht** des Projekts: ein `IngestAdapter`-Protocol (datamodel §7)
plus ein erster, echter **DIP-Plenarprotokoll-Adapter**, der `discover`/`fetch`/`normalize`
kann — damit neue Quellen als je *eine* Adapter-Datei angebunden werden, **ohne** den
Kern (Hashing/Archiv/WORM) anzufassen (R-ARCH-01).

## 2. Nicht-Ziele (Scope-Grenze)
- **Kein** `parse`-to-Span in diesem Increment — Spans/`SpanDraft` sind **Phase 1**.
  Der Adapter deklariert `parse` (Protocol-Vollständigkeit), implementiert es aber als
  bewusstes `NotImplementedError`.
- **Kein** Hashing/Archiv/WORM/Insert im Adapter (macht der Kern, #3/#4/#5/#7) — der Adapter
  darf die Beweiskette **nicht** umgehen (Security §3.3 „Provenienz vor Verarbeitung").
- **Kein** Live-DIP-Call im Test (R-TEST-03) — nur gegen aufgezeichnete Fixtures.
- **Kein** weiterer Quellentyp (Drucksachen/Landtage kommen als eigene Adapter später).

## 3. Betroffene Interfaces / Öffentliche Signaturen
```python
# src/wortlaut/ingest/adapter.py   (die Naht, datamodel §7 — dataclasses + Protocol)
@dataclass(frozen=True)
class SourceRef:   origin_url: str; source_type: str; hint: dict
@dataclass(frozen=True)
class RawSource:   origin_url: str; source_type: str; raw_bytes: bytes; mime_type: str; retrieved_at: datetime
@dataclass(frozen=True)
class SpanDraft:   verbatim_text: str; text_start: int; text_end: int; speaker_hint: dict; spoken_at: str; locator: dict; permalink: str

@runtime_checkable
class IngestAdapter(Protocol):
    name: str
    version: str
    trust_level: str                                   # 'verified_primary' | 'secondary' | 'low'
    async def discover(self, since: datetime) -> Sequence[SourceRef]: ...
    async def fetch(self, ref: SourceRef) -> RawSource: ...
    def normalize(self, raw: RawSource) -> str: ...    # kanonischer Klartext (rein)
    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]: ...  # Phase 1

# src/wortlaut/ingest/dip.py   (erster Adapter: Bundestag DIP-API, Plenarprotokolle)
class DipPlenarprotokollAdapter:                       # erfüllt IngestAdapter
    name = "dip-api"; version = "1.0.0"; trust_level = "verified_primary"
    def __init__(self, settings: "DipSettings") -> None: ...
    async def discover(self, since) -> Sequence[SourceRef]: ...   # host-pinned dip.bundestag.de
    async def fetch(self, ref) -> RawSource: ...
    def normalize(self, raw) -> str: ...
    def parse(self, raw, normalized) -> Sequence[SpanDraft]:
        raise NotImplementedError("parse-to-span ist Phase 1")

# src/wortlaut/ingest/settings.py   (pydantic-settings, ENV)
class DipSettings(BaseSettings):
    api_key: str; base_url: str = "https://search.dip.bundestag.de/api/v1"
```
- **Layering (R-ARCH-02):** `ingest` importiert **nicht** `evidence`/`store`/`archive`/`pipeline`
  — der Adapter kennt kein Hashing/Archiv/Storage. Diese Isolation ist testbar (AC über Imports).
- **Async I/O** (`discover`/`fetch`) statt der Sync-Skizze in §7 — bewusst, weil sie Netz-I/O
  tun und der Kern async ist. `normalize`/`parse` bleiben rein/sync.

## 4. Design (kurz)
- **Ziel-Entität Plenarprotokolle (Q4):** `source_type='plenarprotokoll'` — reiner amtlicher
  Wortlaut mit amtlicher Sprecherzuordnung, ideale Basis für Phase-1-Spans. `trust_level=
  verified_primary` (datamodel §6: darf später `official`).
- **Host-pinned fetch:** `discover`/`fetch` sprechen **ausschließlich** `dip.bundestag.de` an
  (aus `DipSettings.base_url`) → keine arbitrary-URL-SSRF-Fläche, **#6 bleibt unabhängig von #4**.
  Ein `ref.origin_url` mit fremdem Host wird abgelehnt.
- **`fetch` liefert Rohbytes unverändert:** `RawSource.raw_bytes` = die exakten Response-Bytes
  der DIP-`…-text`-Ressource → genau diese Bytes hasht/archiviert/WORM-t der Kern (#3/#4/#5).
- **`normalize` ist deterministisch & rein:** gleiche `RawSource` → gleicher kanonischer Klartext
  (Grundlage für spätere Offsets/FTS); keine I/O, kein Zufall.
- **`rights_basis` setzt der Adapter nicht** — DIP=`amtliches_werk_p5` ist Quelltyp-Policy des
  Kerns (#7 setzt es beim Insert). Der Adapter liefert nur `source_type`.
- **DIP-API-Key via ENV** (R-SEC-01); der öffentliche DIP-Key ist trotzdem nie im Repo.

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)
- [ ] **AC1** *Given* `DipPlenarprotokollAdapter`, *When* `isinstance(adapter, IngestAdapter)`
      (runtime_checkable) geprüft, *Then* `True` **und** `name/version/trust_level` vorhanden. `[unit]`
- [ ] **AC2** *Given* DIP-Discover-Fixture (Liste Protokolle seit Datum), *When* `discover(since)`,
      *Then* N `SourceRef` mit `source_type=='plenarprotokoll'` und den erwarteten `origin_url`. `[unit]`
- [ ] **AC3** *Given* `SourceRef` + Fetch-Fixture (Response-Bytes `B`), *When* `fetch(ref)` (httpx
      gemockt), *Then* `RawSource.raw_bytes == B`, `mime_type`/`retrieved_at` gesetzt, **kein**
      Live-Call. `[unit]`
- [ ] **AC4** *Given* `RawSource` aus Fixture, *When* `normalize(raw)` zweimal, *Then* identischer,
      erwarteter kanonischer Klartext (deterministisch/idempotent). `[unit]`
- [ ] **AC5** *Given* ein `ref.origin_url` mit fremdem Host (nicht `dip.bundestag.de`), *When*
      `fetch(ref)`, *Then* Fehler (Host-Pinning), **kein** Request an den fremden Host. `[unit]`
- [ ] **AC6** *Given* der Adapter in Phase 0, *When* `parse(raw, normalized)`, *Then*
      `NotImplementedError` (Span-Parsing bewusst Phase 1). `[unit]`
- [ ] **AC7** *Given* das Paket `wortlaut.ingest`, *When* die Imports statisch geprüft werden,
      *Then* es importiert **nicht** `wortlaut.evidence`/`store`/`archive`/`pipeline` (Adapter greift
      nicht in den Kern, R-ARCH-02). `[unit]`
> Jedes AC ist von einem automatisierten Test mit Ja/Nein beantwortbar.

## 6. Testplan (Test-zu-AC-Mapping)
- **Unit (rein, httpx gemockt, DIP-Fixtures):** `tests/unit/test_dip_adapter.py`
  - `test_adapter_satisfies_protocol` → AC1 · `test_discover_yields_source_refs` → AC2
  - `test_fetch_returns_raw_bytes` → AC3 · `test_normalize_deterministic` → AC4
  - `test_fetch_rejects_offhost_ref` → AC5 · `test_parse_not_implemented_phase0` → AC6
- **Unit Layering:** `tests/unit/test_ingest_layering.py`
  - `test_ingest_does_not_import_core` (AST/import-Scan über `wortlaut.ingest`) → AC7
  - *(zusätzlich abgesichert durch die import-linter-Contract-Erweiterung in #7.)*
- **Fixtures:** aufgezeichnete DIP-JSON/-Text-Antworten unter `tests/fixtures/dip/` (kein Live-Call).

## 7. Recht / Security
- **DIP = amtliches Werk (§5 UrhG, Legal §3.1):** `rights_basis=amtliches_werk_p5` — der rechtlich
  sichere MVP-Hafen; Sprecher amtlich zugeordnet (kein Diarization-Risiko).
- **Host-Pinning statt SSRF-Client:** feste, vertrauenswürdige API → minimale Fetch-Fläche
  (R-SEC-05 sinngemäß); Adapter kennt keinen arbitrary-URL-Fetch.
- **Adapter greift nicht in den Kern (R-ARCH-01/02, Security §3.3):** kein Hashing/Archiv/Storage
  → kann die Beweiskette nicht umgehen. **Keine Live-Calls in Unit-Tests (R-TEST-03).**

## 8. Risiken & offene Fragen / Entscheidungen
- **Entscheidung (Q4):** Ziel = **Plenarprotokolle**, `fetch` host-pinned → #6 unabhängig von #4.
- **DIP-API-Konkreta** (genaues `…/plenarprotokoll` vs. `/plenarprotokoll-text`-Endpoint,
  Pagination-Cursor, Rohbyte = JSON-Envelope oder eingebetteter Volltext) beim Bau final gegen die
  echte API fixieren; Fixtures daraus aufzeichnen. Rohbyte-Definition (was genau gehasht wird)
  ist beweisrelevant → in der Spec-Umsetzung explizit festlegen.
- **`normalize`-Kanonisierung:** Whitespace/Encoding-Regeln deterministisch definieren (spätere
  Offsets hängen daran) — als eigener, gut getesteter Schritt.

## 9. Definition of Done (Verweis)
[../docs/rules.md](../docs/rules.md) DoD: alle AC grün (Unit gegen Fixtures), alle Gates grün
(Lint·Type·Test·Coverage ≥80, Security, Architektur inkl. import-linter, SonarCloud), Review
(Architekt; Security wegen Ingest-Pfad), Interface-first (Protocol), keine Live-Calls, kein
Secret. PR referenziert **#6**.
