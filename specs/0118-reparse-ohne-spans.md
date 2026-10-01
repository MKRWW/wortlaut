# Increment-Spec: Spans für Quellen ohne Spans nachziehen (#118)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #118 · **Status:** Reviewed · **Phase/Layer:** phase/1-mvp · `pipeline`, `store`, `cli`
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#42** (Span-Insert), **#70** (Sprecher-Marker), **#76** (abgeleiteter
  Rückstand ohne Status-Flag, Muster `list_sources_without_timestamp`).
- Schließt die letzte Lücke von **#93** AC4 (Protokoll 21/90).

## 0. Ausgangslage

Protokoll **21/90** liegt seit dem 05.08.2026 im Ledger, hat aber **keinen Span**. Der
gespeicherte `normalized_text` ist sauber (62 × `(AfD)`, Kopfzeilen wie `Marc Bernhard (AfD):`).
Die Quelle wurde **vor** dem Fix des Sprecher-Markers (#70) erfasst; damals lieferte der Parser
null Treffer. Ein erneuter `ingest` meldet für sie `skipped_duplicate`, **bevor** irgendetwas
geparst wird (`pipeline/ingest.py`, Schritt 3). Es gibt keinen Weg, die Spans einer vorhandenen
Quelle zu erzeugen.

### 0a. Vorklärung: warum nur Quellen **ohne** Spans

`span` ist per Trigger append-only (`trg_span_immutable`, R-DATA-01) und hat **keinen**
UNIQUE-Schlüssel, der Duplikate verhindert. Eine Quelle mit Spans erneut zu parsen, würde jeden
Span ein zweites Mal anlegen. Dieses Increment fasst deshalb ausschließlich Quellen an, zu denen
**keine einzige** span-Zeile existiert. „Ohne Spans" ist **abgeleitet** (keine Zeile) — kein
Status-Flag, kein UPDATE; dasselbe Muster wie der Zeitstempel-Rückstand aus #76.

### 0b. Vorklärung: die Wiederverwendung erzwingt einen Umzug

Die Span-Logik steckt heute privat in `pipeline/ingest.py::_ingest_spans`. Ein Import von dort
würde `reparse` an `pipeline/ingest.py` hängen und damit **indirekt** an `wortlaut.archive` —
import-linter wertet bei `forbidden`-Contracts auch indirekte Ketten. Der Netzfreiheits-Contract
aus §4.4 wäre dann rot. Deshalb zieht die Funktion **unverändert** in ein eigenes Modul
`pipeline/spans.py` um; `ingest.py` importiert sie von dort. Ein Weg für beide, keine Kopie.

### 0c. Vorklärung: Nebenläufigkeit

Zwei gleichzeitige `reparse`-Läufe könnten dieselbe Quelle beide als „ohne Spans" sehen und
beide schreiben — die Datenbank würde es nicht verhindern (0a). Abhilfe ohne Schemaänderung:
**Zeilensperre** auf die `source` (`SELECT … FOR UPDATE`) und **Nachprüfung** in derselben
Transaktion. `FOR UPDATE` löst keinen UPDATE-Trigger aus; der Append-only-Trigger bleibt
unberührt. Der zweite Lauf wartet auf die Sperre, sieht danach die Spans und überspringt.

## 1. Ziel

Ein neues Kommando `python -m wortlaut reparse` erzeugt für jede Quelle ohne Spans die Spans aus
ihrem **gespeicherten** Text — mit genau der Logik, die auch `ingest` benutzt, ohne Netzzugriff,
ohne Schemaänderung, idempotent und sicher gegen Doppel-Läufe.

## 2. Nicht-Ziele (Scope-Grenze)

- **Kein** Ersetzen, Löschen oder Ergänzen vorhandener Spans. Quellen mit ≥ 1 Span sind tabu.
- **Keine** Parser-Versionierung, kein Supersede-Mechanismus (eigenes Ticket mit ADR, falls gewollt).
- **Kein** erneutes `normalize`. Der eingefrorene `normalized_text` ist die Grundlage (R-DATA-06).
- **Keine** Migration, keine Schemaänderung.
- **Keine** Änderung am Verhalten von `ingest` (nur der Umzug aus 0b).
- **Kein** Fetch, keine Fremdarchivierung, kein Zeitstempel.

## 3. Betroffene Interfaces / Öffentliche Signaturen

```python
# src/wortlaut/pipeline/spans.py — NEU (Umzug aus pipeline/ingest.py, Logik unverändert)
async def write_spans(
    session: AsyncSession,
    *,
    adapter: IngestAdapter,
    raw: RawSource,
    normalized: str,
    source_id: UUID,
) -> int: ...

# src/wortlaut/store/reparse.py — NEU
@dataclass(frozen=True)
class SpanlessSource:
    source_id: UUID
    content_hash: str
    raw_bytes_ref: str
    origin_url: str
    source_type: str
    mime_type: str
    retrieved_at: datetime
    normalized_text: str | None

async def list_sources_without_spans(
    session: AsyncSession, *, adapter_name: str, limit: int | None = None
) -> list[SpanlessSource]: ...

async def lock_source_if_spanless(session: AsyncSession, source_id: UUID) -> bool: ...

# src/wortlaut/pipeline/reparse.py — NEU
@dataclass(frozen=True)
class ReparseOutcome:
    status: Literal[
        "reparsed", "still_empty", "no_text", "skipped_has_spans",
        "hash_mismatch", "worm_missing", "error",
    ]
    source_id: UUID
    span_count: int = 0

async def reparse_source(
    source: SpanlessSource,
    *,
    session: AsyncSession,
    worm: WormStore,
    adapter: IngestAdapter,
) -> ReparseOutcome: ...

# src/wortlaut/cli.py — neues Subcommand
#   python -m wortlaut reparse [--limit N] [--dry-run] [--no-migrate]
```

## 4. Design (kurz) — die vier Entscheidungen

### 4.1 Auswahl: abgeleitet, gefiltert nach Adapter

`list_sources_without_spans` liefert alle `source`-Zeilen, für die **keine** `span`-Zeile mit
`span.source_id = source.id` existiert **und** deren `adapter_name` dem übergebenen Namen
entspricht. Sortierung stabil nach `created_at, id`; optionales `limit`. Nur Read.
Quellen eines anderen Adapters werden nicht ausgewählt — es gibt heute nur einen Adapter, und
eine fremde Quelle mit dem DIP-Parser zu lesen, wäre falsch.

### 4.2 Rohbytes immer aus WORM, immer gegengeprüft

Auch wenn der DIP-Parser heute nur `raw.origin_url` liest: `reparse` lädt die Rohbytes **immer**
aus WORM und rechnet `content_hash` nach, **bevor** geparst wird. Spans sind Beweismaterial;
sie entstehen nur aus einer Quelle, deren Bindung an den Ledger gerade selbst nachgerechnet
wurde (Muster `pipeline/timestamp.py`, AC10 aus #76). Daraus wird ein `RawSource` gebaut:
`origin_url`, `source_type`, `mime_type`, `retrieved_at` aus der `source`-Zeile, `raw_bytes` aus
WORM.

### 4.3 Eine Transaktion pro Quelle, mit Sperre und Nachprüfung

Pro Quelle eine eigene Session. Reihenfolge in `reparse_source`:

1. `normalized_text is None` → `no_text`. Kein WORM-Read.
2. WORM lesen; jeder Fehler → `worm_missing`.
3. Hash nachrechnen; Abweichung → `hash_mismatch` (ERROR-Log). Parser wird **nicht** aufgerufen.
4. `lock_source_if_spanless`: `SELECT source.id … FOR UPDATE`, danach Prüfung „existiert eine
   span-Zeile?". Existiert eine → `rollback`, `skipped_has_spans`.
5. `write_spans(...)`. Die Funktion committet selbst (Logik unverändert aus `ingest`).
6. Rückgabe 0 → `rollback`, `still_empty` (WARNING-Log mit `source_id`). Sonst `reparsed`.
7. Jede andere Exception ab Schritt 4 → `rollback`, `error` (Log mit `source_id`). Die Quelle
   hat danach weiterhin null Spans und wird beim nächsten Lauf wieder ausgewählt.

### 4.4 Netzfreiheit strukturell absichern

Neuer import-linter-Contract: `wortlaut.pipeline.reparse` importiert weder `wortlaut.archive`
noch `wortlaut.timestamp`. Dazu ein Unit-Test, der beweist, dass `fetch` und `discover` des
Adapters nie aufgerufen werden.

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)

- **AC1 — Auswahl.** *Given* drei Quellen: A ohne Spans, B mit ≥ 1 Span, C ohne Spans mit
  anderem `adapter_name`. *When* `list_sources_without_spans(adapter_name="dip-api")`.
  *Then* genau `[A]`.
- **AC2 — Gleichheit mit `ingest`.** *Given* die Protokoll-Fixture aus #41, einmal per `ingest`
  mit funktionierendem Parser in DB 1, einmal per `ingest` mit Parser, der `[]` liefert, und
  anschließendem `reparse_source` in DB 2. *Then* die Mengen
  `(text_start, text_end, span_hash, spoken_at, locator, permalink, speaker.full_name, mandate.party)`
  beider DBs sind **gleich** und nicht leer.
- **AC3 — Hash-Gegenprüfung.** *Given* WORM liefert Bytes, deren Hash nicht zum `content_hash`
  passt. *Then* `hash_mismatch`, der Parser wird **nicht** aufgerufen (Aufrufzähler = 0), es
  entsteht kein Span. *Given* WORM wirft. *Then* `worm_missing`, kein Span.
- **AC4 — Alles oder nichts.** *Given* eine Quelle ohne Spans und `init_span_state` wirft beim
  **zweiten** Aufruf. *When* `reparse_source`. *Then* Status `error`, die Quelle hat **0** Spans
  und erscheint erneut in `list_sources_without_spans`.
- **AC5 — Idempotenz.** *Given* `reparse_source` hat eine Quelle mit n > 0 Spans versorgt.
  *When* ein zweiter Lauf. *Then* die Quelle wird nicht mehr ausgewählt, Span-Zahl bleibt n.
- **AC6 — Nebenläufigkeit.** *Given* eine Quelle ohne Spans. *When* zwei `reparse_source`-Aufrufe
  mit **getrennten Sessions** laufen per `asyncio.gather` gleichzeitig. *Then* die Quelle hat
  genau so viele Spans wie nach einem einzelnen Lauf; ein Ergebnis ist `reparsed`, das andere
  `skipped_has_spans`.
- **AC7 — Leer bleibt sichtbar.** *Given* der Parser liefert `[]`. *Then* `still_empty`, ein
  WARNING-Log mit der `source_id`, kein Span. *Given* `normalized_text is None`. *Then* `no_text`,
  kein WORM-Read.
- **AC8 — CLI.** `reparse --dry-run` gibt `pending=<n> dry_run=True` aus und schreibt nichts.
  Der echte Lauf gibt **genau eine** Ergebniszeile aus, Felder in dieser Reihenfolge:
  `pending= reparsed= spans_total= still_empty= no_text= skipped_has_spans= hash_mismatch= worm_missing= error=`.
  `--limit N` begrenzt die Auswahl auf N Quellen.
- **AC9 — Exit-Codes.** 0 im Normalfall (auch bei `still_empty`, `no_text`, `worm_missing`,
  `skipped_has_spans`) · 2 bei fehlender Konfiguration · **4**, sobald `hash_mismatch > 0`
  (Alarm, Muster `timestamp`) · sonst **1**, sobald `error > 0`.
- **AC10 — Kein Netz.** `fetch` und `discover` des Adapters werden in keinem Pfad von `reparse`
  aufgerufen (Unit-Test mit Adapter, dessen beide Methoden werfen). Der import-linter-Contract
  aus §4.4 ist grün.
- **AC11 — `ingest` unverändert.** Alle bestehenden Tests in `tests/integration/test_span_ingest.py`
  und `tests/integration/test_pipeline_ingest.py` laufen **ohne Änderung** grün.

## 6. Testplan (Test-zu-AC-Mapping)

| AC | Test | Datei | Art |
|---|---|---|---|
| AC1 | `test_list_sources_without_spans_selects_only_spanless_same_adapter` | `tests/integration/test_reparse.py` | Integration |
| AC2 | `test_reparse_yields_same_spans_as_ingest` | `tests/integration/test_reparse.py` | Integration |
| AC3 | `test_hash_mismatch_never_parses` · `test_worm_missing` | `tests/unit/test_reparse_pipeline.py` | Unit |
| AC4 | `test_failure_mid_source_leaves_no_spans` | `tests/integration/test_reparse.py` | Integration |
| AC5 | `test_second_run_is_noop` | `tests/integration/test_reparse.py` | Integration |
| AC6 | `test_concurrent_runs_write_once` | `tests/integration/test_reparse.py` | Integration |
| AC7 | `test_empty_parse_is_still_empty` · `test_no_text_skips_worm` | `tests/unit/test_reparse_pipeline.py` | Unit |
| AC8 | `test_dry_run_line` · `test_summary_line_field_order` · `test_limit_passed_through` | `tests/unit/test_cli_reparse.py` | Unit |
| AC9 | `test_exit_codes` (parametrisiert) | `tests/unit/test_cli_reparse.py` | Unit |
| AC10 | `test_never_fetches_or_discovers` | `tests/unit/test_reparse_pipeline.py` | Unit |
| AC11 | bestehende Tests | — | Integration |

## 7. Recht / Security

- Beweiskette: Spans entstehen nur aus hash-geprüften Rohbytes (§4.2) und zeigen per Offset in
  den eingefrorenen `normalized_text` (R-DATA-06). Keine bestehende Zeile wird verändert (R-DATA-01).
- Keine neuen ENV-Variablen, keine Secrets. Konfigurationsfehler werden wie bei den anderen
  Kommandos über `_config_error` gemeldet — ohne ENV-Werte (R-SEC-01).
- Kein Netzzugriff (AC10). Kein Kontakt zu Fremdarchiven, also keine Last auf fremden Diensten.

## 8. Risiken & offene Fragen

- **Verhalten des heutigen Parsers ist maßgeblich.** `reparse` erzeugt Spans mit dem Parser
  zum Zeitpunkt des Laufs, nicht mit dem zum Zeitpunkt der Erfassung. Für 21/90 ist genau das
  gewollt. Eine Parser-Version am Span gibt es nicht (Nicht-Ziel).
- **`DipSettings` wird benötigt**, obwohl nicht gefetcht wird — der Adapter verlangt die
  Settings im Konstruktor. Auf dem Server sind sie gesetzt; kein neuer Bedarf.
- **Sperre und Langläufer:** Die Zeilensperre hält nur für die Dauer einer Quelle (Sekunden).
  `serve` liest `source` ohne Sperre und wird nicht blockiert.

## 9. Definition of Done (Verweis)

Siehe `docs/engineering.md`. Zusätzlich: Abnahme im Betrieb nach dem Merge —
`reparse --dry-run` zeigt genau 21/90, der echte Lauf liefert dafür Spans > 0, `/verify` ergibt
für eine Stichprobe `status = "ok"`.

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu:**
- `src/wortlaut/pipeline/spans.py`
- `src/wortlaut/pipeline/reparse.py`
- `src/wortlaut/store/reparse.py`
- `tests/unit/test_reparse_pipeline.py`
- `tests/unit/test_cli_reparse.py`
- `tests/integration/test_reparse.py`

**Ändern:**
- `src/wortlaut/pipeline/ingest.py` — `_ingest_spans` entfernen, `write_spans` importieren.
- `src/wortlaut/cli.py` — Subcommand `reparse`, `_run_reparse`, `_ReparseStats`.
- `.importlinter` — ein neuer Contract.
- `docs/deploy.md` — Abschnitt „Erfassungs-Läufe" um `reparse` ergänzen.

## 11. Umsetzungsdetails je Datei

### `src/wortlaut/pipeline/spans.py` (neu)

- Modul-Docstring: Span-Erzeugung aus kanonischem Text, gemeinsam genutzt von `ingest` und
  `reparse` (#118).
- `write_spans` ist **wörtlich** der Körper von `_ingest_spans` aus `pipeline/ingest.py`
  (inklusive `verification`-Ableitung, `spoken_at`-Fail-loud, Sprecher-/Mandat-Resolution,
  `insert_span`, `init_span_state`, `await session.commit()` am Ende, Rückgabe der Anzahl).
- Die Konstante `_PARLIAMENT = "bundestag"` zieht mit um.
- Imports: `date`, `UUID`, `AsyncSession`, `span_hash`, `IngestAdapter`, `RawSource`,
  `NewSpan`, `init_span_state`, `insert_span`, `resolve_or_create_mandate`,
  `resolve_or_create_speaker`, `logging`.

### `src/wortlaut/pipeline/ingest.py` (ändern)

- `_ingest_spans` und `_PARLIAMENT` **löschen**.
- `from wortlaut.pipeline.spans import write_spans` ergänzen.
- Den einen Aufruf `await _ingest_spans(...)` durch `await write_spans(...)` mit **denselben**
  Argumenten ersetzen.
- Nicht mehr benutzte Imports entfernen (`date`, `span_hash`, die `store.spans`-Importe) —
  `content_hash` bleibt.
- **Sonst nichts ändern.**

### `src/wortlaut/store/reparse.py` (neu)

- `SpanlessSource` wie §3.
- `list_sources_without_spans`: `exists().where(Span.source_id == Source.id)` negiert, plus
  `Source.adapter_name == adapter_name`, `order_by(Source.created_at, Source.id)`, optional
  `.limit(limit)`. Muster: `store/timestamps.py::list_sources_without_timestamp`.
- `lock_source_if_spanless(session, source_id) -> bool`:
  1. `await session.execute(select(Source.id).where(Source.id == source_id).with_for_update())`
  2. `has = await session.scalar(select(Span.id).where(Span.source_id == source_id).limit(1))`
  3. `return has is None`
  Kein Commit, kein Rollback in dieser Funktion.

### `src/wortlaut/pipeline/reparse.py` (neu)

- `ReparseOutcome` wie §3.
- `reparse_source` exakt nach §4.3. Hash mit `wortlaut.evidence.hashing.content_hash`.
- `RawSource(origin_url=…, source_type=…, raw_bytes=<WORM>, mime_type=…, retrieved_at=…)`.
- Bei `skipped_has_spans`, `still_empty` und `error`: `await session.rollback()`.
- Der `except` für `error` fängt `Exception` und loggt mit `logger.exception`.
- Imports nur aus `wortlaut.evidence`, `wortlaut.ingest.adapter`, `wortlaut.pipeline.spans`,
  `wortlaut.store`. **Nicht** aus `wortlaut.archive`, `wortlaut.timestamp`,
  `wortlaut.pipeline.ingest`.

### `src/wortlaut/cli.py` (ändern)

- Subparser `reparse` mit `--limit` (int, default None), `--dry-run`, `--no-migrate` —
  Muster `timestamp`.
- In `main` die Liste der gültigen Subcommands und die Fehlermeldung um `reparse` ergänzen und
  `asyncio.run(_run_reparse(args))` verdrahten.
- `_run_reparse`: Settings `DbSettings`, `WormSettings`, `DipSettings` in **einem**
  `try`-Block, Fehler → `_config_error`, Exit 2. Danach Engine, Sessionmaker,
  `MinioWormStore`, `DipPlenarprotokollAdapter(dip_settings)`. Bootstrap wie `timestamp`
  (`upgrade_head` außer bei `--no-migrate`, `worm.ensure_bucket()`).
  Auswahl mit `adapter_name=adapter.name`. `--dry-run` → `pending=<n> dry_run=True`, Exit 0.
  Sonst je Quelle eigene Session, `reparse_source`, Stats buchen. `hash_mismatch` zusätzlich
  auf stderr melden (Muster `timestamp`). Am Ende genau eine Summary-Zeile, Exit nach AC9.
  Im `finally`: `adapter.aclose` und `engine.dispose` über `_aclose_all`.
- `_ReparseStats` als `@dataclass` mit einem Zähler je Status, `spans_total` und
  `record(outcome)`, `summary_line(pending)` nach AC8.

### `.importlinter` (ändern)

Am Ende anfügen:

```
# Spec 0118 §4.4: reparse arbeitet ausschliesslich auf vorhandenen Ledger-Daten. Kein
# Fremdarchiv, kein Zeitstempeldienst — auch nicht indirekt.
[importlinter:contract:reparse-ohne-netzdienste]
name = Reparse importiert weder Archiv- noch Zeitstempel-Layer
type = forbidden
source_modules =
    wortlaut.pipeline.reparse
forbidden_modules =
    wortlaut.archive
    wortlaut.timestamp
```

### `docs/deploy.md` (ändern)

Im Abschnitt „Erfassungs-Läufe" nach dem Codeblock mit `ingest`/`timestamp` einen kurzen
Absatz ergänzen: `reparse` erzeugt Spans für Quellen, die noch keine haben (etwa nach einer
Parser-Korrektur), arbeitet ohne Netz, fasst Quellen mit Spans nie an; erst `--dry-run`.
Dazu ein Codeblock im selben Stil:

```
docker compose --env-file /srv/wortlaut/.env -f compose.yml \
  run --rm api python -m wortlaut reparse --dry-run
```

### Tests

- **Integration** (`pytestmark = pytest.mark.integration`): Fixtures `fresh_pg_dsn` und
  `worm_store` aus `tests/integration/conftest.py`; Adapter, Archiver-Fake, Settings und
  Seeding nach dem Vorbild von `tests/integration/test_span_ingest.py`
  (`_FixtureDipAdapter`, `_OkArchiver`, `_seed_adapter`).
  „Quelle ohne Spans" herstellen: `ingest_source` mit einer Adapter-Unterklasse, deren
  `parse` `[]` liefert. Danach `reparse_source` mit dem **echten** `_FixtureDipAdapter`.
- AC4: `unittest.mock.patch("wortlaut.pipeline.spans.init_span_state", side_effect=…)` — erster
  Aufruf echt, zweiter wirft `RuntimeError`.
- AC6: zwei Sessions aus demselben Sessionmaker, `asyncio.gather` beider `reparse_source`.
- **Unit**: Fakes für `WormStore`, Session und Adapter nach dem Vorbild von
  `tests/unit/test_timestamp_pipeline.py` und `tests/unit/test_cli_timestamp.py`.
- **Sonar-Muster beachten:** In jedem `pytest.raises`-Block steht genau **ein** Aufruf;
  Hilfsaufrufe vorher in Variablen ziehen. Kein Lambda in `patch()` — `side_effect=`/
  `return_value=` verwenden. Kein Modul- oder Klassen-Zustand für Testdaten. Keine
  ausgeschriebenen DSNs mit Zugangsdaten in Tests.

## 12. Do-NOT (hart)

- **Kein** UPDATE oder DELETE auf `source`, `span`, `span_state`, `speaker`, `mandate`.
- **Keine** Migration, keine Änderung an `migrations/`.
- **Kein** erneutes `adapter.normalize(...)` in `reparse`.
- **Keine** Logikänderung in `write_spans` gegenüber `_ingest_spans` — nur der Umzug.
- **Keine** Änderung an bestehenden Testdateien.
- **Kein** Import von `wortlaut.archive`, `wortlaut.timestamp` oder `wortlaut.pipeline.ingest`
  in `pipeline/reparse.py`.
- **Keine** neuen Abhängigkeiten in `pyproject.toml`.
- **Keine** Dateien außerhalb von §10.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate (ruff · mypy · import-linter · pytest) fährt der Reviewer selbst — ein Selbstbericht
des Coders ersetzt es nicht. Falls du doch lokal testen willst, ist der Marker-Ausdruck
`-m "not integration and not live"` zu benutzen: Ein bloßes `-m "not integration"` **ersetzt**
den `-m "not live"`-Ausdruck aus `addopts` in `pyproject.toml`, statt ihn zu ergänzen.
