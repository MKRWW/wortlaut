# Increment-Spec: Fremdarchivierung backfill-robust + diagnostizierbar (#73)

- **Story/Issue:** #73 · **Status:** Reviewed · **Phase/Layer:** phase/1-mvp · `archive` · `pipeline` · CLI
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#4** (Fremdarchiv-Client), **#7** (Pipeline-Order), **#64** (Ingest-CLI).

## 0. Ursache (gemessene Diagnose, 2026-08-17)

Der Backfill `python -m wortlaut ingest --since 2024-01-01` lieferte
`discovered=159 inserted=0 skipped_duplicate=2 archive_failed=157 spans_total=0`.

Reproduktion gegen die echten Dienste, mit dem projekteigenen `pinned_client` **und**
zum Gegencheck ungepinnt:

| Probe | Ergebnis |
|---|---|
| `GET web.archive.org/save/<url>` (gepinnt, 6× Burst) | **404**, je ~0,2 s, kein `content-location`, kein `Location` |
| dieselbe URL **ungepinnt** | **404** — identisch ⇒ kein Pinning-/SSRF-Artefakt |
| andere Ziele (`example.com`, `bundestag.de`) | **404** ⇒ nicht zielabhängig |
| Browser-User-Agent statt `python-httpx` | **404** ⇒ kein UA-Gating |
| `POST /save/`, `GET /save/`, `/web/<ts>/<url>`, CDX | **503** „Internet Archive: Temporarily Offline" |
| `archive.org/wayback/available` | **502 Bad Gateway** |
| `POST archive.ph/submit/` | **429 — schon beim allerersten Request** |
| Wiederholung über ~15 s | stabil 404 / 429, **kein Flappen** |

Die Ziel-PDFs sind einwandfrei erreichbar (`200 application/pdf`).

> **Ursache: ein Ausfall des Internet Archive — kein Rate-Limit durch unseren Burst.**
> Der Fehler tritt beim *ersten* Request auf, unabhängig von Frequenz, Ziel, Pinning und
> User-Agent; extern bestätigte IA-Störungen am 12./16./17.08.2026. **Die Burst-Hypothese
> ist widerlegt.**

Warum das den *ganzen* Backfill kippt — fünf Defekte im Code, unabhängig vom Ausfall:

1. **`archive.today` ist dauerhaft 429.** Der Retry in `ArchiveTodayArchiver` deckt nur
   `Timeout`/`RemoteProtocolError`/5xx ab; **429 fällt in den `!= 200`-Zweig und wirft sofort
   hart**, ohne Retry. Der eigene Live-Test nennt den Dienst bereits „bot-hostil".
2. Damit degeneriert **„≥1 Archiv reicht" faktisch zu „Wayback erforderlich"** — ein Single
   Point of Failure, der nirgends als solcher benannt ist.
3. **`WaybackArchiver.archive` prüft `response.status_code` überhaupt nicht.** 404/503/429 sind
   von einem echten Miss nicht unterscheidbar; alles kollabiert in dasselbe opake
   `ValueError("no snapshot url")`. Transient vs. permanent ist **prinzipiell nicht
   entscheidbar** — Retry ist so gar nicht implementierbar.
4. **`archive_all` sammelt `errors`, `ingest_source` wirft das Dict weg**
   ([../src/wortlaut/pipeline/ingest.py](../src/wortlaut/pipeline/ingest.py) Z. 72-74) — kein Log,
   keine Summary. Daher 157 **stumme** Fehler.
5. **Keine Drosselung, kein Backoff, kein Abbruchkriterium** — der Lauf zieht 157 PDFs
   (~0,5–0,9 MB) von `dserver.bundestag.de`, um sie alle wegzuwerfen.

**🔴 Beweisketten-Befund (unabhängig vom Ausfall):** `WaybackArchiver.archive` übernimmt
`content-location` bzw. `Location` aus **jeder** Antwort, **ohne Statusprüfung**;
`_validate_snapshot_url` prüft nur Schema und Host. Trägt eine Fehler-/Interstitial-Seite von
`web.archive.org` einen dieser Header, wird ihre URL als `source.archive_wayback` persistiert —
ein **Beweis-Anker, der nie als echter Snapshot verifiziert wurde**. Gleiche Fehlerklasse wie
#25, nur auf dem Archiv-Anker statt auf dem PDF (CLAUDE.md §2.3, R-CORE-02).

