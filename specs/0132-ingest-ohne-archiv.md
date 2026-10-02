# Increment-Spec: `ingest` ohne Archiv, `chk_archive` weg, Rückstand sichtbar (#132)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #132 · **Epic:** #123 (Increment 3b) · **Status:** Reviewed (autonom; Durchsicht der mit „zur Durchsicht“ markierten Punkte durch den Stakeholder steht aus)
- **Phase/Layer:** phase/1-mvp · Migration, `pipeline`, `store`, `cli`, Tests, Doku
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Entscheidung: [ADR-0009](../docs/adr/0009-pflicht-anker-und-zitierfaehigkeit.md) §1, Konsequenzen
- Baut auf **#126** (Span-Trigger), **#130** (`capture`, `capture_request`). Branch setzt auf
  `feature/0130-capture-schritt` auf.

## 0. Ausgangslage

Seit #126 erzwingt ein Trigger, dass Spans nur für attestierte Quellen entstehen; seit #130 gibt es
`capture` als eigenen Schritt. `ingest` fordert aber weiterhin **synchron** einen Wayback-Capture an
und **verwirft** die Quelle, wenn er scheitert (`archive_failed`). Damit hängt das Erfassen noch an
der Erreichbarkeit eines fremden Dienstes — genau das, was ADR-0009 beenden will: Ein Ausfall soll
einen Wiederholungslauf kosten, kein Dokument.

**Entscheidung (Stakeholder, 2026-10-02):** `ingest` spricht nach diesem Increment **gar nicht
mehr** mit dem Internet Archive. Ablauf im Betrieb:

```
ingest → timestamp → capture → attest → reparse
```

### 0a. Was `chk_archive` heute schützt — und warum es wegkann

`chk_archive` (Migration `0002`) verlangt `archive_wayback IS NOT NULL OR archive_today IS NOT
NULL` auf jeder `source`-Zeile. Ohne Capture im Ingest hat eine neue Quelle keinen von beiden.
ADR-0009 verlangt, dass die Garantie „nichts wird zitierbar ohne Fremdbezeugung" an der
Zitierfähigkeits-Grenze **gleichwertig** weiterbesteht. Das tut sie seit #126: Der Trigger
`trg_span_requires_attestation` lässt keinen Span ohne `source_archive`-Zeile zu, und der Read-Pfad
filtert zusätzlich. `chk_archive` ist damit überflüssig — und würde den neuen Ingest verhindern.

### 0b. Bestandsdaten bleiben unverändert

Die 9 Bestandsquellen behalten ihre `archive_wayback`-Werte (append-only). Neue Quellen tragen dort
`NULL`. `/verify` und der Quellen-Beleg zeigen seit #128 ohnehin die Attestierung.

## 1. Ziel

1. `ingest` holt, hasht, legt in WORM ab, friert den Text ein und trägt ein — **ohne** jeden Kontakt
   zum Internet Archive, ohne IA-Zugangsdaten, ohne Pre-Flight, ohne Circuit-Breaker.
2. Migration `0008` entfernt `chk_archive`.
3. Ein neues Kommando `status` macht den Rückstand sichtbar.
4. Die Pre-Flight-Absicherung bleibt erhalten — getestet über `capture` statt über `ingest`.

## 2. Nicht-Ziele

- **Kein** Entfernen der Spalten `archive_wayback`/`archive_today` (Bestandsdaten, append-only).
- **Keine** Änderung an `capture`, `attest`, `reparse`, `timestamp`, Read-Pfad, `/verify`.
- **Kein** Löschen von Archiv-Code (`archive/archiver.py`, `spn2.py`, `preflight.py`) — `capture`
  nutzt ihn weiter.

## 3. Betroffene Interfaces / Öffentliche Signaturen

