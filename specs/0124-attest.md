# Increment-Spec: Attestierung prüfen — `source_archive` + `attest` (#124)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #124 · **Epic:** #123 (Increment 1) · **Status:** Draft
- **Phase/Layer:** phase/1-mvp · `archive`, `evidence`, `store`, `pipeline`, `cli`, Migration
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Entscheidung: [ADR-0009](../docs/adr/0009-pflicht-anker-und-zitierfaehigkeit.md) §2, §3
- Baut auf **#76** (append-only Zusatztabelle, abgeleiteter Rückstand), **#108** (Wayback-Client,
  verzögerte Abrufbarkeit), **#118** (Muster Pass + CLI).

## 0. Ausgangslage

ADR-0009 macht Eigenschaft A zur Bedingung für Spans und Ausgabe — und verlangt, dass A erst gilt,
wenn die **Bytes eines Snapshots nachweislich gleich `content_hash`** sind. Heute prüft das kein
Code: Gespeichert wird nur die URL, die Save Page Now zurückmeldet (`source.archive_wayback`).

Dieses Increment baut die Prüfung — **rein additiv**. Es ändert weder `ingest` noch `reparse` noch
den Read-Pfad. Spans und Ausgabe an die Attestierung zu binden ist Increment 2.

### 0a. Gemessen: das Rezept funktioniert (2026-10-01)

```
GET https://web.archive.org/web/20260805170741id_/https://dserver.bundestag.de/btp/21/21090.pdf
→ 200, Content-Type application/pdf, 1 465 507 Bytes, keine Umleitung
  Memento-Datetime: Wed, 05 Aug 2026 17:07:41 GMT
  SHA-256 = 57e33f7d…2612 = source.content_hash  ✔
```

Der Zusatz `id_` hinter dem Zeitstempel liefert die **Rohbytes** ohne Umschreibung durch die
Wayback-Oberfläche.

### 0b. Gemessen: der CDX-Digest taugt als Vorfilter

```
GET https://web.archive.org/cdx/search/cdx?url=dserver.bundestag.de/btp/21/21090.pdf
    &output=json&fl=timestamp,original,statuscode,digest,mimetype
```

Die Spalte `digest` ist **SHA-1 der Rohbytes, Base32-kodiert**. Für 21/90:
`JGGDIPGPZ6TTUZU5L3XVQYM43M6VY6NR` — identisch mit `base32(sha1(bytes))`. Kandidaten lassen sich
damit ohne Download vorfiltern. **SHA-1 ist nur der Filter, nie der Beweis** — der Beweis ist der
SHA-256 über die geladenen Bytes.

### 0c. Gemessen: der eigene Capture ist ein `revisit` — Falle für einen Status-Filter

Im CDX steht unser eigener Capture vom 05.08. so:

```
["20260805170741", "-", "JGGDIPGPZ6TTUZU5L3XVQYM43M6VY6NR", "warc/revisit"]
```

Status `-`, Typ `warc/revisit`: Der Internet Archive hat ihn dedupliziert, weil die Bytes schon
bekannt waren. Der `id_`-Abruf liefert trotzdem 200 und die vollen Bytes (0a). **Ein Filter
„statuscode = 200" würde genau diese Snapshots verwerfen.** Kandidat ist deshalb jede Zeile mit
passendem Digest, deren Status `200` **oder** `-` ist.

### 0d. Gemessen: Umleitung = anderer Snapshot

```
GET …/web/20261001132553id_/https://dserver.bundestag.de/btp/21/21097.pdf   (eine Sekunde daneben)
→ 302, Location: …/web/20260928123406id_/…   (ein anderer, älterer Snapshot)
```

Wayback leitet auf den **zeitlich nächsten** Snapshot um. Wer dem folgt, prüft einen anderen
Snapshot als den, den er festhalten will. **Umleitungen werden nie gefolgt.** Ein 3xx bedeutet:
Dieser Snapshot ist (noch) nicht exakt abrufbar.