## 0b. Stakeholder-Entscheidungen (2026-08-17)

- **Q1 — Verhalten bei längerem Ausfall: Fail-fast, aber laut + Circuit-Breaker.**
  Es wird **nichts ohne Fremdarchiv persistiert** (R-CORE-02 bleibt unangetastet). In-Run-Retry
  deckt echte Blips ab; bei anhaltendem Ausfall **bricht der Lauf früh und diagnostizierbar ab**,
  statt sinnlos weiterzuladen. Der Korpus entsteht per Re-Run, sobald IA zurück ist.
  *Verworfen:* persistente Retry-Queue (Maschinerie ohne Gegenwert bei 159 Dokumenten);
  Entkopplung via `archive_state=pending` (bräche R-CORE-02, ADR-pflichtig).
- **Q2 — Redundanz: Ist-Zustand codifizieren.** **Wayback ist Pflicht, `archive.today` ist
  Bonus.** `archive_today=None` wird toleriert; fehlt `wayback_url`, wird die Source **nicht**
  gespeichert. Der Single Point of Failure bleibt **bewusst** bestehen und ist unten als Risiko
  dokumentiert. *Verworfen:* dritter Archivdienst (eigenes Increment); WORM+Hash als primärer
  Anker (schwächt die Unabhängigkeit der Beweiskette, ADR-pflichtig).

## 1. Ziel
Ein Backfill scheitert nie mehr **stumm**: jeder Archiv-Fehler ist mit Dienst, Statuscode und
Grund sichtbar, transiente Fehler werden gedrosselt und wiederholt, ein anhaltender Ausfall
bricht den Lauf **früh und mit klarer Ursache** ab — und es wird **kein Snapshot-Anker
akzeptiert, der nicht aus einer Erfolgsantwort stammt**.

## 2. Nicht-Ziele (Scope-Grenze)
- **Keine** persistente Retry-Queue, **kein** `archive_state=pending`, **keine** Entkopplung von
  Archiv und Ingest (Q1 verworfen).
- **Kein** dritter Archivdienst, **keine** Archiv-Credentials/Accounts (Q2 verworfen).
- **Keine** Änderung der Pipeline-Reihenfolge `fetch→hash→dedup→archiv→WORM→insert` (der
  Circuit-Breaker begrenzt die verschwendeten Fetches, statt die Ordnung umzubauen).
- **Keine** Verifikation, dass der Snapshot später wirklich abrufbar ist (kein Re-Fetch des
  Snapshots) — nur die Erfolgsantwort wird geprüft.
- **Keine** Parallelisierung des Ingest-Loops.
- **Keine** Änderung an Span-Parsing, Recht/`rights_basis` oder Serving.

## 3. Betroffene Interfaces / Öffentliche Signaturen