```python
# src/wortlaut/pipeline/ingest.py
@dataclass(frozen=True)
class PipelineDeps:
    adapter: IngestAdapter
    worm: WormStore                     # wayback/archive_today entfallen

@dataclass(frozen=True)
class IngestOutcome:
    status: Literal["inserted", "skipped_duplicate"]   # "archive_failed" entfällt
    source_id: UUID | None
    content_hash: str
    # span_count und archive_failures entfallen

# src/wortlaut/store/status.py — NEU
@dataclass(frozen=True)
class BacklogCounts:
    sources: int
    unstamped: int
    unattested: int
    unattested_capture_failed: int
    attested_without_spans: int

async def backlog_counts(session: AsyncSession) -> BacklogCounts: ...

# src/wortlaut/cli.py
#   python -m wortlaut ingest --since … [--limit N] [--dry-run] [--no-migrate] [--rights-basis …]
#       (--no-preflight entfällt)
#   python -m wortlaut status            # NEU, nur lesend, migriert nicht
```

## 4. Design — die Entscheidungen (zur Durchsicht markiert)

### 4.1 `ingest` ohne Archiv

In `ingest_source` entfallen die Schritte 4/5 (`archive_all`) vollständig. `NewSource` bekommt
`archive_wayback=None`, `archive_today=None`. Reihenfolge danach: fetch → hash → dedup → WORM-put →
normalize → insert. Die Hash-über-Rohbytes-Regel (R-DATA-02) und die Dedup-Logik bleiben exakt.

Neuer import-linter-Contract: `wortlaut.pipeline.ingest` importiert **nichts** aus
`wortlaut.archive`.

### 4.2 Ingest-CLI schlanker

`_run` (ingest) verliert: Zugangsdaten-Pflicht, `_build_archivers`, Pre-Flight, Circuit-Breaker,
`archive_failed`-Meldungen. `_load_settings` liefert nur noch `DbSettings`, `WormSettings`,
`DipSettings`. `--no-preflight` entfällt am `ingest`-Subparser (bleibt am `capture`-Subparser).

**Summary-Zeile** (zur Durchsicht): `discovered= inserted= skipped_duplicate= fetch_error=`.
`archive_failed`, `spans_total` und `reasons` entfallen — sie haben keine Bedeutung mehr.
Dry-Run: `discovered=<n> dry_run=True`.

### 4.3 Migration `0008`

`ALTER TABLE source DROP CONSTRAINT chk_archive`. `downgrade()` legt sie mit `NOT VALID` wieder an,
damit ein Rückweg nicht an Quellen ohne Archiv-URL scheitert, die nach dem Upgrade entstanden sind.

### 4.4 Rückstand: Kommando `status` (zur Durchsicht)

Nur lesend, **ohne** Migration und ohne Netz. Gibt genau eine Zeile aus:

```
sources=<n> unstamped=<n> unattested=<n> unattested_capture_failed=<n> attested_without_spans=<n>
```

- `unstamped`: Quellen ohne `source_timestamp`-Zeile.
- `unattested`: Quellen ohne `source_archive`-Zeile.
- `unattested_capture_failed`: davon die, deren **letzte** `capture_request` `failed` ist.
- `attested_without_spans`: attestierte Quellen ohne Span (Arbeit für `reparse`).

Exit 0; Konfigurationsfehler → Exit 2.

### 4.5 Pre-Flight-Tests ziehen um

Die vier Pre-Flight-Tests in `tests/unit/test_cli.py` prüfen heute `_run` (ingest). Sie werden
nach `tests/unit/test_cli_capture.py` übertragen und prüfen dort `_run_capture` mit **denselben**
Aussagen (Ausfall → Exit 3 vor der ersten Quelle; gesund → normaler Lauf; `--no-preflight` und
`preflight_enabled=False` überspringen den Probe-Call). Die Absicherung geht nicht verloren, sie
wandert dorthin, wo der Pre-Flight jetzt läuft.

## 5. Testbare Akzeptanzkriterien

- **AC1 — Kein Archiv im Ingest.** `ingest_source` legt eine neue Quelle an, ohne dass ein Archivar
  existiert (`PipelineDeps(adapter, worm)`); die Zeile hat `archive_wayback IS NULL` und
  `archive_today IS NULL`. Der import-linter-Contract aus §4.1 ist grün.
