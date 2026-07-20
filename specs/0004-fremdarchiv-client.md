# Increment-Spec: Fremdarchiv-Client (Wayback + archive.today) mit SSRF-Schutz (#4)

- **Story/Issue:** #4 · **Status:** Draft · **Phase/Layer:** phase/0 · `archive` (neues Paket)
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **nichts** (unabhängiger Baustein) · Layer-Entscheidung: neues Paket `wortlaut.archive`
  (SSRF-behaftete I/O gehört **nicht** ins reine `evidence`).

## 1. Ziel
Jede Quelle vor Verarbeitung **redundant fremdarchivieren** (Wayback **und**
archive.today), damit der Beleg eine Löschung überlebt (Threat T1) — mit hartem
**SSRF-Schutz** (Egress-Allowlist + interne-IP-Blocklist), weil der Client mit von
außen eingeschleusten URLs arbeitet (Security §3.2, R-SEC-05).

## 2. Nicht-Ziele (Scope-Grenze)
- **Keine** WARC-Erstellung, **kein** WORM-Store (#5), **kein** `source`-Insert (#7).
- **Keine** Pipeline-Orchestrierung / Entscheidung „insert ja/nein" — der Client meldet nur
  Ergebnis/Teil-Fehlschlag; die „≥1 Archiv nötig"-Regel zieht #7.
- **Kein** Hashing (#3), kein Fetch der Rohbytes (das ist Adapter-`fetch`, #6).
- **Kein** Retry-Framework / keine Queue — schlichte Best-Effort-Calls mit Timeout.

## 3. Betroffene Interfaces / Öffentliche Signaturen
```python
# src/wortlaut/archive/ssrf.py   (rein bis auf DNS-Auflösung; keine wortlaut-Imports)
class SsrfBlocked(Exception): ...

def assert_url_allowed(url: str, *, allow_hosts: frozenset[str] | None = None) -> None:
    """Wirft SsrfBlocked, wenn url auf private/loopback/link-local/ULA/Metadata-IP
    (169.254.169.254) auflöst, ein Nicht-http(s)-Schema hat, oder (falls allow_hosts
    gesetzt) der Host nicht in der Allowlist ist. DNS wird aufgelöst und geprüft."""

# src/wortlaut/archive/archiver.py
@dataclass(frozen=True)
class ArchiveResult:
    wayback_url: str | None
    archive_today_url: str | None
    errors: dict[str, str]        # {'wayback': '...', 'archive_today': '...'} bei Teil-/Total-Fehlschlag

class Archiver(Protocol):
    async def archive(self, origin_url: str) -> str: ...   # Snapshot-URL oder Exception

class WaybackArchiver:        # 'Save Page Now' → web.archive.org
    async def archive(self, origin_url: str) -> str: ...
class ArchiveTodayArchiver:   # POST /submit → archive.ph
    async def archive(self, origin_url: str) -> str: ...

async def archive_all(origin_url: str, *, wayback: Archiver, archive_today: Archiver) -> ArchiveResult:
    """SSRF-Check auf origin_url, dann beide Dienste anstoßen; Teil-Fehlschlag toleriert
    (Redundanz) und in .errors protokolliert."""
```
- **Layering (R-ARCH-02):** `wortlaut.archive` ist ein eigener Infrastruktur-Layer, importiert
  keinen anderen wortlaut-Layer; wird von `wortlaut.pipeline` (#7) konsumiert, nie umgekehrt.
- **Egress-Allowlist** der Archiv-Endpunkte: `{web.archive.org, archive.ph}` (feste Hosts).

## 4. Design (kurz)
- **SSRF zuerst (R-SEC-05, Security §3.2):** `assert_url_allowed(origin_url)` **vor** jeder
  Submission — DNS auflösen, gegen private/loopback/link-local/ULA + `169.254.169.254`
  (Cloud-Metadata) blocken; Nicht-http(s)-Schemata ablehnen. Die Archiv-Hosts selbst laufen
  über die Egress-Allowlist; ein Snapshot-Redirect auf einen fremden Host wird **nicht** als
  gültiger Archivlink akzeptiert.
- **Wayback (Save Page Now):** Request an `https://web.archive.org/save/<origin_url>`;
  Snapshot-URL aus `Content-Location`/Redirect ableiten.
- **archive.today:** POST `https://archive.ph/submit/` mit `url=<origin_url>`; Snapshot-URL aus
  Antwort/Redirect. Dienst ist **bot-hostil** (Rate-Limit/Captcha) → Timeout + einmaliger
  Backoff; echte Calls nur im `live`-Test.
- **Teil-Fehlschlag toleriert (Redundanz, T1):** fällt ein Dienst aus, bricht `archive_all`
  **nicht** ab — es füllt `.errors[service]` und liefert die verbliebene(n) URL(s). `chk_archive`
  (#2) braucht ≥1; die „insert ja/nein"-Entscheidung trifft #7.
- **Keine Live-Netz-Calls in Unit-Tests (R-TEST-03):** httpx gemockt; echte Calls nur unter
  `@pytest.mark.live` (in CI **deselektiert** via `-m "not live"`).

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)
- [ ] **AC1** *Given* origin_url + gemockte Wayback-Antwort (Snapshot-Location `S`), *When*
      `archive_all`, *Then* `result.wayback_url == S`. `[unit]`
- [ ] **AC2** *Given* gemockte archive.today-Antwort (Snapshot `T`), *When* `archive_all`,
      *Then* `result.archive_today_url == T`. `[unit]`
- [ ] **AC3** *Given* eine Ziel-URL, die auf interne IP auflöst (`127.0.0.1`, `10.0.0.1`,
      `http://169.254.169.254/…`, `http://localhost`), *When* `archive_all`/`assert_url_allowed`,
      *Then* `SsrfBlocked` **und** es wird **kein** HTTP-Call abgesetzt (Mock: 0 Requests). `[unit]`
- [ ] **AC4** *Given* eine öffentliche URL, *When* `assert_url_allowed`, *Then* kein Fehler
      (Positivfall, keine Übersperrung). `[unit]`
- [ ] **AC5** *Given* Wayback wirft (5xx/Timeout), archive.today ok, *When* `archive_all`, *Then*
      `archive_today_url` gesetzt, `wayback_url is None`, `'wayback' in result.errors`, **kein**
      Gesamt-Abbruch. `[unit]`
- [ ] **AC6** *Given* **beide** Dienste werfen, *When* `archive_all`, *Then* beide URLs `None`,
      beide Keys in `result.errors` (der Aufrufer #7 entscheidet: kein source-insert). `[unit]`
- [ ] **AC7** *Given* eine Archiv-Antwort mit Redirect auf einen **nicht** allowlisted Host, *When*
      Snapshot-URL abgeleitet, *Then* wird sie verworfen/als Fehler behandelt (Egress-Allowlist). `[unit]`
- [ ] **AC8** *Given* eine echte öffentliche URL, *When* `archive_all` **live**, *Then* ≥1 realer
      Snapshot-Link (beide Dienste angesprochen). `[live]` — Marker `live`, **aus CI deselektiert**.
> Jedes AC ist von einem automatisierten Test mit Ja/Nein beantwortbar.

## 6. Testplan (Test-zu-AC-Mapping)
- **Unit (rein, httpx gemockt):** `tests/unit/test_archiver.py`
  - `test_wayback_snapshot_url` → AC1 · `test_archive_today_snapshot_url` → AC2
  - `test_partial_failure_tolerated` → AC5 · `test_total_failure_reported` → AC6
  - `test_snapshot_redirect_offhost_rejected` → AC7
- **Unit SSRF:** `tests/unit/test_ssrf.py`
  - `test_internal_ip_blocked` (parametrisiert: loopback/RFC1918/link-local/metadata/localhost/`file:`) → AC3
  - `test_public_url_allowed` → AC4
- **Live (nicht-CI, Marker `live`):** `tests/live/test_archive_live.py`
  - `test_archive_all_live_real_snapshot` → AC8 (`pytest -m live`, lokal/manuell).
- Marker `live` in `pyproject.toml` registrieren; CI-Job läuft mit `-m "not live"`.

## 7. Recht / Security
- **SSRF-Schutz Pflicht (R-SEC-05, Security §3.2):** Egress-Allowlist + interne-IP-Blocklist,
  Prüfung **vor** jedem Fetch/Submit — der Fetcher ruft per Definition eingeschleuste URLs auf.
- **Fremdarchiv = Beweis-Redundanz (Threat T1, Legal §1/§10 public_evidence):** ≥1 Archiv ist
  strukturell (chk_archive, #2) und prozessual (#7) Pflicht.
- **Keine Live-Calls in Unit-Tests (R-TEST-03):** deterministisch, mock-basiert; `live`-Marker separat.

## 8. Risiken & offene Fragen / Entscheidungen
- **Entscheidung (Q3, Stakeholder):** **beide Dienste voll live** implementiert; echte Save-Calls
  in separatem `live`-Test (aus CI deselektiert) — nicht nur Fake.
- **archive.today ist bot-hostil** (Captcha/Rate-Limit) → Live-Test potenziell flaky, daher nicht
  im CI-Gate; Timeout + einmaliger Backoff, kein aggressives Retry.
- **DNS-Rebinding:** zwischen Auflösung und Connect kann sich die IP ändern → auf der aufgelösten,
  geprüften IP connecten (pinnen), nicht erneut per Hostname. Als Härtung notiert.
- **Snapshot-URL-Parsing brüchig** (Wayback `Content-Location` vs. Redirect-Header;
  archive.today HTML) → defensiv parsen, bei Unklarheit als Fehler werten (kein falscher Link).

## 9. Definition of Done (Verweis)
[../docs/rules.md](../docs/rules.md) DoD: alle AC grün (Unit; `live` separat/manuell), alle Gates
grün (Lint·Type·Test·Coverage ≥80, Security, Architektur, SonarCloud), Review (Architekt +
**Security**, R-SEC-05), keine Live-Calls im CI-Gate, kein Secret. PR referenziert **#4**.
