# Increment-Spec: Pre-Flight-Archiv-Health-Check vor Backfill (#77)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #77 · **Status:** Reviewed · **Phase/Layer:** phase/1-mvp · `archive` · CLI
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#4** (Fremdarchiv-Client) und **#73** (Status-Gate, Circuit-Breaker, `ArchiveError`).

## 0. Ausgangslage

Im Backfill-Vorfall (#73) zog der Lauf **157 PDFs** (~0,5–0,9 MB je Stück) von
`dserver.bundestag.de`, nur um sie im Internet-Archive-Ausfall wieder wegzuwerfen. Der
Circuit-Breaker aus #73 stoppt den Lauf **nach** N Fehlern; dieser Increment stoppt ihn **davor**.

### 0a. Gemessene Vorklärung: WELCHER Endpunkt geprobt wird, entscheidet alles

Die naheliegenden Proben sind **beide falsch** — in entgegengesetzte Richtungen:

| Probe-Kandidat | Während des #73-Ausfalls | Heute (2026-08-17, IA erholt) | Taugt? |
|---|---|---|---|
| `GET web.archive.org/` (Site-Root) | **200** (obwohl `/save/` tot war) | 200 | ❌ **falsch grün** — hätte den Vorfall durchgewinkt |
| `GET archive.org/wayback/available` | 502 | **502** (immer noch!) | ❌ **falsch rot** — würde heute jeden Ingest blockieren, obwohl `/save/` funktioniert |
| `GET web.archive.org/save/<neutral>` | **404 / 503** | **302 + `Location`-Snapshot** | ✅ **einzig korrekt** |

> **Der Probe muss exakt den Endpunkt treffen, von dem der Lauf abhängt** — `/save/`. Ein
> Health-Check, der etwas anderes misst als das, was gleich benutzt wird, ist kein Health-Check,
> sondern eine zweite Fehlerquelle.

Daraus folgt die zentrale Design-Entscheidung: der Probe ist **kein neuer HTTP-Pfad**, sondern ein
ganz normaler `wayback.archive(<neutrale URL>)`-Aufruf über den **bereits injizierten** Archiver.
Er erbt damit Status-Gate, Snapshot-Validierung, Drosselung und Retry aus #73 — und misst
nachweislich dasselbe, was der Lauf gleich tun wird.

## 1. Ziel

Ein Backfill, dessen Fremdarchivierung ohnehin scheitern würde, **startet gar nicht erst** — er
bricht mit klarer Meldung und Exit-Code ab, **bevor** ein einziges Ziel-PDF gezogen wird.

## 2. Nicht-Ziele (Scope-Grenze)

- **Kein** Pre-Flight gegen `archive.today` — der Dienst ist seit #73 (Q2) ausdrücklich **optional**;
  sein Ausfall darf einen Lauf nicht verhindern. `DisableAfterFailures` deckt ihn ab.
- **Kein** Pre-Flight gegen die TSA aus #76. Begründung: der Zeitstempel läuft seit Spec 0076 in
  einem **eigenen Pass**, nicht im Ingest — ein TSA-Ausfall kann den Ingest gar nicht beschädigen,
  und ungestempelte Quellen holt der nächste `timestamp`-Lauf nach. Ein Gate dort wäre reine Reibung.
- **Keine** Änderung am Circuit-Breaker, an der Pipeline-Reihenfolge oder an `archive_all`.
- **Keine** Wiederholung des Probes im laufenden Betrieb (kein periodisches Re-Check) — dafür ist
  der Circuit-Breaker da.
- **Kein** Caching des Probe-Ergebnisses über Läufe hinweg.

## 3. Betroffene Interfaces / Öffentliche Signaturen

```python
# ── NEU: src/wortlaut/archive/preflight.py (stdlib + archive.errors/archiver) ──
PROBE_URL = "https://example.com/"     # IANA-reservierte Beispiel-Domain, keine echte Quelle

async def probe_archive(wayback: Archiver, *, probe_url: str = PROBE_URL) -> str:
    """Ein einziger Archivierungs-Versuch gegen eine neutrale URL.

    Liefert die Snapshot-URL, wenn der Dienst funktionsfähig ist. Wirft den
    ArchiveError des Archivers unverändert weiter, wenn nicht — Grund und
    Statuscode bleiben damit bis in die Abbruchmeldung erhalten.
    """

# ── GEÄNDERT: src/wortlaut/archive/settings.py ──────────────────────────
class ArchiveSettings(BaseSettings):
    ...                                       # bestehende Felder unverändert
    preflight_enabled: bool = True            # ENV WORTLAUT_ARCHIVE_PREFLIGHT_ENABLED
    preflight_url: str = PROBE_URL            # ENV WORTLAUT_ARCHIVE_PREFLIGHT_URL

# ── GEÄNDERT: src/wortlaut/cli.py ───────────────────────────────────────
# ingest-Subparser: neues Flag --no-preflight (action="store_true")
```

- **Layering (R-ARCH-02):** `preflight.py` liegt **in** `wortlaut.archive` und importiert keinen
  anderen wortlaut-Layer — der Contract `archive-ist-unabhaengig` bleibt erfüllt.

## 4. Design (kurz)

**4.1 Der Probe ist ein echter Archivierungs-Versuch** (§0a). Kein eigener HTTP-Call, kein eigenes
Status-Gate, keine zweite Wahrheit. Gelingt er, ist bewiesen, dass `/save/` gerade Snapshots
liefert; scheitert er, trägt der `ArchiveError` Dienst, Grund und Statuscode bis in die Meldung.

**4.2 Platzierung: so früh wie möglich.** Der Probe läuft **nach** dem Bootstrap (Migration,
Bucket) und **vor** `adapter.discover(...)`. Damit wird bei totem Archiv **kein** DIP-Call und
**kein** Ziel-PDF geladen (AC1) — genau die 157 verschwendeten Downloads aus #73.

**4.3 Exit-Code 3, wie der Circuit-Breaker.** Für den Betreiber bedeuten beide dasselbe:
„Fremdarchiv nicht verfügbar, Lauf sinnlos, später erneut versuchen". Ein eigener Code würde eine
Unterscheidung suggerieren, die operativ keine ist. Die **Meldung** unterscheidet sie klar
(`Pre-Flight` vs. `Circuit-Breaker`). Abgegrenzt bleibt 2 = Konfiguration.

**4.4 Neutralität der Probe-URL (AC3).** `https://example.com/` ist die von der IANA für genau
diesen Zweck reservierte Domain — kein Protokoll, keine unserer Quellen, kein Bundestags-Server.
Der Probe archiviert damit nie eine echte Quelle. Die URL ist per ENV überschreibbar, damit ein
Betreiber sie wechseln kann, ohne den Code anzufassen.

**4.5 Opt-out an zwei Stellen.** `--no-preflight` (für gezielte Einzelläufe) und
`preflight_enabled=false` per ENV (für Umgebungen ohne Egress). Beides überspringt den Probe
vollständig — es wird **kein** Call abgesetzt und der Lauf verhält sich exakt wie heute.

**4.6 `--dry-run` überspringt den Probe ebenfalls.** Ein Dry-Run archiviert nichts und lädt nichts;
ihn an einem Fremddienst scheitern zu lassen, wäre sinnlos. (Reihenfolge: `--dry-run` wird **nach**
`discover` ausgewertet — der Probe steht **davor**, also braucht es eine explizite Bedingung.)

**4.7 Kein Secret, kein Live-Call im Unit-Test.** Der Probe-Archiver wird im Test als Fake
injiziert; es gibt keine Credentials, die URL enthält keine Query-Parameter (R-SEC-01).

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)