```python
# ── NEU: src/wortlaut/archive/errors.py (nur stdlib) ────────────────────
class ArchiveError(Exception):
    """Strukturierter Archiv-Fehler — trägt den Grund bis in Log und Summary."""

    def __init__(
        self,
        service: str,            # 'wayback' | 'archive_today'
        reason: str,             # 'http_status'|'timeout'|'transport'|'no_snapshot_url'
                                 # |'invalid_snapshot_url'|'disabled'
        *,
        status_code: int | None = None,
        transient: bool = False,
    ) -> None: ...

    service: str
    reason: str
    status_code: int | None
    transient: bool

    def label(self) -> str:
        """Kompakter Aggregations-Schlüssel, z.B. 'wayback:http_status_404'."""

# ── NEU: src/wortlaut/archive/retry.py (stdlib + archive.errors) ────────
async def with_retry(
    operation: Callable[[], Awaitable[str]],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Wiederholt operation NUR bei ArchiveError.transient; exponentieller Backoff
    (base * 2**(n-1)). Permanente Fehler fliegen sofort durch. Der letzte Fehler wird
    weitergereicht. `sleep` ist injizierbar — Unit-Tests warten nie real (R-TEST-03)."""

# ── NEU: src/wortlaut/archive/throttle.py (stdlib + archive.errors) ─────
class RateLimiter:
    """Erzwingt einen Mindestabstand zwischen zwei Calls (kein Burst)."""

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None: ...

    async def acquire(self) -> None: ...

class DisableAfterFailures:
    """Archiver-Dekorator: legt einen OPTIONALEN Dienst nach `limit` Fehlern im Lauf still.

    Danach wirft `archive` sofort ArchiveError(reason='disabled', transient=False),
    ohne HTTP-Call. Verhindert, dass ein dauerhaft 429-blockierter Dienst jeden Ingest
    um attempts×Backoff verlängert. Erfüllt das Archiver-Protokoll.
    """

    def __init__(self, inner: Archiver, *, service: str, limit: int) -> None: ...
    async def archive(self, origin_url: str) -> str: ...
    async def aclose(self) -> None: ...   # delegiert an inner, falls vorhanden

# ── NEU: src/wortlaut/archive/settings.py (ENV-Präfix WORTLAUT_ARCHIVE_) ─
class ArchiveSettings(BaseSettings):
    wayback_min_interval_seconds: float = 5.0
    archive_today_min_interval_seconds: float = 15.0
    retry_attempts: int = 3
    retry_base_delay_seconds: float = 2.0
    optional_failure_limit: int = 3        # archive.today im Lauf stilllegen
    consecutive_failure_limit: int = 5     # Circuit-Breaker für den ganzen Lauf

# ── GEÄNDERT: src/wortlaut/archive/archiver.py ──────────────────────────
@dataclass(frozen=True)
class ArchiveResult:
    wayback_url: str | None
    archive_today_url: str | None
    failures: dict[str, ArchiveError]   # ERSETZT `errors: dict[str, str]`

class WaybackArchiver:
    def __init__(
        self,
        *,
        limiter: RateLimiter | None = None,
        attempts: int = 3,
        base_delay_seconds: float = 2.0,
    ) -> None: ...

class ArchiveTodayArchiver:
    def __init__(
        self,
        *,
        limiter: RateLimiter | None = None,
        attempts: int = 2,
        base_delay_seconds: float = 2.0,
    ) -> None: ...
# archive_all: Signatur unverändert; meldet weiterhin NUR (Spec 0004 §3) —
# die Hard/Soft-Entscheidung trifft der Aufrufer (pipeline).

# ── GEÄNDERT: src/wortlaut/pipeline/ingest.py ───────────────────────────
@dataclass(frozen=True)
class IngestOutcome:
    status: Literal["inserted", "skipped_duplicate", "archive_failed"]
    source_id: UUID | None
    content_hash: str
    span_count: int = 0
    archive_failures: tuple[str, ...] = ()   # ArchiveError.label() je Dienst
```

- **Layering (R-ARCH-02):** alle neuen Module liegen **in** `wortlaut.archive` und importieren
  keinen anderen wortlaut-Layer — der Contract `archive-ist-unabhaengig` bleibt erfüllt.
  `archiver.py` bleibt frei von `pydantic`: `ArchiveSettings` wird **nur** im Composition-Root
  (CLI) gelesen und als einfache Werte injiziert (DI).
- **Import-Richtung:** `cli → pipeline → archive`; unverändert.

## 4. Design (kurz)

**4.1 Status-Gate zuerst (behebt 🔴 + macht Retry überhaupt möglich).**
`WaybackArchiver.archive` akzeptiert eine Snapshot-URL **nur** aus einer Erfolgsantwort:

| Antwort | Ergebnis |
|---|---|
| 2xx **mit** `content-location` | Snapshot (nach Schema-/Host-Validierung) |
| 3xx **mit** `Location` | Snapshot (nach Schema-/Host-Validierung) |
| 429 · 408 · 5xx | `ArchiveError(reason='http_status', transient=True)` |
| sonstige 4xx (404, 403, …) | `ArchiveError(reason='http_status', transient=False)` |
| 2xx/3xx **ohne** verwertbaren Header | `ArchiveError(reason='no_snapshot_url', transient=False)` |
| Schema ≠ https oder fremder Host | `ArchiveError(reason='invalid_snapshot_url', transient=False)` |
| `httpx.TimeoutException` | `ArchiveError(reason='timeout', transient=True)` |
| `httpx.TransportError` | `ArchiveError(reason='transport', transient=True)` |

`ArchiveTodayArchiver` klassifiziert identisch (429 wird damit endlich als transient erkannt);
sein Ad-hoc-Retry entfällt zugunsten von `with_retry`.

**4.2 Drosselung.** Jeder Archiver bekommt einen eigenen `RateLimiter` (getrennte Limits je
Dienst). `acquire()` läuft **vor jedem Versuch**, auch vor Retries — Backoff und Mindestabstand
addieren sich, es entsteht kein Burst.