- **AC2 — Migration.** Nach `0008` gelingt ein `source`-Insert ohne beide Archiv-URLs; vor `0008`
  (auf `0007` zurückgefahren) scheitert er an `chk_archive`. `downgrade` → `upgrade` ist wiederholbar,
  auch wenn zwischendurch Quellen ohne Archiv-URL angelegt wurden (`NOT VALID`).
- **AC3 — Ende-zu-Ende ohne Archiv.** *Given* echte Postgres/MinIO, Fixture-Adapter. *When* `ingest`
  → Attestierung (Fixture `seed_attestation`) → `reparse`. *Then* die Quelle hat Spans, ohne dass im
  ganzen Ablauf ein Archivar gebaut wurde.
- **AC4 — Ingest-CLI.** Läuft **ohne** `WORTLAUT_ARCHIVE_IA_*` (kein Exit 2 mehr), ruft keinen
  Pre-Flight, baut keine Archivare (Unit-Test: Patch auf `_build_archivers` und `_preflight_ok`
  mit Zählern = 0). Summary in der Reihenfolge aus §4.2.
- **AC5 — `status`.** *Given* eine Quelle je Zustand (ungestempelt; unattestiert ohne Anfrage;
  unattestiert mit letzter Anfrage `failed`; attestiert ohne Span; attestiert mit Span). *Then* die
  Zeile aus §4.4 nennt genau die erwarteten Zahlen. `status` legt keine Migration an (Unit-Test:
  `upgrade_head` nicht aufgerufen).
- **AC6 — Pre-Flight bleibt abgesichert.** Die vier übertragenen Tests laufen gegen `_run_capture`
  grün.
- **AC7 — Bestand.** Alle übrigen Tests grün. Geändert wird nur, was §11 nennt.

## 6. Testplan

| AC | Test | Datei | Art |
|---|---|---|---|
| AC1 | `test_ingest_without_archive` | `tests/integration/test_pipeline_ingest.py` | Integration |
| AC2 | `test_chk_archive_dropped` · `test_downgrade_not_valid_roundtrip` | `tests/integration/test_ingest_without_archive.py` (neu) | Integration |
| AC3 | `test_end_to_end_without_archive` | `tests/integration/test_ingest_without_archive.py` | Integration |
| AC4 | `test_ingest_needs_no_ia_credentials` · `test_ingest_never_builds_archivers` · `test_summary_line_field_order` (angepasst) | `tests/unit/test_cli.py` | Unit |
| AC5 | `test_backlog_counts` | `tests/integration/test_status.py` (neu) · `test_status_does_not_migrate` in `tests/unit/test_cli_status.py` (neu) | Integration/Unit |
| AC6 | vier Pre-Flight-Tests | `tests/unit/test_cli_capture.py` | Unit |

## 7. Recht / Security

- **Beweiskette unverändert stark:** Zitierfähig wird weiterhin nur, was attestiert ist (Trigger aus
  #126, Read-Filter). `chk_archive` war eine schwächere, ältere Form derselben Garantie (es genügte
  *irgendeine* gemeldete Archiv-URL, ohne Byte-Prüfung).
- **Weniger Angriffsfläche im Ingest:** keine IA-Zugangsdaten mehr in diesem Pfad.

## 8. Risiken

- **Großer Test-Umbau.** Viele Tests bauen `PipelineDeps` mit Archivaren oder prüfen das alte
  Gate. §11 listet jede betroffene Stelle; der Review prüft, dass nur diese Tests entfallen.
- **Betrieb:** Nach dem Ausrollen captured `ingest` nicht mehr; `capture` muss im Ablauf stehen,
  sonst bleiben neue Quellen unattestiert. `status` macht das sichtbar.

## 9. Definition of Done

