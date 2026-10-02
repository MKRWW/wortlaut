# Increment-Spec: Capture als eigener Schritt (#130)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #130 · **Epic:** #123 (Increment 3a) · **Status:** Draft
- **Phase/Layer:** phase/1-mvp · Migration, `store`, `pipeline`, `cli`
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Entscheidung: [ADR-0009](../docs/adr/0009-pflicht-anker-und-zitierfaehigkeit.md) §2, §3
- Baut auf **#108** (SPN2-Archiver), **#124** (`attest`, `wayback_lookup`), **#126** (Span-Grenze).

## 0. Ausgangslage

Heute fordert ausschließlich `ingest` einen Capture an, und zwar synchron vor dem Insert. Nach
der Stakeholder-Entscheidung aus #130 spricht `ingest` nach Increment 3 **gar nicht mehr** mit dem
Internet Archive. Dann braucht es einen eigenen Schritt, der Bezeugungen anfordert. Der Ablauf im
Betrieb wird:

```
ingest → timestamp → capture → attest → reparse
```

Dieses Increment baut **nur** diesen Schritt. `ingest` bleibt unverändert (Increment 3b).

### 0a. Vorklärung: erst nachsehen, dann anfordern

Seit #124 zählt **jeder** byte-gleiche Wayback-Snapshot derselben URL. Bei der Abnahme waren 6 von
9 Quellen über Snapshots attestierbar, die die Crawler des Internet Archive schon **vor** unserem
Abruf gemacht hatten. Ein Capture ist dann überflüssig — er kostet Kontingent und belastet einen
fremden Dienst. `capture` fragt deshalb **zuerst** den CDX-Index (wie `attest`, nur lesend) und
löst einen Save-Page-Now-Auftrag nur aus, wenn dort kein Snapshot mit unserem Digest steht.

### 0b. Vorklärung: Gedächtnis gegen Doppel-Captures