**4.3 Soft/Hard-Fail (Q2).** `archive_all` bleibt reiner Reporter. `ingest_source` entscheidet:
**fehlt `wayback_url` ⇒ `archive_failed`** (Source wird nicht gespeichert), unabhängig von
`archive_today`. `archive_today=None` bei gesetztem `wayback_url` ⇒ **`inserted`**, die Soft-Failure
wird trotzdem geloggt.

**4.4 Observability.** `ingest_source` loggt **jede** Failure aus `res.failures` als WARNING mit
Dienst, Ziel-URL und `ArchiveError` (also Grund + Statuscode) — auch dann, wenn der Lauf trotzdem
erfolgreich ist. `IngestOutcome.archive_failures` trägt die `label()`s nach oben; die CLI
aggregiert sie zu einer Gründe-Verteilung in der Summary-Zeile:
`archive_failed=157 reasons=wayback:http_status_404=157,archive_today:http_status_429=157`.

**4.5 Circuit-Breaker (Q1).** Die CLI zählt **aufeinanderfolgende** `archive_failed`-Outcomes.
Bei `consecutive_failure_limit` bricht der Lauf ab, gibt die Summary **inklusive Gründe** aus,
nennt die Ursache explizit und endet mit **Exit-Code 3** (abgegrenzt von 2 = Konfiguration).
Ein Erfolg oder ein Dedup-Skip setzt den Zähler zurück.

**4.6 Resumability (unverändert, wird nur abgesichert).** `archive_failed` schreibt **keine**
Source-Zeile ⇒ ein Re-Run entdeckt und versucht sie erneut, während bereits gespeicherte per
Content-Hash-Dedup übersprungen werden. Das ist heute schon so und wird durch AC10 gegen
Regression festgenagelt.

**4.7 Kein Secret, keine Live-Calls im Unit-Test.** Weder URL noch Log enthalten Credentials
(es gibt keine). `sleep`/`monotonic` sind injizierbar, damit Retry- und Throttle-Tests
deterministisch und ohne reale Wartezeit laufen (R-TEST-03).

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)

- [ ] **AC1** — *Given* Wayback antwortet **429**, danach **200 mit `content-location`**, *When*
      `WaybackArchiver.archive`, *Then* Ergebnis ist die Snapshot-URL, `sleep` wurde **genau 1×**
      mit `base_delay_seconds` aufgerufen, und **kein** `ArchiveError` verlässt die Methode. `[unit]`
- [ ] **AC2** — *Given* Wayback wirft 2× `httpx.TimeoutException`, dann 302 mit `Location`, *When*
      `archive` mit `attempts=3`, *Then* Snapshot-URL; `sleep`-Aufrufe = `[base, base*2]`
      (exponentieller Backoff). `[unit]`
- [ ] **AC3** — *Given* Wayback antwortet **404**, *When* `archive` mit `attempts=3`, *Then*
      `ArchiveError(service='wayback', reason='http_status', status_code=404, transient=False)`
      und **genau 1** HTTP-Call (permanent ⇒ kein Retry). `[unit]`
- [ ] **AC4** *(🔴 Beweis-Anker)* — *Given* Wayback antwortet **503** *mit* gesetztem
      `content-location` auf `web.archive.org`, *When* `archive`, *Then* `ArchiveError`
      (`reason='http_status'`, `status_code=503`, `transient=True`) — die URL wird **nicht**
      als Snapshot zurückgegeben. `[unit]`
- [ ] **AC5** — *Given* `archive_today` schlägt fehl, `wayback` liefert Snapshot `S`, *When*
      `ingest_source`, *Then* Status `inserted`, `source.archive_wayback == S`,
      `source.archive_today is None` — **und** ein WARNING-Log nennt `archive_today` samt Grund.
      `[unit]` + `[integration]`
- [ ] **AC6** — *Given* `wayback` schlägt fehl, `archive_today` liefert eine Snapshot-URL, *When*
      `ingest_source`, *Then* Status `archive_failed` und **keine** Source-Zeile in der DB
      (Wayback ist Pflicht, Q2). `[unit]` + `[integration]`
