# Increment-Spec: Ingest-Pipeline-Kern (fetch→hash→dedup→archiv→WORM→insert) (#7)

- **Story/Issue:** #7 · **Status:** Draft · **Phase/Layer:** phase/0 · `pipeline` (neues Paket)
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#3** (hash+dedup), **#4** (archiv), **#5** (WORM), **#6** (adapter), **#2** (source-schema).

## 1. Ziel
Der **Kern-Orchestrator**, der eine entdeckte Quelle vollständig und **in der erzwungenen
Reihenfolge** in den Ledger bringt: `fetch → hash → dedup → fremd-archivieren → WORM →
insert source`. Er komponiert die Bausteine #3–#6, damit **keine Verarbeitung vor
Provenienz** stattfindet (README §1.1, Security §3.3 „Provenienz vor Verarbeitung [ARCH]").

## 2. Nicht-Ziele (Scope-Grenze)
- **Kein** AI-Schritt, **kein** `parse`/Span/Embedding (Phase 1) — die Pipeline endet nach
  `insert source`.
- **Kein** neuer Baustein — #7 **komponiert** nur #3/#4/#5/#6; neue Funktion hier ist allein
  der Orchestrator + `insert_source` (store).
- **Keine** Queue/kein Scheduler/kein Retry-Framework — synchroner Einzel-Durchlauf pro Quelle.
- **Kein** Serving/API-Endpoint (`POST /v1/ingest` ist Phase 1).

## 3. Betroffene Interfaces / Öffentliche Signaturen
```python
# src/wortlaut/pipeline/ingest.py   (der Orchestrator; komponiert ingest+evidence+archive+store)
@dataclass(frozen=True)
class IngestOutcome:
    status: Literal["inserted", "skipped_duplicate", "archive_failed"]
    source_id: UUID | None
    content_hash: str

async def ingest_source(
    ref: SourceRef, *,
    adapter: IngestAdapter, archiver_wayback: Archiver, archiver_today: Archiver,
    worm: WormStore, session: AsyncSession, rights_basis: str,
) -> IngestOutcome: ...

# src/wortlaut/store/sources.py   (erweitert #3: neben source_exists nun der Insert)
async def insert_source(session: AsyncSession, *, content_hash: str, raw_bytes_ref: str,
                        archive_wayback: str | None, archive_today: str | None,
                        origin_url: str, source_type: str, rights_basis: str,
                        adapter_name: str, adapter_version: str, byte_size: int,
                        mime_type: str, retrieved_at: datetime, normalized_text: str) -> UUID: ...
```
- **Layering (R-ARCH-02), neu:** `wortlaut.pipeline` darf `ingest`/`evidence`/`archive`/`store`
  importieren; **nichts** importiert `pipeline`; `serving` bleibt für alle tabu. Der
  **import-linter-Contract wird erweitert** (siehe §8) — sonst greift das Architektur-Gate für
  das neue Paket nicht.

## 4. Design (kurz)
- **Erzwungene Reihenfolge (Kern-Invariante):**
  1. `raw = await adapter.fetch(ref)`
  2. `h = content_hash(raw.raw_bytes)` (#3, über **Rohbytes**)
  3. `if await source_exists(session, h): return skipped_duplicate` (#3, Dedup-Vorabcheck)
  4. `res = await archive_all(raw.origin_url, …)` (#4) — **VOR** jeder Verarbeitung
  5. `if res.wayback_url is None and res.archive_today_url is None: return archive_failed` (kein Insert)
  6. `ref_ = await worm.put(h, raw.raw_bytes, content_type=raw.mime_type)` (#5)
  7. `norm = adapter.normalize(raw)`; `sid = await insert_source(…, content_hash=h, raw_bytes_ref=ref_, …)`
- **Archiv-Fehlschlag beider → kein `source`-Insert (AC):** `public_evidence`/`chk_archive` (#2,
  Legal §10) wären sonst verletzt. **≥1** Archiv reicht (chk_archive braucht ≥1).
- **Dedup-Skip (AC):** bekannter Hash → früher Ausstieg, **kein** Archiv/WORM/Insert-Call.
- **Race/UNIQUE — das in #3 angekündigte „saubere Abfangen":** bei parallelem Ingest kann
  `source_exists` False/False liefern; der zweite `insert_source` scheitert am UNIQUE (#2). #7
  **fängt** die `IntegrityError` und meldet `skipped_duplicate` — die DB bleibt die Wahrheit,
  genau eine Zeile.
- **Kein AI/parse (Phase 0):** `adapter.parse` wird nie aufgerufen; Pipeline endet nach Insert.
- **Content-adressierter WORM-Key:** #7 nutzt `content_hash` als WORM-Key (Konvention des
  Aufrufers; der Adapter #5 bleibt key-generisch).

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)
- [ ] **AC1** *Given* Fakes für adapter/archiver/worm/insert mit Aufruf-Recorder, *When*
      `ingest_source` (Happy path), *Then* Aufrufreihenfolge exakt
      `fetch → content_hash → source_exists → archive_all → worm.put → insert_source`. `[unit]`
- [ ] **AC2** *Given* frische DB + echte Bausteine (Testcontainer PG+MinIO, gemockter Archiver),
      *When* `ingest_source` einer neuen Quelle, *Then* `status=='inserted'`, `source_id` gesetzt,
      genau eine `source`-Zeile mit dem erwarteten `content_hash`/`raw_bytes_ref`/Archivlinks. `[integration]`
- [ ] **AC3** *Given* eine `source` mit Hash `H` existiert, *When* `ingest_source` derselben Bytes,
      *Then* `status=='skipped_duplicate'`, **kein** `archive_all`/`worm.put`/`insert` (Recorder = 0),
      keine zweite Zeile. `[integration]`
- [ ] **AC4** *Given* Archiver liefert **beide** URLs `None`, *When* `ingest_source`, *Then*
      `status=='archive_failed'`, **kein** `worm.put`, **kein** `insert_source`. `[unit]`
- [ ] **AC5** *Given* nur `wayback_url` gesetzt (archive.today None), *When* `ingest_source`, *Then*
      Insert erfolgt (chk_archive ≥1), `archive_today` in der Zeile ist NULL. `[integration]`
- [ ] **AC6** *Given* zwei nebenläufige `ingest_source` derselben Bytes (source_exists beide False),
      *When* beide `insert_source`, *Then* genau **eine** Zeile; der zweite Outcome ist
      `skipped_duplicate` (IntegrityError sauber gefangen). `[integration]`
- [ ] **AC7** *Given* der Happy path, *When* `ingest_source`, *Then* `adapter.parse` wird **nie**
      aufgerufen (Recorder = 0) — kein AI/Span in Phase 0. `[unit]`
> Jedes AC ist von einem automatisierten Test mit Ja/Nein beantwortbar.

## 6. Testplan (Test-zu-AC-Mapping)
- **Unit (rein, Fakes mit Recorder):** `tests/unit/test_pipeline_order.py`
  - `test_happy_path_call_order` → AC1 · `test_archive_total_failure_no_insert` → AC4
  - `test_partial_archive_inserts` (Fake-DB-Grenze) / bzw. integration → AC5
  - `test_parse_never_called_phase0` → AC7
- **Integration (Testcontainers PG **+** MinIO; gemockter Archiver, R-TEST-03):**
  `tests/integration/test_pipeline_ingest.py`
  - `test_new_source_inserted` → AC2 · `test_duplicate_skipped_no_side_effects` → AC3
  - `test_partial_archive_inserts` → AC5
  - `test_concurrent_ingest_unique_race` → AC6 (zwei Tasks, `asyncio.gather`)
- **Invariante (R-DATA-01/-02):** Provenienz-vor-Verarbeitung (Reihenfolge) + „kein Insert ohne
  Archiv" sind Pflicht-Tests; die Race-/Unique-Garantie ist Integration gegen echtes PG.

## 7. Recht / Security
- **Provenienz vor Verarbeitung (R-CORE-02, Security §3.3 [ARCH], README §1.1):** Hash+Archiv+WORM
  strikt **vor** Insert; kein Verarbeiten ohne belegte Herkunft.
- **public_evidence/chk_archive (Legal §10, #2):** kein `source`-Insert ohne ≥1 Fremdarchiv.
- **rights_basis Pflicht (R-DATA-03):** wird als Parameter gesetzt (`amtliches_werk_p5` für DIP),
  NOT NULL (#2). **Immutability (R-DATA-01):** Insert erzeugt append-only Zeilen (#2-Trigger).
- **Layering (R-ARCH-02):** neuer import-linter-Contract hält `pipeline`→innen, `serving` tabu.

## 8. Risiken & offene Fragen / Entscheidungen
- **import-linter-Contract erweitern (Pflicht):** neuer Layers-/Forbidden-Contract für
  `wortlaut.pipeline` (darf ingest/evidence/archive/store importieren; niemand importiert pipeline;
  serving bleibt für alle verboten). Ohne das greift das Architektur-Gate für das neue Paket nicht.
- **Verwaister WORM-Blob:** WORM.put erfolgreich, danach Insert scheitert (z.B. Race) → Blob bleibt
  liegen (append-only, kein Datenverlust; Retry dedupt über `content_hash`). Bewusst akzeptiert;
  keine verteilte Transaktion in Phase 0.
- **Transaktionsgrenze:** DB-Insert ist atomar; Archiv/WORM sind externe Seiteneffekte außerhalb der
  DB-TX — die erzwungene Reihenfolge (Archiv/WORM **vor** Insert) ist die Kompensationsstrategie.
- **`insert_source`-Ort:** schlichte Modulfunktion in `store/sources.py` (kein Repository-Overhead),
  konsistent mit `source_exists` (#3).

## 9. Definition of Done (Verweis)
[../docs/rules.md](../docs/rules.md) DoD: alle AC grün (Unit + Integration gegen echtes PG+MinIO),
alle Gates grün (Lint·Type·Test·Coverage ≥80, Security, Architektur **inkl. erweitertem
import-linter-Contract**, SonarCloud), Review (Architekt + **Security**, Provenienz-Pfad),
Reihenfolge-/Provenienz-Invariante gewahrt, keine Gott-Klassen, kein AI im Phase-0-Pfad.
PR referenziert **#7**.