Derselbe Abruf mit dem **exakten** Zeitstempel unseres Captures von 21/97 (13:25 UTC) ergab vier
Stunden später ebenfalls 302 — ein frischer Capture ist anfangs nicht abrufbar (vgl. #108).

## 1. Ziel

`python -m wortlaut attest` sucht für jede Quelle ohne Attestierung einen Wayback-Snapshot
**derselben URL mit denselben Bytes** und schreibt nur bei nachgewiesener Gleichheit eine Zeile in
die neue append-only Tabelle `source_archive`. Die Gleichheit ist zusätzlich **in der Datenbank**
erzwungen. Das Kommando liest nur und löst nie einen Capture aus.

## 2. Nicht-Ziele (Scope-Grenze)

- **Kein** Capture, kein Save-Page-Now-Aufruf, keine Zugangsdaten (Entscheidung #124).
- **Keine** Änderung an `ingest`, `reparse`, `timestamp`, Read-Pfad, `/verify` (Increment 2/3).
- **Kein** Entfernen von `chk_archive` oder `source.archive_wayback` (Increment 3).
- **Kein** zweiter Archivar. Die Registry enthält genau `wayback`.
- **Kein** Zeitstempeln der Attestierung (offene Frage des Epics, eigenes Ticket).

## 3. Betroffene Interfaces / Öffentliche Signaturen

```python
# src/wortlaut/evidence/hashing.py — additiv
def sha1_base32(raw: bytes) -> str: ...          # Format des CDX-Digests

# src/wortlaut/archive/wayback_lookup.py — NEU (Archive-Layer, importiert keinen anderen Layer)
@dataclass(frozen=True)
class SnapshotCandidate:
    timestamp: str        # 14 Ziffern, YYYYMMDDhhmmss
    original: str         # URL laut CDX

class WaybackLookup(Protocol):
    async def candidates(self, origin_url: str, *, sha1_b32: str) -> list[SnapshotCandidate]: ...
    async def fetch(self, candidate: SnapshotCandidate) -> bytes | None: ...
    async def aclose(self) -> None: ...

class HttpWaybackLookup:  # erfüllt WaybackLookup
    def __init__(self, *, limiter: RateLimiter | None = None,
                 max_bytes: int = 100 * 1024 * 1024, attempts: int = 3,
                 base_delay_seconds: float = 2.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None: ...

def snapshot_url(candidate: SnapshotCandidate) -> str: ...   # https://web.archive.org/web/<ts>/<original>

# src/wortlaut/store/attestations.py — NEU
@dataclass(frozen=True)
class PendingAttestation:
    source_id: UUID
    content_hash: str
    raw_bytes_ref: str
    origin_url: str
    retrieved_at: datetime

@dataclass(frozen=True)
class NewSourceArchive:
    source_id: UUID
    archiver: str
    snapshot_url: str
    snapshot_at: datetime
    verified_sha256: str

async def list_sources_without_attestation(
    session: AsyncSession, *, limit: int | None = None
) -> list[PendingAttestation]: ...
async def insert_source_archive(session: AsyncSession, row: NewSourceArchive) -> UUID: ...

# src/wortlaut/pipeline/attest.py — NEU
ATTESTING_ARCHIVERS: tuple[str, ...] = ("wayback",)

@dataclass(frozen=True)
class AttestOutcome:
    status: Literal["attested", "no_matching_snapshot", "snapshot_unavailable",
                    "bytes_mismatch", "hash_mismatch", "worm_missing", "error"]
    source_id: UUID
    snapshot_url: str | None = None

async def attest_source(
    pending: PendingAttestation, *, session: AsyncSession, worm: WormStore,
    lookup: WaybackLookup, max_candidates: int = 3,
) -> AttestOutcome: ...

# src/wortlaut/cli.py — neues Subcommand
#   python -m wortlaut attest [--limit N] [--dry-run] [--no-migrate]
```

## 4. Design — die fünf Entscheidungen

### 4.1 Die Datenbank erzwingt die Gleichheit

Migration `0005`: Tabelle `source_archive` mit `verified_sha256 char(64)`. Ein `BEFORE INSERT`-
Trigger vergleicht `NEW.verified_sha256` mit `source.content_hash` der referenzierten Quelle und
**verweigert den Insert bei Ungleichheit**. Damit gilt „eine Zeile nur bei gleichen Bytes" auch
dann, wenn Anwendungscode irrt. Dazu der bekannte Append-only-Trigger (`forbid_mutation()`) und
`UNIQUE (source_id, archiver)`. Der Rückstand ist **abgeleitet** (keine Zeile), kein Flag.

Die Tabelle referenziert Quelle und Snapshot, **keine** S3-Version im eigenen Speicher (#122).

### 4.2 Suche: CDX, exakte URL, Digest-Filter, Revisits eingeschlossen

`candidates()` ruft CDX mit der **exakten** `origin_url` (kein Präfix-Match) auf, Felder
`timestamp,original,statuscode,digest,mimetype`, Ausgabe JSON. Behalten wird jede Zeile mit
`digest == sha1_b32` **und** `statuscode in {"200", "-"}` (0c) **und** deren `original` in Host,
Pfad und Query gleich `origin_url` ist (Schema `http`/`https` darf abweichen, Host
case-insensitiv). Die Pipeline sortiert die Kandidaten nach Abstand des Zeitstempels zu
`retrieved_at` (nächster zuerst) und prüft höchstens `max_candidates`.

### 4.3 Abruf: exakt, ohne Umleitung, mit Größengrenze

`fetch()` ruft `https://web.archive.org/web/<timestamp>id_/<original>` mit dem gepinnten Client
(`pinned_client`, `follow_redirects=False`) ab.

- **200** → Bytes zurück, aber nur wenn der Header `Memento-Datetime` (RFC 1123, GMT) genau dem
  Kandidaten-Zeitstempel entspricht; sonst `None`.
- **3xx** → `None` (Snapshot nicht exakt abrufbar, 0d). **Keiner Umleitung folgen.**
- **404** → `None`.
- **429, 5xx, Timeout, Netzfehler** → transient: Retry nach `with_retry`; bleibt es dabei,
  `ArchiveError` (transient).
- Antwort größer als `max_bytes` → Abbruch beim Lesen, `ArchiveError` (permanent,
  `reason="too_large"`). Gelesen wird gestreamt, nie mehr als `max_bytes + 1` Bytes.

Jede HTTP-Anfrage (CDX und Abruf) geht vorher durch den `RateLimiter`.

### 4.4 Ablauf je Quelle

`attest_source` in dieser Reihenfolge:

1. WORM lesen; jeder Fehler → `worm_missing`.
2. `content_hash(raw) != pending.content_hash` → `hash_mismatch` (ERROR-Log). **Kein** Netzaufruf.
3. `sha1 = sha1_base32(raw)`; `candidates(origin_url, sha1_b32=sha1)`.
4. Keine Kandidaten → `no_matching_snapshot`. Das ist ein **Befund über die Quelle**: Das Archiv
   kennt diese Bytes unter dieser URL nicht (noch nicht, oder die Quelle lieferte anderes).
5. Für jeden der ersten `max_candidates` Kandidaten (nächster zuerst): `fetch()`.
   - `None` → nächster Kandidat.
   - `content_hash(bytes) == pending.content_hash` → Zeile schreiben (`archiver="wayback"`,
     `snapshot_url=snapshot_url(candidate)`, `snapshot_at` aus dem Zeitstempel in UTC,
     `verified_sha256` = der gerade berechnete Hash) → `attested`.
   - sonst (SHA-1 passte, SHA-256 nicht) → merken, WARNING-Log, nächster Kandidat.
6. Kein Kandidat attestiert: mindestens ein SHA-256-Fehlschlag → `bytes_mismatch`; sonst
   `snapshot_unavailable`.
7. `ArchiveError` aus `candidates` oder `fetch` → `error` (Log ohne Antworttext, R-SEC-07).
8. `IntegrityError` beim Insert (UNIQUE-Race: ein paralleler Lauf war schneller) → `rollback`,
   trotzdem `attested`. **Jede andere** DB-Ausnahme beim Insert (insbesondere die
   Trigger-Verweigerung) wird **nicht** abgefangen.

### 4.5 Netz- und Capture-Freiheit strukturell absichern

Neuer import-linter-Contract: `wortlaut.pipeline.attest` importiert weder `wortlaut.archive.spn2`
noch `wortlaut.archive.archiver` noch `wortlaut.timestamp`. `wayback_lookup.py` definiert seine
eigene Host-Konstante, statt sie aus `archiver.py` zu importieren.

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)

- **AC1 — DB erzwingt Gleichheit.** *Given* eine Quelle. *When* ein direkter Insert in
  `source_archive` mit `verified_sha256 != content_hash`. *Then* die DB verweigert ihn. Mit
  gleichem Hash gelingt er.
- **AC2 — Append-only.** UPDATE und DELETE auf `source_archive` scheitern am Trigger; ein zweiter
  Insert für dieselbe `(source_id, archiver)` scheitert an UNIQUE.
- **AC3 — Auswahl.** `list_sources_without_attestation` liefert genau die Quellen ohne
  `source_archive`-Zeile, stabil nach `created_at, id`, mit `limit`.
- **AC4 — Revisits zählen.** CDX-Antwort mit einer Zeile Status `-`/`warc/revisit` und passendem
  Digest → genau diese Zeile ist Kandidat. Zeilen mit anderem Digest, Status `404` oder `302`,
  oder anderem Pfad im `original` sind es nicht.
- **AC5 — Keine Umleitung.** Abruf mit Antwort `302` → `fetch` gibt `None`; es wird **keine**
  zweite Anfrage an die `Location` gestellt (Zähler im Mock-Transport = 1).
- **AC6 — Memento-Datetime.** 200 mit abweichendem `Memento-Datetime` → `None`.
- **AC7 — Größengrenze.** Antwort über `max_bytes` → `ArchiveError` mit `reason="too_large"`;
  gelesen wurden höchstens `max_bytes + 1` Bytes.
- **AC8 — Beweis ist SHA-256.** *Given* ein Kandidat mit passendem SHA-1, dessen Bytes einen
  anderen SHA-256 haben. *Then* keine Zeile, Status `bytes_mismatch`.
- **AC9 — Hash vor Netz.** WORM-Bytes passen nicht zu `content_hash` → `hash_mismatch`, Lookup
  wird **nicht** aufgerufen (Zähler = 0). WORM wirft → `worm_missing`, Lookup nicht aufgerufen.
- **AC10 — Reihenfolge und Obergrenze.** Kandidaten werden nach Abstand zu `retrieved_at`
  geprüft; nach `max_candidates` Abrufen ist Schluss; der erste passende wird festgehalten.
- **AC11 — Ergebnisarten.** Keine Kandidaten → `no_matching_snapshot`; alle `fetch` → `None` →
  `snapshot_unavailable`; `ArchiveError` → `error`.
- **AC12 — Ende-zu-Ende.** *Given* echtes Postgres und MinIO, eine erfasste Quelle, ein Fake-Lookup
  mit den Fixture-Bytes. *When* `attest_source`. *Then* genau eine `source_archive`-Zeile mit
  `verified_sha256 = content_hash`, und die Quelle verschwindet aus
  `list_sources_without_attestation`. Ein zweiter Lauf schreibt nichts.
- **AC13 — CLI.** `attest --dry-run` gibt `pending=<n> dry_run=True` aus, ohne Netzaufruf. Der
  echte Lauf gibt **genau eine** Ergebniszeile aus, Felder in dieser Reihenfolge:
  `pending= attested= no_matching_snapshot= snapshot_unavailable= bytes_mismatch= hash_mismatch= worm_missing= error=`.
- **AC14 — Exit-Codes.** 0 im Normalfall (auch bei `no_matching_snapshot`,
  `snapshot_unavailable`, `worm_missing`) · 2 Konfiguration · **4**, sobald
  `bytes_mismatch > 0` oder `hash_mismatch > 0` · **3** bei Circuit-Breaker
  (`consecutive_failure_limit` aufeinanderfolgende `error`) · sonst **1**, sobald `error > 0`.
- **AC15 — Kein Capture.** Der import-linter-Contract aus §4.5 ist grün. `attest` benötigt keine
  Internet-Archive-Zugangsdaten (läuft ohne `WORTLAUT_ARCHIVE_IA_*`).
- **AC16 — Bestand unverändert.** Alle bestehenden Tests laufen ohne Änderung grün.

## 6. Testplan (Test-zu-AC-Mapping)

| AC | Test | Datei | Art |
|---|---|---|---|
| AC1, AC2 | `test_insert_requires_equal_hash` · `test_source_archive_is_append_only` · `test_unique_per_archiver` | `tests/integration/test_attest.py` | Integration |
| AC3 | `test_list_sources_without_attestation` | `tests/integration/test_attest.py` | Integration |
| AC4 | `test_candidates_include_revisit_and_filter_rest` | `tests/unit/test_wayback_lookup.py` | Unit (httpx.MockTransport) |
| AC5 | `test_fetch_never_follows_redirect` | `tests/unit/test_wayback_lookup.py` | Unit |
| AC6 | `test_fetch_rejects_wrong_memento_datetime` | `tests/unit/test_wayback_lookup.py` | Unit |
| AC7 | `test_fetch_size_limit` | `tests/unit/test_wayback_lookup.py` | Unit |
| AC8 | `test_sha1_match_sha256_mismatch_is_bytes_mismatch` | `tests/unit/test_attest_pipeline.py` | Unit |
| AC9 | `test_hash_mismatch_never_calls_lookup` · `test_worm_missing` | `tests/unit/test_attest_pipeline.py` | Unit |
| AC10 | `test_candidates_ordered_and_capped` | `tests/unit/test_attest_pipeline.py` | Unit |
| AC11 | `test_outcomes_no_match_unavailable_error` | `tests/unit/test_attest_pipeline.py` | Unit |
| AC12 | `test_attest_end_to_end_and_idempotent` | `tests/integration/test_attest.py` | Integration |
| AC13, AC14 | `test_dry_run_line` · `test_summary_line_field_order` · `test_exit_codes` (parametrisiert) | `tests/unit/test_cli_attest.py` | Unit |
| AC15 | import-linter; `test_runs_without_ia_credentials` | `tests/unit/test_cli_attest.py` | Unit |
| AC16 | bestehende Tests | — | — |

## 7. Recht / Security

- **Beweiskette:** Eine Attestierung entsteht nur über SHA-256 gegen `content_hash` und wird in
  der DB erzwungen (AC1). SHA-1 dient nur als Vorfilter. Umleitungen werden nie gefolgt (AC5).
- **SSRF (R-SEC-05):** Abrufe nur über `pinned_client("web.archive.org")`; der Transport verweigert
  jeden anderen Host. `original` aus der CDX-Antwort ist Fremdinhalt und wird nur als Pfadteil
  hinter `web.archive.org/web/<ts>id_/` verwendet, nie als eigenständiges Ziel.
- **Fremdinhalt (R-SEC-07):** Antworttexte und Snapshot-Bytes werden nie geloggt.
- **Ressourcen:** Größengrenze je Abruf (AC7), gestreamtes Lesen.
- **Gast-Verhalten:** Nur lesende Anfragen, Mindestabstand über `RateLimiter`, höchstens
  `max_candidates` Abrufe je Quelle.

## 8. Risiken & offene Fragen

- **Schema-Toleranz:** Ein Snapshot von `http://…` zählt für eine Quelle `https://…` mit gleichem
  Host, Pfad und Query. Gleiche Bytes unter derselben Adresse sind dieselbe Aussage; das
  Protokoll ist Transport.
- **Neue Quellen sind anfangs `snapshot_unavailable`.** Das ist erwartet (0d) und kein Fehler; ein
  späterer Lauf holt es nach.
- **`no_matching_snapshot` für eine Bestandsquelle** wäre ein inhaltlicher Befund und muss vor
  Increment 2 geklärt werden (Abnahme, §9).

## 9. Definition of Done (Verweis)

Siehe `docs/engineering.md`. Zusätzlich **Abnahme im Betrieb** nach dem Merge: `attest --dry-run`
zeigt 9, der echte Lauf attestiert alle 9 Bestandsquellen oder weist jede Ausnahme mit Grund aus.
Jede `bytes_mismatch` oder `no_matching_snapshot` wird geklärt, bevor Increment 2 beginnt.

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu:**
- `migrations/versions/0005_source_archive.py`
- `src/wortlaut/archive/wayback_lookup.py`
- `src/wortlaut/store/attestations.py`
- `src/wortlaut/pipeline/attest.py`
- `tests/unit/test_wayback_lookup.py`
- `tests/unit/test_attest_pipeline.py`
- `tests/unit/test_cli_attest.py`
- `tests/integration/test_attest.py`

**Ändern:**
- `src/wortlaut/evidence/hashing.py` — `sha1_base32` ergänzen.
- `src/wortlaut/store/models.py` — Klasse `SourceArchive` ergänzen.
- `src/wortlaut/archive/settings.py` — zwei Felder in `ArchiveSettings` ergänzen.
- `src/wortlaut/archive/retry.py` — `with_retry` generisch machen (heute auf `str` festgelegt).
- `src/wortlaut/cli.py` — Subcommand `attest`, `_run_attest`, `_AttestStats`.
- `.importlinter` — ein neuer Contract.
- `docs/deploy.md` — Abschnitt „Erfassungs-Läufe" um `attest` ergänzen.

## 11. Umsetzungsdetails je Datei

### `migrations/versions/0005_source_archive.py` (neu)

Muster: `0004_source_timestamp.py` (rohes SQL, `revision = "0005"`, `down_revision = "0004"`).

```sql
CREATE TABLE source_archive (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       uuid NOT NULL REFERENCES source(id),
  archiver        text NOT NULL,
  snapshot_url    text NOT NULL,
  snapshot_at     timestamptz NOT NULL,
  verified_sha256 char(64) NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_source_archive_archiver UNIQUE (source_id, archiver)
);
CREATE INDEX ix_source_archive_source ON source_archive(source_id);

CREATE FUNCTION check_source_archive_hash() RETURNS trigger AS $$
BEGIN
  IF NEW.verified_sha256 IS DISTINCT FROM
     (SELECT content_hash FROM source WHERE id = NEW.source_id) THEN
    RAISE EXCEPTION 'source_archive: verified_sha256 passt nicht zu source.content_hash';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_source_archive_hash BEFORE INSERT ON source_archive
  FOR EACH ROW EXECUTE FUNCTION check_source_archive_hash();
CREATE TRIGGER trg_source_archive_immutable BEFORE UPDATE OR DELETE ON source_archive
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
```

`forbid_mutation()` existiert seit `0002` — **nicht** neu anlegen. `downgrade()` in umgekehrter
Reihenfolge: beide Trigger, die Funktion `check_source_archive_hash`, Index, Tabelle.
Modul-Docstring nach dem Muster von `0004`, mit Verweis auf ADR-0009 und Spec 0124.

### `src/wortlaut/evidence/hashing.py` (ändern)

```python
def sha1_base32(raw: bytes) -> str:
    """SHA-1 der Rohbytes, Base32 — das Format des Wayback-CDX-Digests (Spec 0124 §0b).

    NUR als Vorfilter für Snapshot-Kandidaten. Nie als Beweis verwenden: SHA-1 ist
    kollisionsschwach; bewiesen wird über content_hash (SHA-256).
    """
    return base64.b32encode(hashlib.sha1(raw, usedforsecurity=False).digest()).decode("ascii")
```

`import base64` ergänzen. `usedforsecurity=False` ist Pflicht: Es dokumentiert, dass SHA-1 hier
kein Sicherheitszweck ist, und verhindert einen Security-Hotspot im Code-Quality-Gate. Sonst nichts ändern.

### `src/wortlaut/archive/settings.py` (ändern)

In `ArchiveSettings` zwei Felder ergänzen:

```python
    attest_max_candidates: int = 3  # Snapshot-Abrufe je Quelle (Spec 0124 §4.2)
    attest_max_snapshot_bytes: int = 100 * 1024 * 1024  # Größengrenze je Abruf (§4.3)
```

### `src/wortlaut/archive/retry.py` (ändern)

`with_retry` ist heute auf `Callable[[], Awaitable[str]] -> str` festgelegt; `wayback_lookup`
braucht `bytes | None` und `list[SnapshotCandidate]`. Signatur generisch machen (PEP 695, Python 3.12):

```python
async def with_retry[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
```

**Nur** die Signatur ändern. Körper, Docstring und Verhalten bleiben unverändert; die bestehenden
Aufrufer in `archiver.py` funktionieren damit unverändert weiter.

### `src/wortlaut/archive/wayback_lookup.py` (neu)

- Imports nur aus stdlib, `httpx`, `wortlaut.archive.pinned`, `wortlaut.archive.errors`,
  `wortlaut.archive.retry`, `wortlaut.archive.throttle`. **Nicht** aus `archiver.py` oder `spn2.py`.
- `_HOST = "web.archive.org"`; `_CDX_PATH = "/cdx/search/cdx"`.
- `SnapshotCandidate` und `snapshot_url()` wie §3; `snapshot_url` liefert
  `f"https://{_HOST}/web/{c.timestamp}/{c.original}"` (ohne `id_` — das ist die Adresse für Menschen).
- `candidates()`: GET `_CDX_PATH` mit Query-Parametern `url=<origin_url>`, `output=json`,
  `fl=timestamp,original,statuscode,digest,mimetype` (über `params=`, nicht per Stringbau).
  Leere Antwort oder nur Kopfzeile → `[]`. Die erste Zeile ist die Kopfzeile. Filter nach §4.2.
  `timestamp` muss genau 14 Ziffern sein, sonst Zeile verwerfen.
  Nicht-JSON-Antwort → `ArchiveError` permanent, `reason="invalid_response"`.
- `fetch()`: Pfad `f"/web/{c.timestamp}id_/{c.original}"`; `client.stream("GET", …)`; Status
  nach §4.3; beim 200 die Bytes chunkweise lesen und bei Überschreiten von `max_bytes` mit
  `ArchiveError(service="wayback", reason="too_large", transient=False)` abbrechen.
  `Memento-Datetime` mit `email.utils.parsedate_to_datetime` parsen und mit dem Zeitstempel
  (UTC) vergleichen; fehlt der Header oder weicht er ab → `None`.
- Transiente Fehler über `with_retry` (Muster `archiver.py`), `ArchiveError` mit
  `service="wayback"`.
- Client lazy über `pinned_client(_HOST)`; `aclose()` schließt ihn.
- Vor jeder Anfrage `await self._limiter.acquire()`, falls ein Limiter gesetzt ist.

### `src/wortlaut/store/models.py` (ändern)

Klasse `SourceArchive` nach dem Muster von `SourceTimestamp`: Spalten wie in der Migration,
`verified_sha256` als `CHAR(64)`. Docstring: append-only, Gleichheit per Trigger erzwungen,
ADR-0009. Sonst nichts ändern.

### `src/wortlaut/store/attestations.py` (neu)

Muster: `store/timestamps.py`. `list_sources_without_attestation` mit
`~exists().where(SourceArchive.source_id == Source.id)`, Sortierung `created_at, id`, `limit`.
`insert_source_archive` mit `flush` + `commit`, gibt die id zurück; `IntegrityError` propagiert.

### `src/wortlaut/pipeline/attest.py` (neu)

Exakt nach §4.4. Konstante `ATTESTING_ARCHIVERS = ("wayback",)` mit Kommentar: Registry ist Code,
ein neuer Archivar kostet einen Review (ADR-0009 §3). Sortierung der Kandidaten über den absoluten
Abstand von `datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=UTC)` zu `retrieved_at`.
Imports nur aus `wortlaut.evidence`, `wortlaut.store`, `wortlaut.archive.wayback_lookup`,
`wortlaut.archive.errors`.

### `src/wortlaut/cli.py` (ändern)

Muster: `_run_timestamp` / `_run_reparse`.

- Subparser `attest` mit `--limit`, `--dry-run`, `--no-migrate`; in `main` die Liste gültiger
  Subcommands und die Fehlermeldung ergänzen.
- `_run_attest`: Settings `DbSettings`, `WormSettings`, `ArchiveSettings` in **einem** `try`,
  Fehler → `_config_error`, Exit 2. **Keine** Prüfung auf IA-Zugangsdaten. Engine, Sessionmaker,
  `MinioWormStore`; Bootstrap wie `timestamp`. Auswahl über `list_sources_without_attestation`.
  `--dry-run` → `pending=<n> dry_run=True`, Exit 0, **bevor** ein Lookup gebaut wird.
  Sonst `HttpWaybackLookup(limiter=RateLimiter(settings.wayback_min_interval_seconds),
  max_bytes=settings.attest_max_snapshot_bytes, attempts=settings.retry_attempts,
  base_delay_seconds=settings.retry_base_delay_seconds)`; je Quelle eigene Session,
  `attest_source(..., max_candidates=settings.attest_max_candidates)`, Stats buchen.
  `bytes_mismatch` und `hash_mismatch` zusätzlich mit `source_id` auf stderr.
  Circuit-Breaker: `consecutive_failure_limit` aufeinanderfolgende `error` → Summary, Exit 3.
  Am Ende genau eine Summary-Zeile, Exit nach AC14 — **ohne verschachtelten Ternary**
  (`if … return 4`, dann `if … return 1`, dann `return 0`).
  `finally`: `lookup.aclose` (falls gebaut) und `engine.dispose` über `_aclose_all`.
- `_AttestStats` als `@dataclass` mit einem Zähler je Status plus `consecutive_error`,
  `record(outcome)`, `summary_line(pending)` nach AC13.

### `.importlinter` (ändern)

Am Ende anfügen:

```
# Spec 0124 §4.5: attest prueft nur. Kein Capture, kein Zeitstempeldienst — auch nicht indirekt.
[importlinter:contract:attest-ohne-capture]
name = Attest importiert weder Capture- noch Zeitstempel-Code
type = forbidden
source_modules =
    wortlaut.pipeline.attest
forbidden_modules =
    wortlaut.archive.spn2
    wortlaut.archive.archiver
    wortlaut.timestamp
```

### `docs/deploy.md` (ändern)

Im Abschnitt „Erfassungs-Läufe" nach dem `reparse`-Absatz: `attest` prüft, ob der Internet
Archive für jede Quelle einen Snapshot derselben URL mit denselben Bytes hat, und hält das fest.
Liest nur, löst keine Captures aus, braucht keine Zugangsdaten. Neue Quellen sind anfangs oft
`snapshot_unavailable` — ein späterer Lauf holt das nach. Exit 4 heißt: Bytes weichen ab, Quelle
prüfen. Dazu ein Codeblock im Stil der anderen mit `attest --dry-run`.

### Tests

- **Unit, Lookup:** `httpx.MockTransport` hinter einem `httpx.AsyncClient`; den Client über einen
  Test-Seam injizieren (z. B. optionaler Konstruktor-Parameter `client: httpx.AsyncClient | None`,
  Default `None` → `pinned_client`). Für AC5 zählt der Handler die Aufrufe.
- **Unit, Pipeline:** Fakes für `WormStore`, Lookup und Session nach dem Vorbild von
  `tests/unit/test_reparse_pipeline.py`; `insert_source_archive` per `patch` mit `return_value=`
  bzw. `side_effect=`.
- **Integration:** Fixtures `fresh_pg_dsn`/`worm_store` aus `tests/integration/conftest.py`;
  Quelle anlegen wie in `tests/integration/test_reparse.py`. AC1/AC2 per rohem SQL über
  `session.execute(text(...))`. Fake-Lookup liefert die Fixture-Bytes.
- **Sonar-Muster:** genau **ein** Aufruf je `pytest.raises`-Block (Hilfsaufrufe wie `text(...)`
  vorher in Variablen); kein Lambda in `patch()`; kein Modul-/Klassenzustand für Testdaten; keine
  ausgeschriebenen DSNs mit Zugangsdaten; `async def` nur, wo awaited wird.

## 12. Do-NOT (hart)

- **Keine** Umleitung folgen, nirgends (`follow_redirects` bleibt `False`).
- **Kein** Save-Page-Now-Aufruf, kein Import von `archive.spn2` oder `archive.archiver` in den
  neuen Modulen.
- **Kein** UPDATE/DELETE auf irgendeiner Tabelle.
- **Keine** Änderung an `ingest`, `reparse`, `timestamp`, `serving/`, `pipeline/verify.py`,
  bestehenden Migrationen oder bestehenden Tests.
- **Kein** SHA-1 als Beweis: geschrieben wird nur nach SHA-256-Gleichheit.
- **Keine** Antworttexte oder Bytes von Fremddiensten im Log.
- **Keine** neuen Abhängigkeiten in `pyproject.toml`.
- **Keine** Dateien außerhalb von §10.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate (ruff · mypy · import-linter · pytest) fährt der Reviewer selbst. Falls du doch lokal
testen willst: Marker-Ausdruck `-m "not integration and not live"` — ein bloßes
`-m "not integration"` **ersetzt** den `-m "not live"`-Ausdruck aus `addopts`, statt ihn zu ergänzen.