- [ ] **AC7** — *Given* ein Lauf, in dem jede Archivierung fehlschlägt, *When* die CLI
      `consecutive_failure_limit` erreicht, *Then* bricht der Lauf ab, Exit-Code ist **3**, es
      werden **nicht mehr** als `limit` Sources verarbeitet, und die Ausgabe nennt Abbruchgrund
      und Gründe-Verteilung. `[unit]`
- [ ] **AC8** — *Given* ein Lauf mit ≥1 Archiv-Fehler, *When* die CLI endet, *Then* enthält die
      Summary-Zeile ein `reasons=`-Feld, in dem jeder Grund mit Dienst, Kürzel und Anzahl steht
      (z.B. `wayback:http_status_404=3`). `[unit]`
- [ ] **AC9** — *Given* ein `RateLimiter(min_interval_seconds=5)` mit injizierter Uhr, *When* 3
      `acquire()` unmittelbar hintereinander, *Then* wird 2× geschlafen, mit jeweils ≈5 s —
      der erste Call wartet nicht. `[unit]`
- [ ] **AC10** *(Resumability)* — *Given* Lauf 1, in dem Wayback fehlschlägt, danach Lauf 2 über
      dieselbe Quelle mit funktionierendem Wayback, *When* beide Läufe, *Then* Lauf 1 →
      `archive_failed` + 0 Zeilen; Lauf 2 → `inserted` + 1 Zeile; ein dritter Lauf →
      `skipped_duplicate` **ohne** Archiv-Call. `[integration]`
- [ ] **AC11** — *Given* ein optionaler Archiver, der `optional_failure_limit`-mal in Folge
      fehlschlägt, *When* der nächste `archive`-Aufruf, *Then*
      `ArchiveError(reason='disabled', transient=False)` **ohne** HTTP-Call am inneren Archiver
      (Call-Count bleibt konstant). `[unit]`
- [ ] **AC12** — *Given* `archive_all`, bei dem beide Dienste fehlschlagen, *When* aufgerufen,
      *Then* beide URLs `None` und `failures` enthält für **beide** Dienste einen `ArchiveError`
      mit gesetztem `reason` (Ersatz des alten `errors`-Dicts). `[unit]`

> Jedes AC ist von einem automatisierten Test mit Ja/Nein beantwortbar.

## 6. Testplan (Test-zu-AC-Mapping)

**Unit (rein, httpx gemockt, `sleep`/`monotonic` injiziert):**
- `tests/unit/test_archive_retry.py` — `test_wayback_429_then_success` → AC1 ·
  `test_wayback_timeout_backoff_sequence` → AC2 · `test_wayback_404_no_retry` → AC3
- `tests/unit/test_archiver.py` *(erweitert)* — `test_wayback_5xx_with_content_location_rejected`
  → **AC4** · `test_archive_all_failures_structured` → AC12
- `tests/unit/test_throttle.py` — `test_rate_limiter_spaces_calls` → AC9 ·
  `test_disable_after_failures_stops_calling_inner` → AC11
- `tests/unit/test_pipeline_order.py` *(erweitert)* — `test_archive_today_soft_fail_inserts` → AC5 ·
  `test_wayback_hard_fail_blocks_insert` → AC6
- `tests/unit/test_cli.py` *(erweitert)* — `test_circuit_breaker_aborts_run` → AC7 ·
  `test_summary_reports_failure_reasons` → AC8

**Integration (Testcontainers, echte Postgres/MinIO):**
- `tests/integration/test_pipeline_ingest.py` *(erweitert)* — Soft-Fail speichert mit
  `archive_wayback` gesetzt / `archive_today NULL` → AC5 · Hard-Fail schreibt **keine** Zeile → AC6
- `tests/integration/test_cli_ingest.py` *(erweitert)* — `test_archive_failed_retried_on_rerun`
  → **AC10** (drei Läufe: failed → inserted → skipped_duplicate)

**Invarianten (Pflicht, R-DATA):** unverändert — dieser Increment fügt **keinen**
UPDATE/DELETE-Pfad auf `source`/`span` hinzu; die Append-only-Trigger-Tests bleiben gültig
und müssen grün bleiben.

**Anzupassen (Signaturänderung `errors` → `failures`):** `test_partial_failure_tolerated`,
`test_total_failure_reported` (`tests/unit/test_archiver.py`) und
`tests/live/test_archive_live.py`. Die archive.today-Retry-Tests
(`test_archive_today_retry_then_success`, `…_5xx_retry_then_success`, `…_5xx_twice_raises`,
`…_timeout_twice_raises`, `…_unexpected_status_raises`) wandern auf `with_retry` +
`ArchiveError` und müssen entsprechend erwarten.