- [ ] **AC1** *(fail-fast, Kern)* — *Given* ein Wayback-Archiver, der beim Probe einen
      `ArchiveError` wirft, *When* `main(["ingest", "--since", …])`, *Then* ist der Exit-Code **3**,
      `adapter.discover` wurde **0×** aufgerufen, `adapter.fetch` **0×**, und die Ausgabe nennt
      `Pre-Flight` samt Grund und Statuscode. `[unit]`
- [ ] **AC2** *(gesund ⇒ normaler Lauf)* — *Given* ein Probe, der eine Snapshot-URL liefert, *When*
      derselbe Aufruf, *Then* läuft der Ingest normal weiter (Exit **0**, Summary wie bisher) und
      der Archiver wurde **1× für den Probe plus 1× je Quelle** aufgerufen. `[unit]`
- [ ] **AC3** *(neutrale Probe)* — *Given* ein aufzeichnender Archiver, *When* der Probe läuft,
      *Then* wurde `archive` **genau 1×** aufgerufen, mit **exakt** `settings.preflight_url`, und
      dieser Wert ist **keine** der zu ingestierenden `origin_url`s. `[unit]`
- [ ] **AC4** *(Opt-out)* — *Given* `--no-preflight` bzw. `preflight_enabled=False`, *When* der
      Lauf startet, *Then* wurde **kein** Probe-Call abgesetzt und der Lauf verhält sich unverändert
      (Exit 0). `[unit]`