Siehe `docs/engineering.md`. Abnahme im Betrieb: Migration `0008`; `status` zeigt
`sources=9 unstamped=0 unattested=0 unattested_capture_failed=0 attested_without_spans=0`.

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu:**
- `migrations/versions/0008_drop_chk_archive.py`
- `src/wortlaut/store/status.py`
- `tests/integration/test_ingest_without_archive.py`
- `tests/integration/test_status.py`
- `tests/unit/test_cli_status.py`

**Ändern (Produktivcode, Doku):**
- `src/wortlaut/pipeline/ingest.py`
- `src/wortlaut/cli.py`
- `.importlinter`
- `docs/deploy.md`

**Ändern (Tests):**
- `tests/unit/test_cli.py`
- `tests/unit/test_cli_capture.py`
- `tests/unit/test_pipeline_order.py`
- `tests/integration/test_pipeline_ingest.py`
- `tests/integration/test_cli_ingest.py`
- `tests/integration/test_span_ingest.py`
- `tests/integration/test_reparse.py`
- `tests/integration/test_attest.py`
- `tests/integration/test_capture.py`
- `tests/integration/test_db_schema.py` — nur der in §11 genannte Test entfällt

## 11. Umsetzungsdetails je Datei

### `migrations/versions/0008_drop_chk_archive.py`

`revision = "0008"`, `down_revision = "0007"`. Docstring mit Verweis auf ADR-0009 und §0a dieser
Spec. `upgrade`: `ALTER TABLE source DROP CONSTRAINT chk_archive`. `downgrade`:
`ALTER TABLE source ADD CONSTRAINT chk_archive CHECK (archive_wayback IS NOT NULL OR archive_today
IS NOT NULL) NOT VALID`.

### `src/wortlaut/pipeline/ingest.py`

- Imports aus `wortlaut.archive` entfernen. `PipelineDeps` und `IngestOutcome` nach §3.
- Schritte 4/5 streichen; `NewSource(..., archive_wayback=None, archive_today=None, ...)`.
- Rückgaben: `IngestOutcome("skipped_duplicate", None, h)` und `IngestOutcome("inserted",
  source_id, h)`.
- Modul-Docstring: Pipeline `fetch → hash → dedup → WORM → normalize → insert source`; ein Satz,
  dass Fremdbezeugung seit #132 in `capture`/`attest` liegt (ADR-0009).

### `src/wortlaut/store/status.py`

Vier Zählungen per SQLAlchemy-Core oder `text()`; „letzte Anfrage" wie in `store/captures.py`.
Nur Read.

### `src/wortlaut/cli.py`

- `_run` nach §4.2 umbauen. Der Dry-Run gibt `discovered=<n> dry_run=True` aus.
- `_RunStats`: Felder `inserted`, `skipped`, `fetch_error`; `record` und `summary_line` nach §4.2;
  `consecutive_archive_failed`, `reasons`, `top_reason`, `spans_total` entfallen.
- `_load_settings` nach §4.2 (drei Settings).
- `ingest`-Subparser: `--no-preflight` entfernen.
- Neues Subcommand `status` mit `_run_status`: `DbSettings` laden (Fehler → Exit 2 über
  `_config_error`), Engine, eine Session, `backlog_counts`, eine Zeile nach §4.4, `engine.dispose`
  im `finally`. **Kein** `upgrade_head`.
- `main`: Liste gültiger Subcommands und Fehlermeldung um `status` ergänzen.
- `_build_archivers`, `_preflight_ok`, `_ia_credentials`, `_credentials_missing` **bleiben** (für
  `capture`).

### `.importlinter`

Am Ende:

```
# Spec 0132 §4.1: Erfassen braucht keine Fremdbezeugung (ADR-0009) — ingest spricht nicht mit Archiven.
[importlinter:contract:ingest-ohne-archiv]
name = Ingest importiert keinen Archiv-Code
type = forbidden
source_modules =
    wortlaut.pipeline.ingest
forbidden_modules =
    wortlaut.archive
```

### `docs/deploy.md`