## 7. Recht / Security

- **Beweis-Integrität (R-CORE-02, CLAUDE.md §2.3):** Kern des Increments. Ein Snapshot-Anker wird
  **nur** aus einer Erfolgsantwort übernommen (AC4). „Was, wenn diese Bytes nicht sind, was sie
  vorgeben?" — eine 503-Seite mit `content-location` ist genau dieser Fall und wird jetzt hart
  abgelehnt, statt still als `archive_wayback` in den Ledger zu wandern.
- **Provenienz vor Verarbeitung bleibt unangetastet:** ohne Wayback-Snapshot **keine** Source-Zeile
  (Q1/Q2). Es entsteht **kein** `pending`-Zustand und kein Pfad, der ungeprüfte Quellen speichert.
- **Immutability (R-DATA-01):** kein neuer UPDATE-/DELETE-Pfad; `archive_failed` schreibt gar nichts.
- **SSRF (R-SEC-05):** `assert_url_allowed` und der gepinnte Transport bleiben unverändert; die
  Diagnose hat ausdrücklich bestätigt, dass das Pinning **nicht** die Fehlerursache war.
- **Secrets (R-SEC-01):** keine neuen Credentials; Fehler-Logs enthalten Dienst, Statuscode und
  Ziel-URL — keine Tokens, keine Query-Secrets.
- **Fremd-Content bleibt Daten (R-SEC-07):** aus Archiv-Antworten werden ausschließlich
  Statuscode und schema-/host-validierte URLs gelesen, nie Anweisungen.

## 8. Risiken & offene Fragen

- **🟠 Bewusst akzeptiert (Q2): Wayback ist ein Single Point of Failure.** Fällt IA aus, steht der
  Korpusaufbau vollständig. Dieser Increment macht das **sichtbar und schnell**, beseitigt es
  aber nicht. Folge-Increment „dritter unabhängiger Archivdienst" ist der Ausweg und sollte
  eingeplant werden, solange die Demo davon abhängt.
- **404 als „permanent" klassifiziert.** Im beobachteten Ausfall war die 404 faktisch transient
  (IA kommt zurück), aber In-Run-Retry hilft dagegen nachweislich nicht (stabil über Tage). Die
  Kombination „404 = permanent + Circuit-Breaker" ist deshalb das gewünschte Verhalten; ein
  späterer Lauf holt es nach.
- **Verschwendete Fetches vor dem Abbruch.** Bis der Circuit-Breaker greift, werden bis zu
  `consecutive_failure_limit` PDFs geladen und verworfen. Bewusst in Kauf genommen, um die
  Pipeline-Reihenfolge nicht umzubauen (Nicht-Ziel).
- **Live-Test `test_archive_all_live_real_snapshot` ist derzeit rot**, weil IA ausgefallen ist.
  Er ist per Marker aus dem CI deselektiert; der Increment ändert daran nichts. Der Zustand ist
  **kein** Merge-Blocker, muss aber im PR benannt werden.
- **Defaults der Drosselung sind geschätzt**, nicht gemessen (IA veröffentlicht keine harten
  SPN-Limits). Sie sind deshalb per ENV konfigurierbar; bei erneutem Massen-Backfill nachjustieren.
- **Möglicher Split, falls der Increment zu groß wird:** (a) Status-Gate + strukturierte Fehler +
  Observability + Soft/Hard-Semantik, (b) Drosselung + Retry + Circuit-Breaker. Die Reihenfolge ist
  zwingend — (b) setzt die Fehlerklassifikation aus (a) voraus.

## 9. Definition of Done (Verweis)

[../docs/rules.md](../docs/rules.md) DoD: alle AC grün (Unit + Integration; `live` separat),
alle Gates grün (ruff · mypy strict inkl. Tests · pytest Unit+Integration · import-linter ·
Coverage ≥ 80 · Security-Gate · SonarCloud 0 Issues), Review durch Architekt, Invarianten gewahrt,
keine Gott-Klassen, kein Secret, keine Live-Calls im CI-Gate. PR referenziert **#73**
(`Closes #73`) gegen `develop`.

