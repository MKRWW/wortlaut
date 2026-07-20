# Increment-Spec: IngestAdapter-Interface + DIP-Plenarprotokoll-Adapter (#6)

- **Story/Issue:** #6 · **Status:** Reviewed · **Phase/Layer:** phase/0 · `ingest`
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **nichts** (unabhängiger Baustein); Referenz: [datamodel §7](../docs/datamodel.md).

## 1. Ziel
Die **Erweiterbarkeits-Naht** des Projekts: ein `IngestAdapter`-Protocol (datamodel §7)
plus ein erster, echter **DIP-Plenarprotokoll-Adapter**, der amtliche Plenarprotokolle
des Bundestags **entdeckt** (`discover`) und das **amtliche PDF holt** (`fetch`) — damit
neue Quellen als je *eine* Adapter-Datei angebunden werden, **ohne** den Kern
(Hashing/Archiv/WORM) anzufassen (R-ARCH-01).

## 2. Nicht-Ziele (Scope-Grenze)
- **Kein** `normalize` (PDF→Text) und **kein** `parse`-to-Span in diesem Increment — beide
  sind **Phase 1** (Text/Spans/Offsets). Der Adapter **deklariert** sie (Protocol-Vollständigkeit),
  implementiert sie aber als bewusstes `NotImplementedError`.