- [ ] **AC5** *(Dry-Run)* — *Given* `--dry-run`, *When* der Lauf startet, *Then* wurde **kein**
      Probe-Call abgesetzt und die Dry-Run-Zeile ist unverändert. `[unit]`
- [ ] **AC6** *(kein Regress)* — *Given* die bestehende Test-Suite, *When* CI, *Then* bleiben alle
      #73-Tests (Circuit-Breaker, Summary, `reasons=`) unverändert grün. `[ci]`
- [ ] **AC7** — CI vollständig grün + **0 neue Sonar-Issues** im PR. `[ci]`

## 6. Testplan (Test-zu-AC-Mapping)

**Unit (rein, Archiver + Adapter als Fakes, keine Netz-Calls — R-TEST-03):**
- `tests/unit/test_preflight.py` — `test_probe_returns_snapshot_url` → AC3 ·
  `test_probe_propagates_archive_error` → AC1 (Einheit)
- `tests/unit/test_cli.py` *(erweitert)* — `test_preflight_failure_aborts_before_discover` → **AC1** ·
  `test_preflight_healthy_runs_normally` → AC2 · `test_no_preflight_flag_skips_probe` → AC4 ·
  `test_dry_run_skips_probe` → AC5

**Integration:** keine neue — der Increment berührt weder DB noch WORM.

**Invarianten (R-DATA):** unverändert; kein neuer Schreibpfad.

## 7. Recht / Security

- **R-CORE-02 unberührt:** Der Probe ändert nichts an „Provenienz zuerst" — er verhindert nur
  Arbeit, die ohnehin nichts persistieren würde.
- **R-SEC-05 (SSRF):** Die Probe-URL geht durch dieselbe `assert_url_allowed`-Kette wie jede andere
  Archivierung (sie läuft über `Archiver.archive`). Default ist eine konstante, öffentliche Domain.
- **R-SEC-01:** keine Credentials, keine Query-Parameter, keine Secrets im Log.
- **Fremdarchiv-Höflichkeit:** genau **ein** zusätzlicher `/save/`-Call pro Lauf gegen eine
  Domain, die bereits zehntausendfach archiviert ist — vernachlässigbare Last, und billiger als die
  157 Fehlversuche, die er verhindert.

## 8. Risiken & offene Fragen

- **🟠 Falsch-rot bei zielspezifischem Fehler:** Der Probe misst `example.com`. Wäre Wayback
  gesund, würde aber ausgerechnet `dserver.bundestag.de` ablehnen, ginge der Lauf trotzdem los —
  und der Circuit-Breaker aus #73 fängt ihn. Die beiden Mechanismen ergänzen sich genau hier;
  keiner ersetzt den anderen.
- **🟠 Falsch-grün bei Flapping:** Der Probe ist eine Momentaufnahme. Fällt der Dienst eine Minute
  später aus, greift wieder der Circuit-Breaker. Bewusst akzeptiert.
- **🟡 Ein zusätzlicher Snapshot von `example.com`** je Lauf. Vernachlässigbar (§7).
- **🟡 Der Probe kostet bei transientem Fehler die Retry-Zeit des Archivers** (bis
  `retry_attempts` × Backoff). Gewollt: ein einzelner Blip soll keinen Lauf blockieren.

## 9. Definition of Done (Verweis)

[../docs/rules.md](../docs/rules.md) DoD: alle AC grün, alle Gates grün, Review durch Architekt,
Invarianten gewahrt, kein Secret, keine Live-Calls im CI-Gate. PR referenziert **#77**
(`Closes #77`) gegen `develop`.

---

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu anlegen:**
- `src/wortlaut/archive/preflight.py`   — `PROBE_URL`, `probe_archive`
- `tests/unit/test_preflight.py`        — AC3 + Fehler-Propagation