Abschnitt „Erfassungs-Läufe" überarbeiten: Ablauf `ingest` → `timestamp` → `capture` → `attest` →
`reparse`; `ingest` braucht **keine** IA-Zugangsdaten mehr (nur `capture`); der Absatz über das
Insert-Gate und den Pre-Flight im Ingest wird auf `capture` umgeschrieben; `status` mit Beispielzeile.
Die neue Summary-Zeile von `ingest` nennen.

### Tests — was entfällt, was sich ändert

**Entfallen** (das Verhalten gibt es nicht mehr; nichts anderes löschen):
- `tests/integration/test_pipeline_ingest.py`: `test_partial_archive_inserts`,
  `test_wayback_hard_fail_blocks_insert`.
- `tests/unit/test_pipeline_order.py`: `test_archive_total_failure_no_insert`,
  `test_archive_today_soft_fail_inserts`, `test_wayback_hard_fail_blocks_insert`.
- `tests/integration/test_cli_ingest.py`: `test_archive_failed_retried_on_rerun`.
- `tests/integration/test_db_schema.py`: `test_source_requires_archive` (prüft genau `chk_archive`;
  AC2 prüft künftig das Gegenteil). *Nachtrag im Review: fehlte in der ersten Fassung.*
- `tests/unit/test_cli.py`: `test_circuit_breaker_aborts_run`,
  `test_circuit_breaker_resets_on_success`, `test_circuit_breaker_without_reset_would_abort`
  (der Breaker ist bei `capture` abgesichert) und `test_ohne_zugangsdaten_exit_2` (das Verhalten
  kehrt sich um; AC4 prüft das Gegenteil). `test_dry_run_ohne_zugangsdaten_ok` **bleibt** —
  seine Aussage gilt weiterhin.

**Ziehen um** (§4.5): die vier Pre-Flight-Tests aus `tests/unit/test_cli.py`
(`test_preflight_failure_aborts_before_discover`, `test_preflight_healthy_runs_normally`,
`test_no_preflight_flag_skips_probe`, `test_preflight_disabled_via_settings_skips_probe`) nach
`tests/unit/test_cli_capture.py`, gegen `_run_capture`, mit denselben Aussagen (statt „discover 0×"
dort „keine Quelle verarbeitet").

**Mechanisch anpassen**, ohne Aussage zu schwächen:
- Jede Konstruktion `PipelineDeps(adapter=…, wayback=…, archive_today=…, worm=…)` →
  `PipelineDeps(adapter=…, worm=…)`; nicht mehr benutzte Archivar-Fakes und -Imports entfernen.
- `IngestOutcome(..., span_count=…, archive_failures=…)` → ohne diese Argumente; Assertions auf
  `span_count == 0` nach `ingest` entfallen ersatzlos (es gibt das Feld nicht mehr; dass `ingest`
  keine Spans erzeugt, prüfen die Span-Zählungen in der DB weiterhin).
- `tests/unit/test_cli.py`: Erwartungen an die Summary- und Dry-Run-Zeile auf §4.2 umstellen.

**Neu:** die Tests aus §6.

- **Sonar-Muster:** ein Aufruf je `pytest.raises`-Block (Hilfsaufrufe vorher in Variablen); keine
  zusammengesetzten Asserts; kein Lambda in `patch()`; Literale, die dreimal vorkommen, als
  Konstante; `async def` nur, wo awaited wird; SQL-Strings immer über `text(...)`.

## 12. Do-NOT (hart)

- **Keine** Änderung an `capture`, `attest`, `reparse`, `timestamp`, `serving/`, `pipeline/verify.py`,
  Archiv-Modulen oder Migrationen `0001`–`0007`.
- **Kein** Löschen von Tests außer den in §11 namentlich genannten.
- **Keine** Assertion schwächen; entfallene `span_count`-Assertions nur dort, wo das Feld wegfällt.
- **Kein** UPDATE/DELETE auf `source`, `span`, `source_archive`, `capture_request`.
- **Keine** neuen Abhängigkeiten, **keine** Dateien außerhalb von §10.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate fährt der Reviewer selbst.