- **Kein** Hashing/Archiv/WORM/Insert im Adapter (macht der Kern, #3/#4/#5/#7) — der Adapter
  darf die Beweiskette **nicht** umgehen (Security §3.3 „Provenienz vor Verarbeitung").
- **Kein** Live-DIP-Call im Test (R-TEST-03) — nur gegen aufgezeichnete Fixtures.
- **Kein** PDF-Parsing (kommt mit `normalize` in Phase 1, inkl. Sandbox-Erwägung Security §3.4).
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
    def normalize(self, raw: RawSource) -> str: ...    # Phase 1 (PDF→Text)
    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]: ...  # Phase 1

# src/wortlaut/ingest/dip.py   (erster Adapter: Bundestag DIP-API → amtliches PDF)
class DipPlenarprotokollAdapter:                       # erfüllt IngestAdapter
    name = "dip-api"; version = "1.0.0"; trust_level = "verified_primary"
    def __init__(self, settings: "DipSettings") -> None: ...
    async def discover(self, since) -> Sequence[SourceRef]:
        """DIP-Metadaten-Endpoint (plenarprotokoll) → je Protokoll ein SourceRef mit
        origin_url = fundstelle.pdf_url (das amtliche PDF)."""
    async def fetch(self, ref) -> RawSource:
        """Holt die PDF-Bytes von ref.origin_url (host-pinned). raw_bytes = exakte PDF-Bytes."""
    def normalize(self, raw) -> str:
        raise NotImplementedError("PDF→Text ist Phase 1")
    def parse(self, raw, normalized) -> Sequence[SpanDraft]:
        raise NotImplementedError("parse-to-span ist Phase 1")

# src/wortlaut/ingest/settings.py   (pydantic-settings, ENV WORTLAUT_DIP_)
class DipSettings(BaseSettings):
    api_key: str
    api_base_url: str = "https://search.dip.bundestag.de/api/v1"
    pdf_host: str = "dserver.bundestag.de"             # amtlicher PDF-Host (beim Bauen final verifizieren)
```
- **Layering (R-ARCH-02):** `ingest` importiert **nicht** `evidence`/`store`/`archive`/`pipeline`
  — der Adapter kennt kein Hashing/Archiv/Storage. Diese Isolation ist testbar (AC über Imports).
- **Async I/O** (`discover`/`fetch`) statt der Sync-Skizze in §7 — bewusst, weil sie Netz-I/O tun.

## 4. Design (kurz)
- **PDF als Beweis-Anker (Stakeholder-Entscheidung 2026-07-20):** `fetch` holt das **amtliche PDF**
  (`fundstelle.pdf_url`), nicht die DIP-JSON. Das PDF ist byte-stabil, das *zitierbare* amtliche
  Dokument und damit forensisch belastbar. Der Kern hasht/archiviert/WORM-t genau diese PDF-Bytes (#3/#4/#5),
  `origin_url = pdf_url` → Fremdarchiv (#4) sichert dieselbe URL. `content_hash` (Kern) = SHA-256 über die PDF-Bytes.
- **`normalize`/`parse` bewusst Phase 1:** Phase 0 ist „rein, gehasht, fremdarchiviert, unveränderlich"
  (architecture §7) — **ohne** Textverarbeitung. `source.normalized_text` bleibt in Phase 0 **NULL**
  (Schema erlaubt es, #2). Die PDF→Text-Extraktion (und das daran hängende Byte-Match/Anti-Halluzinations-Gate)
  wird in Phase 1 deterministisch mit den Spans zusammen designt → **kein fragiles PDF-Parsing im Phase-0-Vertrauenspfad**.
- **`discover` über DIP-Metadaten:** Query gegen `…/api/v1/plenarprotokoll` (Filter `f.datum.start=since`,
  `f.zuordnung=BT`), Cursor-Pagination; je Dokument ein `SourceRef(origin_url=fundstelle.pdf_url,
  source_type='plenarprotokoll', hint={dokumentnummer, datum, wahlperiode, dip_id})`.
- **Host-Pinning (zwei amtliche Hosts):** `discover` trifft `dip.bundestag.de` (API), `fetch` trifft den
  **PDF-Host** (`dserver.bundestag.de` o.ä.). `fetch` lehnt jede `origin_url` ab, deren Host **nicht** in der
  Allowlist {API-Host, PDF-Host} liegt → keine Arbitrary-URL-SSRF-Fläche (#6 unabhängig von #4).
- **`rights_basis` setzt der Adapter nicht** — DIP=`amtliches_werk_p5` ist Quelltyp-Policy des Kerns (#7).
- **DIP-API-Key via ENV** (R-SEC-01).

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)
- [ ] **AC1** *Given* `DipPlenarprotokollAdapter`, *When* `isinstance(adapter, IngestAdapter)`
      (runtime_checkable) geprüft, *Then* `True` **und** `name/version/trust_level` vorhanden. `[unit]`
- [ ] **AC2** *Given* DIP-Discover-Fixture (Liste Protokolle seit Datum), *When* `discover(since)`,
      *Then* N `SourceRef` mit `source_type=='plenarprotokoll'` und `origin_url == fundstelle.pdf_url`
      des jeweiligen Dokuments. `[unit]`
- [ ] **AC3** *Given* `SourceRef` (origin_url = PDF auf dem PDF-Host) + Fetch-Fixture (PDF-Bytes `B`),
      *When* `fetch(ref)` (httpx gemockt), *Then* `RawSource.raw_bytes == B`, `mime_type=='application/pdf'`,
      `retrieved_at` gesetzt, **kein** Live-Call. `[unit]`
- [ ] **AC4** *Given* ein `ref.origin_url` mit Host **außerhalb** der Allowlist ({API-Host, PDF-Host}),
      *When* `fetch(ref)`, *Then* Fehler (Host-Pinning), **kein** Request an den fremden Host. `[unit]`
- [ ] **AC5** *Given* der Adapter in Phase 0, *When* `normalize(raw)` bzw. `parse(raw, "")`, *Then* je
      `NotImplementedError` (Text/Spans bewusst Phase 1). `[unit]`
- [ ] **AC6** *Given* das Paket `wortlaut.ingest`, *When* die Imports statisch geprüft werden, *Then* es
      importiert **nicht** `wortlaut.evidence`/`store`/`archive`/`pipeline` (Adapter greift nicht in den Kern,
      R-ARCH-02). `[unit]`
- [ ] **AC7** *Given* der Adapter, *When* die Attribute gelesen, *Then* `trust_level == 'verified_primary'`
      und `name`/`version` stabil (`'dip-api'`/`'1.0.0'`). `[unit]`
> Jedes AC ist von einem automatisierten Test mit Ja/Nein beantwortbar.

## 6. Testplan (Test-zu-AC-Mapping)
- **Unit (rein, httpx gemockt, Fixtures):** `tests/unit/test_dip_adapter.py`
  - `test_adapter_satisfies_protocol` → AC1 · `test_discover_yields_pdf_source_refs` → AC2
  - `test_fetch_returns_pdf_bytes` → AC3 · `test_fetch_rejects_offhost_ref` → AC4
  - `test_normalize_and_parse_not_implemented_phase0` → AC5 · `test_trust_level_and_identity` → AC7
- **Unit Layering:** `tests/unit/test_ingest_layering.py`
  - `test_ingest_does_not_import_core` (AST/import-Scan über `wortlaut.ingest`) → AC6
- **Fixtures:** `tests/fixtures/dip/` — aufgezeichnete DIP-`plenarprotokoll`-JSON (Discover) + eine kleine
  PDF-Bytefolge (Fetch). **Kein Live-Call.** Exakte Feld-/Hostnamen beim Bauen gegen die echte DIP-API
  (mit API-Key) verifizieren.

## 7. Recht / Security
- **DIP = amtliches Werk (§5 UrhG, Legal §3.1):** `rights_basis=amtliches_werk_p5` (Kern setzt es, #7) — der
  rechtlich sichere MVP-Hafen; Sprecher amtlich zugeordnet.
- **PDF-Anker = forensische Belastbarkeit:** byte-stabiles amtliches Dokument, gegen die öffentliche Quelle
  nachrechenbar; Fremdarchiv (#4) sichert dieselbe `pdf_url` (T1/T2).
- **Host-Pinning statt SSRF-Client:** feste, amtliche Hosts → minimale Fetch-Fläche (R-SEC-05 sinngemäß).
- **Kein PDF-Parsing in Phase 0:** die Untrusted-Parsing-Fläche (Security §3.4, R-SEC-06) entsteht erst mit
  `normalize` in Phase 1 und wird dort adressiert.
- **Adapter greift nicht in den Kern (R-ARCH-01/02):** kein Hashing/Archiv/Storage. **Keine Live-Calls im Test** (R-TEST-03).

## 8. Risiken & offene Fragen / Entscheidungen
- **Entscheidung (2026-07-20):** Beweis-Anker = **amtliches PDF** (nicht DIP-JSON) → forensisch belastbar;
  `normalize`/`parse` → **Phase 1** (kein PDF-Parsing im Phase-0-Vertrauenspfad).
- **Exakte DIP-Konkreta beim Bauen fixieren** (mit API-Key): genauer `fundstelle.pdf_url`-Aufbau, der reale
  **PDF-Host** (Annahme `dserver.bundestag.de`), Cursor-Pagination, Filter-Parameter. Fixtures daraus ableiten.
- **DIP-JSON könnte volatil sein** (Envelope-Zeitstempel) — deshalb hashen wir bewusst das **PDF**, nicht die JSON.
- **`fetch` zwei Hosts:** API-Host (discover) + PDF-Host (fetch) → beide in die Allowlist (nicht nur `dip.bundestag.de`).

## 9. Definition of Done (Verweis)
[../docs/rules.md](../docs/rules.md) DoD: alle AC grün (Unit gegen Fixtures), alle Gates grün
(Lint·Type·Test·Coverage ≥80, Security, Architektur inkl. import-linter, SonarCloud), Review
(Architekt; Security wegen Ingest-Pfad), Interface-first (Protocol), keine Live-Calls, kein
Secret. PR referenziert **#6**.