**Ändern (chirurgisch):**
- `src/wortlaut/archive/settings.py`    — zwei Felder **anhängen**
- `src/wortlaut/cli.py`                 — `--no-preflight`, Probe vor `discover`
- `tests/unit/test_cli.py`              — AC1, AC2, AC4, AC5

> **NICHT anfassen:** `src/wortlaut/archive/archiver.py`, `retry.py`, `throttle.py`, `ssrf.py`,
> `pinned.py`, `src/wortlaut/pipeline/**`, `src/wortlaut/store/**`, `src/wortlaut/timestamp/**`,
> `migrations/`, `pyproject.toml`, alle übrigen Tests.

## 11. Umsetzungsdetails je Datei

### `src/wortlaut/archive/preflight.py` (neu)
`PROBE_URL = "https://example.com/"`. `probe_archive(wayback, *, probe_url=PROBE_URL)` ruft
`await wayback.archive(probe_url)` auf und gibt das Ergebnis zurück. **Kein** `try`/`except`: ein
`ArchiveError` soll unverändert nach oben — Grund und Statuscode sind die Abbruchmeldung. Kein
eigener HTTP-Client, kein eigenes Status-Gate (§4.1). Docstring nennt, **warum** `/save/` und nicht
der Site-Root geprobt wird (§0a) — dieser Kommentar ist der Wert der Datei.

### `src/wortlaut/archive/settings.py` (ändern)
Die zwei Felder aus §3 **ans Ende** anhängen; bestehende Felder und Defaults unverändert.
`preflight_url` als `str` mit `PROBE_URL` als Default — Import aus `preflight.py`.
⚠️ Achte auf die Import-Richtung: `settings.py` importiert `preflight.py` (nicht umgekehrt), sonst
entsteht ein Zyklus (R-ARCH-05).

### `src/wortlaut/cli.py` (ändern)
- `p_ingest.add_argument("--no-preflight", action="store_true")`.
- In `_run`, **nach** `await worm.ensure_bucket()` / dem `ensure_ingest_adapter`-Block und
  **vor** `refs = list(await adapter.discover(args.since))`:
  ```
  ueberspringen = args.no_preflight or args.dry_run or not archive_settings.preflight_enabled
  ```
  Ist es nicht übersprungen: `await probe_archive(wayback, probe_url=archive_settings.preflight_url)`
  in einem `try`. Bei `ArchiveError as e`: Zeile nach **stderr**
  `f"Pre-Flight: Fremdarchiv nicht funktionsfaehig ({e}) — Abbruch vor dem ersten Ziel-Fetch"`
  und `return 3`. **Kein** anderer Exception-Typ wird gefangen (ein `SsrfBlocked` ist ein
  Security-Stopp und fliegt durch, wie in #73 festgelegt).
- Sonst nichts verändern — insbesondere Summary, Breaker und Dry-Run-Zeile bleiben **wörtlich**
  wie sie sind.

### Tests
- `test_cli.py` benutzt bereits Fakes für Adapter und Archiver — an dieselben anknüpfen, **keine**
  neue Test-Infrastruktur bauen. Für AC1 zählt der Fake-Adapter seine `discover`-Aufrufe und der
  Test prüft `== 0`.
- Keine Netz-Calls, keine echten Wartezeiten.

## 12. Do-NOT (hart)
- KEINE git-, docker-, uv-, npm-, alembic- oder pytest-Befehle ausführen — nur das in §13.
- KEINE anderen als die in §10 genannten Dateien anlegen oder ändern.
- KEIN eigener HTTP-Call, KEIN eigener httpx-Client, KEIN eigenes Status-Gate im Preflight —
  ausschließlich `wayback.archive(...)` (§4.1).
- KEINE Probe gegen `archive.today`, gegen `archive.org/wayback/available` oder gegen den
  Site-Root von `web.archive.org` (§0a — alle drei messen das Falsche).
- KEINE echte Quelle als Probe-URL. KEIN Ziel-PDF-Fetch vor dem Probe.
- KEIN Fangen von `SsrfBlocked`. KEINE Änderung an Circuit-Breaker, Summary-Zeile oder Dry-Run-Zeile.
- KEINE Netz-Calls in Tests. KEINE erfundenen Feld-/Parameternamen — bestehende Dateien vorher lesen.

## 13. Abschluss (und NUR das an Kommandos ausführen)
- `git status --porcelain` ausgeben.