---

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu anlegen:**
- `src/wortlaut/archive/errors.py`      — `ArchiveError`
- `src/wortlaut/archive/retry.py`       — `with_retry`
- `src/wortlaut/archive/throttle.py`    — `RateLimiter`, `DisableAfterFailures`
- `src/wortlaut/archive/settings.py`    — `ArchiveSettings`
- `tests/unit/test_archive_retry.py`    — AC1, AC2, AC3
- `tests/unit/test_throttle.py`         — AC9, AC11

**Ändern (chirurgisch, nichts Umliegendes umbauen):**
- `src/wortlaut/archive/archiver.py`    — Status-Gate, Klassifikation, Limiter/Retry, `failures`
- `src/wortlaut/pipeline/ingest.py`     — Logging jeder Failure, Wayback-Pflicht, `archive_failures`
- `src/wortlaut/cli.py`                 — `ArchiveSettings`, Gründe-Aggregation, Circuit-Breaker
- `tests/unit/test_archiver.py`         — AC4, AC12 + Anpassung `errors` → `failures`
- `tests/unit/test_pipeline_order.py`   — AC5, AC6
- `tests/unit/test_cli.py`              — AC7, AC8
- `tests/live/test_archive_live.py`     — nur `result.errors` → `result.failures`

> NICHT anfassen: `pyproject.toml`, `.importlinter`, `migrations/`, Alembic, alle übrigen
> Integrationstests, `src/wortlaut/serving/**`, `src/wortlaut/store/**`, `src/wortlaut/ingest/**`,
> `src/wortlaut/archive/ssrf.py`, `src/wortlaut/archive/pinned.py`.
> Die Integrationstests (AC5/AC6/AC10) zieht der Architekt separat nach.

## 11. Umsetzungsdetails je Datei

### `src/wortlaut/archive/errors.py` (neu)
`ArchiveError(Exception)` mit `__init__(self, service, reason, *, status_code=None,
transient=False)`; setzt die vier gleichnamigen Attribute und ruft
`super().__init__(str(self))` **nicht** — stattdessen `__str__` implementieren als
`f"{service}: {reason}"` plus `f" {status_code}"`, wenn gesetzt, plus `" (transient)"`,
wenn transient. `label()` liefert `f"{service}:{reason}"`, bei gesetztem `status_code`
`f"{service}:{reason}_{status_code}"`.

### `src/wortlaut/archive/retry.py` (neu)
`with_retry` ruft `operation()` bis zu `attempts` Mal. Fängt **nur** `ArchiveError`:
ist `transient` False → sofort weiterwerfen. Ist es der letzte Versuch → weiterwerfen.
Sonst `await sleep(base_delay_seconds * 2 ** (versuch_index))` und erneut versuchen
(erster Backoff = `base_delay_seconds`). Andere Exceptions **nie** fangen.

### `src/wortlaut/archive/throttle.py` (neu)
`RateLimiter.acquire`: beim ersten Aufruf nie schlafen, danach
`wartezeit = min_interval_seconds - (jetzt - letzter_call)`; ist sie > 0, `await sleep(wartezeit)`.
Nach dem (ggf. Warten) `letzter_call` auf `monotonic()` setzen.
`DisableAfterFailures.archive`: ist der interne Fehlerzähler ≥ `limit`, sofort
`ArchiveError(service, "disabled")` werfen **ohne** `inner.archive` aufzurufen. Sonst
`inner.archive` aufrufen; bei Erfolg Zähler auf 0 zurücksetzen und Ergebnis liefern, bei
`ArchiveError` Zähler erhöhen und weiterwerfen. `aclose` delegiert an `inner.aclose`, falls
vorhanden (`getattr`-Prüfung), sonst NO-OP.

### `src/wortlaut/archive/archiver.py` (ändern)
- Eine Hilfsfunktion, die aus einer `httpx.Response` **entweder** die Snapshot-URL **oder**
  einen `ArchiveError` erzeugt, exakt nach der Tabelle in §4.1. Sie ist die **einzige** Stelle,
  die `content-location`/`Location` liest — und sie liest sie **erst nach** der Statusprüfung.
- `WaybackArchiver.archive` und `ArchiveTodayArchiver.archive` bestehen danach nur noch aus:
  `await limiter.acquire()` (falls Limiter gesetzt) → HTTP-Call in `try` (httpx-Timeout- und
  Transport-Fehler in `ArchiveError` übersetzen) → Antwort durch die Hilfsfunktion. Der ganze
  Ablauf wird in `with_retry` gewickelt; `acquire()` muss **innerhalb** der wiederholten
  Operation liegen, damit auch Retries gedrosselt sind.