Ein Capture ist erst Stunden bis Tage später abrufbar (#124 §0d). In dieser Zeit ist die Quelle
weiter unattestiert. Ohne Gedächtnis würde jeder Lauf erneut einen Capture auslösen. `source` ist
append-only und kann den Versuch nicht festhalten. Deshalb eine eigene append-only Tabelle
`capture_request`, und eine **Abkühlzeit** je Quelle: nach einem erfolgreichen Capture lange (bis
der Index nachgezogen hat), nach einem Fehlschlag kürzer.

### 0c. Vorklärung: kein archive.today

`archive.today` ist nach ADR-0009 §3 kein Anker. `capture` fordert nur Wayback an.

## 1. Ziel

`python -m wortlaut capture` fordert für jede unattestierte Quelle, für die es noch keinen
byte-gleichen Snapshot gibt und deren letzte Anfrage abgekühlt ist, genau einen Wayback-Capture an
und protokolliert das Ergebnis in `capture_request`.

## 2. Nicht-Ziele (Scope-Grenze)

- **Keine** Änderung an `ingest`, `chk_archive`, `source.archive_wayback` (Increment 3b).
- **Keine** Attestierung — die bleibt Sache von `attest`. `capture` schreibt nie in `source_archive`.
- **Kein** archive.today.
- **Kein** neuer SPN2-Code: verwendet wird der bestehende `WaybackArchiver` aus #108.

## 3. Betroffene Interfaces / Öffentliche Signaturen

```python
# src/wortlaut/store/captures.py — NEU
@dataclass(frozen=True)
class CaptureCandidate:
    source_id: UUID
    content_hash: str
    raw_bytes_ref: str
    origin_url: str

@dataclass(frozen=True)
class NewCaptureRequest:
    source_id: UUID
    archiver: str
    outcome: Literal["captured", "failed"]
    snapshot_url: str | None
    reason: str | None

async def list_sources_needing_capture(
    session: AsyncSession, *, now: datetime,
    captured_cooldown: timedelta, failed_cooldown: timedelta, limit: int | None = None,
) -> list[CaptureCandidate]: ...
async def insert_capture_request(session: AsyncSession, row: NewCaptureRequest) -> UUID: ...

# src/wortlaut/pipeline/capture.py — NEU
@dataclass(frozen=True)
class CaptureOutcome:
    status: Literal["captured", "already_archived", "failed",
                    "hash_mismatch", "worm_missing", "error"]
    source_id: UUID
    snapshot_url: str | None = None
    reason: str | None = None

async def capture_source(
    candidate: CaptureCandidate, *, session: AsyncSession, worm: WormStore,
    lookup: WaybackLookup, wayback: Archiver,
) -> CaptureOutcome: ...

# src/wortlaut/cli.py — neues Subcommand
#   python -m wortlaut capture [--limit N] [--dry-run] [--no-migrate] [--no-preflight]
```

## 4. Design

### 4.1 Tabelle `capture_request` (Migration `0007`)

```sql
CREATE TABLE capture_request (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id     uuid NOT NULL REFERENCES source(id),
  archiver      text NOT NULL,
  outcome       text NOT NULL CHECK (outcome IN ('captured', 'failed')),
  snapshot_url  text,
  reason        text,
  requested_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_capture_outcome CHECK (
    (outcome = 'captured' AND snapshot_url IS NOT NULL AND reason IS NULL) OR
    (outcome = 'failed'   AND snapshot_url IS NULL     AND reason IS NOT NULL))
);
CREATE INDEX ix_capture_request_source ON capture_request(source_id, requested_at);
```

Dazu der Append-only-Trigger (`forbid_mutation()`). **Kein** UNIQUE: Eine Quelle kann über die
Zeit mehrere Anfragen haben; das Protokoll ist gerade die Historie.

### 4.2 Auswahl

`list_sources_needing_capture` liefert Quellen, für die **alle** gelten:

1. keine `source_archive`-Zeile (unattestiert),
2. die **letzte** `capture_request`-Zeile (nach `requested_at`) fehlt, **oder** sie ist
   `captured` und älter als `captured_cooldown`, **oder** sie ist `failed` und älter als
   `failed_cooldown`.

Sortiert nach `source.created_at, source.id`; optionales `limit`. Nur Read. `now` wird übergeben
(testbar, keine versteckte Uhr).

### 4.3 Ablauf je Quelle (`capture_source`)

1. WORM lesen; Fehler → `worm_missing`. Kein Netz.
2. `content_hash(raw) != candidate.content_hash` → `hash_mismatch` (ERROR-Log). Kein Netz.
3. `lookup.candidates(origin_url, sha1_b32=sha1_base32(raw))`. **Mindestens ein Kandidat** →
   `already_archived`: kein Capture, **keine** Zeile in `capture_request` (`attest` übernimmt).
   `ArchiveError` → `error`, keine Zeile.
4. Sonst `wayback.archive(origin_url)`:
   - Erfolg → Zeile `outcome='captured'`, `snapshot_url` = Rückgabe → `captured`.
   - `ArchiveError` → Zeile `outcome='failed'`, `reason = exc.label()` → `failed`.
5. Andere Ausnahmen werden nicht abgefangen.

Ein `failed` ist ein normales Ergebnis (wird protokolliert und abgekühlt), kein Programmfehler.

### 4.4 CLI und Gast-Verhalten

Muster `_run` (ingest) für Zugangsdaten, Pre-Flight und Breaker; Muster `_run_attest` für den
Lookup:

- Settings `DbSettings`, `WormSettings`, `ArchiveSettings`; IA-Zugangsdaten **Pflicht** (Exit 2,
  `_credentials_missing`), außer bei `--dry-run`.
- `--dry-run` → `pending=<n> dry_run=True`, Exit 0, **ohne** Netz und ohne Pre-Flight.
- Pre-Flight (`_preflight_ok`) vor der ersten Anfrage; `--no-preflight` überspringt ihn; Ausfall → Exit 3.
- `WaybackArchiver` über `_build_archivers` (archive.today wird gebaut, aber **nicht** verwendet —
  nur geschlossen), `HttpWaybackLookup` wie in `_run_attest`.
- Circuit-Breaker: `consecutive_failure_limit` aufeinanderfolgende `failed` oder `error` →
  Summary, Exit 3.
- Summary, Felder in dieser Reihenfolge:
  `pending= captured= already_archived= failed= hash_mismatch= worm_missing= error=`.
- Exit: 4 bei `hash_mismatch > 0` · 3 bei Breaker · 1 bei `error > 0` · sonst 0. `failed` allein
  ist **kein** Fehler-Exit (wird protokolliert und später erneut versucht).
- Zwei neue Settings in `ArchiveSettings`:
  `capture_cooldown_captured_hours: float = 72.0`, `capture_cooldown_failed_hours: float = 6.0`.

## 5. Testbare Akzeptanzkriterien

- **AC1 — Tabelle.** `capture_request` ist append-only (UPDATE/DELETE scheitern); ein `captured`
  ohne `snapshot_url` und ein `failed` ohne `reason` scheitern am Check.
- **AC2 — Auswahl: Attestierte nie.** Eine attestierte Quelle wird nie gewählt, gleich welche
  Anfragen es gibt.
- **AC3 — Auswahl: Abkühlzeit.** Mit `now` fest gewählt: keine Anfrage → gewählt; letzte Anfrage
  `captured` vor 1 h (Abkühlung 72 h) → nicht gewählt; vor 73 h → gewählt; letzte `failed` vor 1 h
  (Abkühlung 6 h) → nicht gewählt; vor 7 h → gewählt. Maßgeblich ist die **letzte** Anfrage
  (Test: alte `failed` + neue `captured` vor 1 h → nicht gewählt).
- **AC4 — Erst nachsehen.** Lookup liefert einen Kandidaten → `already_archived`, `archive` wird
  **nicht** aufgerufen (Zähler = 0), keine Zeile geschrieben.
- **AC5 — Capture protokolliert.** Kein Kandidat, `archive` liefert eine URL → `captured`, genau eine
  Zeile `outcome='captured'` mit dieser URL.
- **AC6 — Fehlschlag protokolliert.** `archive` wirft `ArchiveError` → `failed`, genau eine Zeile
  `outcome='failed'` mit `reason == exc.label()`.
- **AC7 — Hash vor Netz.** WORM-Bytes passen nicht → `hash_mismatch`; weder Lookup noch `archive`
  aufgerufen. WORM wirft → `worm_missing`, ebenso.
- **AC8 — Lookup-Fehler.** `candidates` wirft `ArchiveError` → `error`, `archive` nicht aufgerufen,
  keine Zeile.
- **AC9 — CLI.** `--dry-run` ohne Zugangsdaten → Exit 0 mit `pending=<n> dry_run=True`; echter Lauf
  ohne Zugangsdaten → Exit 2; Summary-Zeile in der Reihenfolge aus §4.4; Exit-Codes nach §4.4
  (parametrisiert); Breaker nach `consecutive_failure_limit` → Exit 3.
- **AC10 — Kein archive.today.** Der `archive_today`-Archiver wird im Capture-Pfad nie aufgerufen
  (Unit-Test mit einem Fake, der beim Aufruf wirft).
- **AC11 — `capture` attestiert nie.** Der Capture-Pfad schreibt nie in `source_archive`
  (Integrationstest: nach einem `captured` ist die Quelle weiter unattestiert).
- **AC12 — Bestand.** Alle bestehenden Tests bleiben ohne Änderung grün.

## 6. Testplan

| AC | Test | Datei | Art |
|---|---|---|---|
| AC1 | `test_capture_request_append_only` · `test_capture_request_outcome_check` | `tests/integration/test_capture.py` | Integration |
| AC2, AC3 | `test_selection_skips_attested` · `test_selection_cooldowns` (parametrisiert) · `test_selection_uses_latest_request` | `tests/integration/test_capture.py` | Integration |
| AC4–AC8, AC10 | `test_already_archived_skips_capture` · `test_captured_is_logged` · `test_failed_is_logged` · `test_hash_before_network` · `test_worm_missing` · `test_lookup_error` · `test_never_calls_archive_today` | `tests/unit/test_capture_pipeline.py` | Unit |
| AC9 | `test_dry_run_without_credentials` · `test_missing_credentials_exit_2` · `test_summary_line_field_order` · `test_exit_codes` · `test_circuit_breaker` | `tests/unit/test_cli_capture.py` | Unit |
| AC11 | `test_capture_never_attests` | `tests/integration/test_capture.py` | Integration |
| AC12 | bestehende Tests | — | — |

## 7. Recht / Security

- **Gast-Verhalten:** CDX zuerst (AC4), Abkühlzeiten (AC3), `RateLimiter` aus den Settings,
  Pre-Flight und Breaker wie beim Ingest. Kein Capture für schon archivierte Bytes.
- **Zugangsdaten** nur im `Authorization`-Header des bestehenden `WaybackArchiver` (R-SEC-01).
- **Keine Beweiswirkung:** `capture` schreibt nie in `source_archive` (AC11). Ein `captured` ist
  nur eine Anfrage, die Bezeugung prüft erst `attest`.

## 8. Risiken

- **`capture` in 3a doppelt zu `ingest`:** Solange `ingest` noch selbst captured (bis 3b), findet
  `capture` für neue Quellen meist schon einen Snapshot → `already_archived` oder, falls der Index
  noch nicht nachgezogen hat, einen zweiten Capture. Vertretbar für die Übergangszeit; 3b beseitigt
  die Doppelung.
- **Abkühlzeiten sind Schätzwerte** (Index-Nachzug „Stunden bis Tage"). Per ENV einstellbar.

## 9. Definition of Done

Siehe `docs/engineering.md`. Abnahme im Betrieb: Migration `0007` läuft, `capture --dry-run`
zeigt 0 (alle 9 Quellen attestiert).

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu:**
- `migrations/versions/0007_capture_request.py`
- `src/wortlaut/store/captures.py`
- `src/wortlaut/pipeline/capture.py`
- `tests/integration/test_capture.py`
- `tests/unit/test_capture_pipeline.py`
- `tests/unit/test_cli_capture.py`

**Ändern:**
- `src/wortlaut/store/models.py` — Klasse `CaptureRequest`
- `src/wortlaut/archive/settings.py` — zwei Felder
- `src/wortlaut/cli.py` — Subcommand `capture`, `_run_capture`, `_CaptureStats`
- `docs/deploy.md` — Ablauf um `capture` ergänzen

## 11. Umsetzungsdetails je Datei

### `migrations/versions/0007_capture_request.py`

Muster `0005_source_archive.py`; `revision = "0007"`, `down_revision = "0006"`. SQL aus §4.1, dann
`CREATE TRIGGER trg_capture_request_immutable BEFORE UPDATE OR DELETE ON capture_request FOR EACH
ROW EXECUTE FUNCTION forbid_mutation()`. `forbid_mutation()` **nicht** neu anlegen. `downgrade()`:
Trigger, Index, Tabelle.

### `src/wortlaut/store/models.py`

Klasse `CaptureRequest` nach dem Muster `SourceArchive`; Fremdschlüssel über `_SOURCE_ID_FK`.

### `src/wortlaut/store/captures.py`

Muster `store/attestations.py`. Für „letzte Anfrage" eine Unterabfrage mit
`DISTINCT ON (source_id) … ORDER BY source_id, requested_at DESC` oder eine korrelierte
Unterabfrage mit `max(requested_at)`; beides ist zulässig. Die Abkühlbedingung aus §4.2 als
SQL-Bedingung, `now`, Abkühlzeiten und `limit` als gebundene Parameter.

### `src/wortlaut/pipeline/capture.py`

Exakt nach §4.3. Imports aus `wortlaut.evidence`, `wortlaut.store`, `wortlaut.archive.errors`,
`wortlaut.archive.wayback_lookup` und dem Protokoll `Archiver` aus `wortlaut.archive.archiver`.
`archiver="wayback"` als Modulkonstante.

### `src/wortlaut/archive/settings.py`

```python
    capture_cooldown_captured_hours: float = 72.0  # Index-Nachzug abwarten (Spec 0130 §0b)
    capture_cooldown_failed_hours: float = 6.0  # Fehlschlag frueher erneut versuchen
```

### `src/wortlaut/cli.py`

Nach §4.4. `now = datetime.now(UTC)` einmal je Lauf im Composition-Root, an die Auswahl übergeben.
Im `finally` alle gebauten Clients schließen (`wayback`, `atoday_inner`, `lookup`, `engine`) über
`_aclose_all`. Exit-Logik **ohne** verschachtelten Ternary. `_CaptureStats` als `@dataclass` mit
einem Zähler je Status, `consecutive_failure`, `record`, `summary_line`.

### `docs/deploy.md`

Im Abschnitt „Erfassungs-Läufe": `capture` zwischen `timestamp` und `attest`; was es tut (erst
CDX, dann ggf. ein Capture, Abkühlzeiten), dass es IA-Zugangsdaten braucht, und dass `ingest` bis
zum nächsten Increment noch selbst captured. Codeblock mit `capture --dry-run`.

### Tests

- **Unit, Pipeline:** Fakes für `WormStore`, `WaybackLookup` (zählt Aufrufe), `Archiver` (zählt
  Aufrufe, liefert URL oder wirft `ArchiveError`) und `insert_capture_request` per `patch` mit
  `return_value=`/`side_effect=`.
- **Unit, CLI:** Muster `tests/unit/test_cli_attest.py`.
- **Integration:** Fixtures `fresh_pg_dsn`, `worm_store`, `seed_attestation`; Anfragen mit
  bestimmtem `requested_at` per rohem SQL anlegen (`requested_at` explizit setzen) für AC3.
- **Sonar-Muster:** ein Aufruf je `pytest.raises`-Block; keine zusammengesetzten Asserts; kein
  Lambda in `patch()`; Literale, die dreimal vorkommen, als Konstante; `async def` nur, wo awaited wird.

## 12. Do-NOT (hart)

- **Keine** Änderung an `ingest`, `attest`, `reparse`, `timestamp`, `serving/`, bestehenden
  Migrationen oder bestehenden Tests.
- **Kein** Schreiben in `source_archive` oder `source`.
- **Kein** Aufruf von archive.today im Capture-Pfad.
- **Kein** neuer Save-Page-Now-Code.
- **Keine** neuen Abhängigkeiten, **keine** Dateien außerhalb von §10.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate fährt der Reviewer selbst.
