# Increment-Spec: /verify-Fundament — Hash gegen WORM nachrechnen (#8)

- **Story/Issue:** #8 · **Status:** Draft · **Phase/Layer:** phase/0 · `pipeline` (+ rein `evidence`)
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#5** (WORM-Read), **#3** (content_hash), **#2** (source-schema).

## 1. Ziel
Das Nachprüfbarkeits-Fundament: eine `verify_source(...)`-Funktion, die die Rohbytes einer
`source` aus dem WORM neu hasht und gegen `source.content_hash` vergleicht — damit jede
Beweiskette **unabhängig verifizierbar** ist und Manipulation erkennbar wird (Threat T2,
Security §3.6). API-Endpoint (`GET /v1/spans/{id}/verify`) folgt in Phase 1.

## 2. Nicht-Ziele (Scope-Grenze)
- **Kein** API-Endpoint / kein Serving (Phase 1) — nur die aufrufbare Funktion + Report.
- **Kein** Span-Verify / Anti-Halluzination-Gate (das kommt mit dem Span-Increment, R-DATA-06) —
  #8 prüft die **Quelle**-Integrität (Rohbyte↔Hash), nicht den Span↔Quelle-Match.
- **Kein** Live-Check der Archivlinks (datamodel §4 „Archivlinks live") — die Archivlinks werden
  nur **durchgereicht**, nicht angepingt (kein Netz-Call).
- **Kein** Schreibpfad — `verify` ist rein lesend.

## 3. Betroffene Interfaces / Öffentliche Signaturen
```python
# src/wortlaut/pipeline/verify.py   (Orchestrierung: liest store + WORM, rechnet mit evidence)
@dataclass(frozen=True)
class VerifyReport:
    ok: bool
    source_id: UUID
    status: Literal["ok", "hash_mismatch", "source_not_found", "worm_missing"]
    content_hash_expected: str | None
    content_hash_actual: str | None
    archive_wayback: str | None
    archive_today: str | None

async def verify_source(source_id: UUID, *, session: AsyncSession, worm: WormStore) -> VerifyReport: ...
```
- **Layering (R-ARCH-02):** die **reine** Hash-Rechnung bleibt in `evidence` (#3, `content_hash`);
  die I/O-Orchestrierung (source laden, WORM lesen) lebt in `wortlaut.pipeline` — `evidence` bleibt
  I/O-frei. `pipeline` importiert `store`/`evidence`; niemand importiert `pipeline`.

## 4. Design (kurz)
- **Ablauf:** `source` laden (store, via `source_id`) → `raw_bytes_ref` in WORM-Key auflösen →
  `raw = await worm.get(key)` (#5) → `actual = content_hash(raw)` (#3) →
  `ok = (actual == source.content_hash)`.
- **Statusmatrix (definiert, kein falsches `ok`):**
  - Quelle fehlt → `source_not_found` (ok=False).
  - WORM-Objekt fehlt/`get` wirft NotFound → `worm_missing` (ok=False).
  - Hash ≠ → `hash_mismatch` (ok=False) — **Manipulation erkennbar** (T2).
  - alles passt → `ok` (ok=True) + `archive_wayback`/`archive_today` aus der `source` im Report.
- **Öffentlich nachrechenbar (T2):** dieselbe deterministische SHA-256 wie #3 → jeder kann den
  Wert gegen die verhashte Quelle nachrechnen; der Report macht `expected` vs. `actual` explizit.
- **`evidence` bleibt rein:** `verify_source` importiert `content_hash` (pure) + WORM/DB — die
  Reinheit von `evidence` (I/O-frei) bleibt gewahrt.

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)
- [ ] **AC1** *Given* eine `source` mit `content_hash = sha256(B)` und ein Fake-WORM, das `B`
      liefert, *When* `verify_source`, *Then* `ok is True`, `status=='ok'`, `actual==expected`,
      Archivlinks aus der `source` im Report. `[unit]`
- [ ] **AC2** *Given* dieselbe `source`, aber WORM liefert manipulierte Bytes `B' != B`, *When*
      `verify_source`, *Then* `ok is False`, `status=='hash_mismatch'`, `actual != expected`. `[unit]`
- [ ] **AC3** *Given* eine unbekannte `source_id`, *When* `verify_source`, *Then* `ok is False`,
      `status=='source_not_found'` (kein WORM-Zugriff). `[unit]`
- [ ] **AC4** *Given* eine `source`, deren WORM-Objekt fehlt (`worm.get` wirft NotFound), *When*
      `verify_source`, *Then* `ok is False`, `status=='worm_missing'` (**kein** falsches `ok`). `[unit]`
- [ ] **AC5** *Given* eine echte, via #7 eingefügte `source` + echtes MinIO/PG (Testcontainer),
      *When* `verify_source`, *Then* `ok is True`, `status=='ok'`, `expected==actual`. `[integration]`
- [ ] **AC6** *Given* der Happy path, *When* `verify_source`, *Then* die Archivlinks
      (`archive_wayback`/`archive_today`) im Report entsprechen exakt den Werten der `source`
      (Nachprüfbarkeit inkl. Archiv). `[unit]`
> Jedes AC ist von einem automatisierten Test mit Ja/Nein beantwortbar.

## 6. Testplan (Test-zu-AC-Mapping)
- **Unit (rein, Fake-WORM + In-Memory/Fixture-source):** `tests/unit/test_verify.py`
  - `test_verify_ok_intact` → AC1 · `test_verify_hash_mismatch` → AC2
  - `test_verify_source_not_found` → AC3 · `test_verify_worm_missing` → AC4
  - `test_verify_report_carries_archive_links` → AC6
- **Integration (Testcontainers PG **+** MinIO; source via #7-Insert oder direktes Fixture-Setup):**
  `tests/integration/test_verify_e2e.py`
  - `test_verify_e2e_intact_source` → AC5
- **Invariante (R-DATA-02, Beweis):** Manipulation → `hash_mismatch` ist der Kern-Invariantentest.

## 7. Recht / Security
- **Nachprüfbarkeit (Threat T2, Security §3.6):** öffentlich nachrechenbare Hash-Kette; jeder Beleg
  ist unabhängig gegen die verhashte Quelle prüfbar — Vorstufe des Anti-Halluzination-Gates (§5).
- **Hash über Rohbytes (R-DATA-02):** identische Berechnung wie #3, gegen `source.content_hash` (#2).
- **Read-only, kein LLM, kein Serving-Output (Phase 0):** kein R-SEC-04-Pfad; reines Lesen aus
  souveränem WORM/DB.

## 8. Risiken & offene Fragen / Entscheidungen
- **Große Blobs:** `worm.get` lädt aktuell ganz in den RAM; für sehr große Quellen wäre ein
  gestreamtes WORM-Read + `content_hash_stream` (#3 AC7) die Optimierung. Phase-0-Quellen
  (Protokoll-Text) sind moderat → **Vollbyte-Read jetzt, Streaming als notierte Zukunft** (kein AC).
- **Key-Ableitung aus `raw_bytes_ref`:** `s3://<bucket>/<key>` parsen; Format ist die #5-Konvention
  — defensiv parsen, unerwartetes Format → Fehler statt falsches `ok`.
- **Archivlink-Live-Check** (datamodel §4) ist bewusst **nicht** in #8 (kein Netz-Call, kein SSRF
  hier) — kommt mit dem Phase-1-`/verify`-Endpoint (Serving).

## 9. Definition of Done (Verweis)
[../docs/rules.md](../docs/rules.md) DoD: alle AC grün (Unit + Integration gegen echtes PG+MinIO),
alle Gates grün (Lint·Type·Test·Coverage ≥80, Security, Architektur, SonarCloud), Review (Architekt
+ **Security**, Beweis-Integrität), Manipulations-Erkennung getestet, kein Schreibpfad, `evidence`
bleibt rein. PR referenziert **#8**.