- Der handgeschriebene Retry-Block und `_backoff` in `ArchiveTodayArchiver` **entfallen**.
- `ArchiveResult.errors` heißt jetzt `failures: dict[str, ArchiveError]`; `archive_all` fängt
  `ArchiveError` je Dienst und legt sie unter `"wayback"` bzw. `"archive_today"` ab. Ein
  `SsrfBlocked` aus `assert_url_allowed` fliegt weiterhin **unverändert durch** (kein Abfangen).
- `_validate_snapshot_url` wirft künftig `ArchiveError(..., reason="invalid_snapshot_url")`
  statt `ValueError`.

### `src/wortlaut/pipeline/ingest.py` (ändern)
Direkt nach `archive_all`: über `res.failures.items()` iterieren und je Eintrag
`logger.warning("archive %s fehlgeschlagen (%s): %s", dienst, raw.origin_url, fehler)` —
**immer**, auch wenn danach erfolgreich eingefügt wird. Danach die Abbruchbedingung von
`res.wayback_url is None and res.archive_today_url is None` auf **`res.wayback_url is None`**
ändern und `IngestOutcome("archive_failed", None, h, archive_failures=<labels>)` liefern.
`archive_failures` ist in **beiden** Rückgabepfaden (`archive_failed` und `inserted`) mit
`tuple(f.label() for f in res.failures.values())` gefüllt.

### `src/wortlaut/cli.py` (ändern)
- `ArchiveSettings()` neben den anderen Settings laden (im selben `try`, gleiche
  Fehlerbehandlung, Exit 2).
- Archiver mit `RateLimiter(...)`, `attempts` und `base_delay_seconds` aus den Settings bauen;
  `atoday` zusätzlich in `DisableAfterFailures(..., service="archive_today",
  limit=settings.optional_failure_limit)` wickeln. Für `aclose` im `finally` weiterhin die
  **inneren** Objekte referenzieren oder die `aclose`-Delegation des Wrappers nutzen.
- Einen `collections.Counter` über alle `outcome.archive_failures` führen.
- Bei `archive_failed` eine Zeile nach stderr:
  `archive_failed: <origin_url>: <kommaseparierte labels>`.
- Zähler `consecutive_archive_failed`: bei `archive_failed` erhöhen, bei jedem anderen Status
  auf 0 setzen. Erreicht er `settings.consecutive_failure_limit`, Schleife verlassen, nach
  stderr eine Zeile schreiben, die das Limit und den häufigsten Grund nennt, die Summary
  ausgeben und **3** zurückgeben.
- Die Summary-Zeile bekommt ein zusätzliches Feld
  `reasons=<label>=<n>,<label>=<n>` (nach Häufigkeit absteigend sortiert, bei Gleichstand
  alphabetisch); ist der Counter leer, `reasons=-`. Die bestehenden Felder und ihre
  Reihenfolge bleiben **unverändert**, `reasons=` wird hinten angehängt.

## 12. Do-NOT (hart)
- KEINE git-, docker-, uv-, npm-, alembic- oder pytest-Befehle ausführen — nur die in §13.
- KEINE anderen als die in §10 genannten Dateien anlegen oder ändern.
- KEIN Netz-Call und KEIN echtes `asyncio.sleep` in Unit-Tests — `sleep`/`monotonic` werden
  injiziert. Tests dürfen nicht real warten.
- KEINE Snapshot-URL aus einer Nicht-Erfolgsantwort zurückgeben (das ist der Kern von AC4).
- KEIN `archive_state`, KEIN `pending`, KEIN Speichern einer Source ohne Wayback-Snapshot.
- KEIN UPDATE/DELETE auf `source`/`span`. KEIN LLM. KEINE Secrets in Logs oder URLs.
- `SsrfBlocked` NICHT in `ArchiveError` umwandeln und NICHT abfangen.
- Bestehende öffentliche Namen NICHT umbenennen außer `ArchiveResult.errors` → `failures`.
- Keine erfundenen Feld-/Spaltennamen — bestehende Dateien vorher lesen.

## 13. Abschluss (und NUR das an Kommandos ausführen)
- `git status --porcelain` ausgeben.
