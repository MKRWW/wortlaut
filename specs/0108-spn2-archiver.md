# Increment-Spec: Fremdarchivierung auf die SPN2-API (#108)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #108 · **Status:** Reviewed · **Phase/Layer:** phase/1-mvp · `archive`
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#73** (Status-Gate, Drosselung, Retry), **#77** (Pre-Flight), **#36** (IP-Pinning).
- Entblockt den Backfill: ohne funktionierende Fremdarchivierung wächst der Korpus nicht.

## 0. Ausgangslage

`WaybackArchiver` ruft `GET https://web.archive.org/save/<url>` — den **Browser-Pfad** — und
liest die Snapshot-URL aus `Content-Location` bzw. `Location`. Ein `Authorization`-Header wird
nirgends gesetzt; `ArchiveSettings` kennt kein Schlüsselfeld.

Dieser Pfad ist tot. Gemessen am 2026-08-27 vom Betriebsserver:

| Aufruf | Ergebnis |
|---|---|
| `GET /save/<url>` ohne Auth | 429 |
| `GET /save/<url>` **mit** `Authorization: LOW` | **429** |
| `GET /save/<url>` mit Auth + `X-Accept-Reduced-Priority` | **429** |
| `POST /save` **ohne** Auth | **401** `You need to be logged in to use Save Page Now.` |
| `POST /save` **mit** Auth | **200**, JSON |

Anonymes Fremdarchivieren beim Internet Archive gibt es nicht mehr, und ein zusätzlicher Header
allein hätte nichts geändert: Der Browser-Pfad bleibt auch mit gültigen Schlüsseln bei 429.
Endpunkt, Antwortformat und Fehlerauswertung ändern sich gemeinsam.

### 0a. Vorklärung: SPN2 ist asynchron — gemessen, nicht angenommen

Quellen: die offizielle SPN2 Public API Doku (Stand 2026-07-22) **und** eine eigene Messung mit
echten S3-Schlüsseln. Der gemessene Ablauf:

```
POST https://web.archive.org/save
     Header: Authorization: LOW <access>:<secret> · Accept: application/json
     Body  : url=<origin_url>          (application/x-www-form-urlencoded)
  -> 200 {"url":"https://dserver.bundestag.de/btp/21/21089.pdf",
          "job_id":"spn2-dea4b01e0839fb6ccf689dc93f5b2f5a4a4f8335"}

GET  https://web.archive.org/save/status/<job_id>
  -> t= 9s {"status":"pending", ...}
  -> t=33s {"status":"success","timestamp":"20260827142259",
            "original_url":"https://dserver.bundestag.de/btp/21/21089.pdf",
            "duration_sec":16.04,"http_status":200,"counters":{...}}

Snapshot-URL = https://web.archive.org/web/<timestamp>/<original_url>
```

**Es gibt keinen synchronen Weg zu einer `wayback_url`.** Der Capture-Request liefert
ausschließlich eine Auftragsnummer. Das Warten wird deshalb **innerhalb** des Archivers
gekapselt — die Signatur `archive(origin_url) -> str` bleibt unverändert, und damit bleiben
`archive_all`, `pipeline/ingest.py` und der Pflicht-Anker unangetastet.

Vier gemessene Eigenheiten, die die Doku so **nicht** hergibt. Sie sind der Grund, warum diese
Spec an einigen Stellen ausdrücklich etwas verbietet:

**(1) `job_id` ist keine UUID, sondern `spn2-<40 hex>` — und deterministisch pro URL.**
Die Doku zeigt durchgehend UUID-Beispiele. Ein zweiter POST auf dieselbe URL innerhalb einer
Stunde liefert **dieselbe** `job_id` plus ein zusätzliches Feld:

```
{"url":"…","job_id":"spn2-dea4b01e…","message":"The same snapshot had been made 2 minutes ago.
 You can make new capture of this URL after 1 hour."}
```

Für den Client ist das kein Sonderfall — der Status-Abruf funktioniert normal weiter. Eine
Formatprüfung auf UUID wäre dagegen sofort rot. **Deshalb: keine Formatprüfung auf `job_id`.**

**(2) Fehler kommen mit HTTP 200 — und zwar schon beim Capture-Request.**

```
POST /save url=https://example.com/
  -> 200 {"status":"error","status_ext":"error:too-many-daily-captures",
          "message":"This URL has been already captured 5 times today, …"}
```

Kein `job_id` im Body. Ein reiner Statuscode-Check übersieht das. **Die Fehlerauswertung wird an
beiden Stellen gebraucht: Capture-Request und Status-Abruf.**

**(3) Ein frischer Snapshot ist nicht sofort abrufbar.** Direkt nach dem erfolgreichen Capture:

```
/web/20260827142259/…  -> 302 -> /web/20260820132248/…   (älterer Snapshot!)
/web/20260820132248/…  -> 200                            (exakt)
/web/20260805173013/…  -> 200                            (exakt)
CDX-Index für 20260827 : leer
```

Der Anker wird erst **nachträglich** exakt. **Deshalb darf keine Snapshot-Auflösung als Gate in
den Ingest-Pfad** — sie wäre für jeden frischen Capture falsch rot. Die Lücke deckt der
RFC-3161-Zeitstempel aus #76 ab: die unabhängige, zeitgleiche Drittbezeugung des Hashes hängt
nicht am Index-Lauf des Archivs.

**(4) `https://example.com/` ist als Pre-Flight-Probe unbrauchbar.** Das Limit sind 5 Captures
pro URL und Tag, global über alle Nutzer; bei der meistgenutzten Test-URL der Welt ist es
dauerhaft ausgeschöpft (Messung siehe (2)). Der Pre-Flight aus #77 wäre ab der Umstellung
**permanent rot** — genau der falsch-rote Health-Check, den #77 vermeiden wollte.

### 0b. Vorklärung: warum der Pre-Flight jetzt etwas anderes misst

#77 hat bewusst festgelegt: Ein Health-Check, der etwas anderes misst als das, was gleich benutzt
wird, ist keine Prüfung, sondern eine zweite Fehlerquelle. Diese Begründung bleibt richtig — ihre
Anwendung ändert sich, weil sich der Fehlermodus geändert hat.

Die Capture-Probe hat seit SPN2 einen systematischen Falsch-Rot-Modus, der **nichts mit dem
Dienst zu tun hat**, sondern mit der Probe-URL: das Tageslimit dieser einen URL. Eine Probe, die
rot wird, obwohl der Dienst gesund ist, ist wertlos — sie hätte den Backfill dauerhaft blockiert.

`GET /save/status/user` belegt in einem einzigen Call genau das, was am Laufbeginn systematisch
kaputt sein kann: **die Zugangsdaten werden akzeptiert** und **der Dienst antwortet**. Gemessen:

```
GET https://web.archive.org/save/status/user?_t=<zahl>   (Auth erforderlich)
  -> 200 {"processing":0,"available":3,"daily_captures":0,"daily_captures_limit":30000}
```

Er verbraucht kein Capture-Kontingent und antwortet sofort statt nach 33 s.

Was er **nicht** belegt: dass ein konkreter Capture gelingt. Das ist der bewusst gezahlte Preis.
Ein fehlgeschlagener Einzel-Capture ist ohnehin kein Lauf-Abbruch, sondern ein
`archive_failed`-Outcome pro Quelle — dafür ist der Circuit-Breaker aus #73 zuständig, nicht der
Pre-Flight.

**Achtung, gegen die Versuchung:** `available` und `daily_captures` gehören ins Log, **nicht** ins
Gate. Ein `available == 0` ist ein Sekundenzustand; daraus einen Abbruch zu machen, würde den
Falsch-Rot-Modus durch die Hintertür wieder einbauen.

## 1. Ziel

Der Wayback-Archiver spricht die SPN2-API: authentifizierter `POST /save`, Polling auf
`GET /save/status/<job_id>`, Snapshot-URL aus `timestamp` + `original_url`. Fehler werden auch
dann erkannt, wenn sie mit HTTP 200 kommen. Ohne Zugangsdaten bricht der Lauf früh und mit klarer
Meldung ab, statt jede Quelle einzeln gegen eine 401 laufen zu lassen.

## 2. Nicht-Ziele (Scope-Grenze)

- **Kein** `Retry-After`-Auswerten, **kein** `X-Accept-Reduced-Priority`. Das waren AC5–AC7 des
  Issues, geschrieben unter der widerlegten Drosselungs-Annahme; mit gültiger Auth wurde kein
  einziger 429 gemessen. Eigenes Ticket, sobald der Fall im SPN2-Pfad auftritt — dort erscheint er
  als `error:too-many-requests` im Status-JSON, nicht als HTTP-Status.
- **Keine** Änderung an `archive.today`. Der Dienst behält Endpunkt, Header-Extraktion und Retry.
- **Keine** Änderung an `pipeline/ingest.py`, an `archive_all` oder am Pflicht-Anker.
- **Keine** Snapshot-Auflösung als Gate (§0a (3)).
- **Keine** Änderung an der Beweiskette, an Migrationen oder am Datenmodell.
- **Kein** Umgehen von Limits durch mehrere IPs oder wechselnde Absender. Wir sind Gast bei einem
  gemeinnützigen Dienst.

## 3. Betroffene Interfaces / Öffentliche Signaturen

```python
# NEU: src/wortlaut/archive/spn2.py — reines Protokoll, nur stdlib
@dataclass(frozen=True, repr=False)
class IaCredentials:
    access_key: str
    secret: str
    def authorization_header(self) -> str: ...   # "LOW <access>:<secret>"

@dataclass(frozen=True)
class CaptureStatus:
    state: Literal["pending", "success"]
    timestamp: str | None
    original_url: str | None

TRANSIENT_STATUS_EXT: frozenset[str]
SPN2_SAVE_URL: str
SPN2_STATUS_URL: str
SPN2_USER_STATUS_URL: str

def job_id_from_payload(payload: Mapping[str, object]) -> str: ...
def capture_status_from_payload(payload: Mapping[str, object]) -> CaptureStatus: ...
def snapshot_url(timestamp: str, original_url: str) -> str: ...
def user_status_summary(payload: Mapping[str, object]) -> str: ...

# src/wortlaut/archive/archiver.py — WaybackArchiver, erweiterte __init__
class WaybackArchiver:
    def __init__(self, *, credentials: IaCredentials | None = None,
                 limiter: RateLimiter | None = None, attempts: int = 3,
                 base_delay_seconds: float = 2.0,
                 poll_interval_seconds: float = 3.0,
                 poll_timeout_seconds: float = 180.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None: ...
    async def archive(self, origin_url: str) -> str: ...   # SIGNATUR UNVERÄNDERT
    async def user_status(self) -> str: ...                # NEU
    async def aclose(self) -> None: ...

# src/wortlaut/archive/preflight.py — Protokoll + Probe geändert
class ArchiveHealth(Protocol):
    async def user_status(self) -> str: ...
async def probe_archive(wayback: ArchiveHealth) -> str: ...   # probe_url ENTFÄLLT

# src/wortlaut/archive/settings.py — additiv, plus zwei Änderungen
class ArchiveSettings(BaseSettings):
    wayback_min_interval_seconds: float = 10.0    # war 5.0
    ia_access_key: SecretStr | None = None        # NEU
    ia_secret: SecretStr | None = None            # NEU
    spn2_poll_interval_seconds: float = 3.0       # NEU
    spn2_poll_timeout_seconds: float = 180.0      # NEU
    # preflight_url ENTFÄLLT
```

## 4. Design (kurz) — die fünf Entscheidungen

### 4.1 Protokoll und Transport getrennt

`spn2.py` kennt **kein** httpx und **kein** `await`: Es bekommt bereits dekodierte JSON-Payloads
(`Mapping[str, object]`) und liefert Werte oder wirft `ArchiveError`. Damit ist die gesamte
Fehler-Taxonomie ohne Netz und ohne Mock-Client testbar. Den HTTP-Verkehr, das Polling und den
Client-Besitz behält `archiver.py`.

### 4.2 Ein Status-Gate, nicht zwei

`_snapshot_or_error` wird aufgeteilt: Die reine HTTP-Statusbewertung wandert in
`_http_error_or_none(response, service)`, `_snapshot_or_error` ruft sie auf und hängt nur noch die
dienstspezifische Extraktion an. Der SPN2-Pfad benutzt `_http_error_or_none` direkt. So bleibt es
bei **einer** Wahrheit darüber, welcher Statuscode transient ist — eine zweite Tabelle wäre genau
die Doppelung, die #77 an anderer Stelle schon zum Problem gemacht hat.

Neu darin: **401 bekommt einen eigenen Grund** (`unauthorized`, permanent). Er fällt zwar ohnehin
unter „sonstige 4xx", aber die Meldung muss den Betreiber direkt zur Ursache führen.

### 4.3 Unbekannte Fehlercodes sind permanent

`TRANSIENT_STATUS_EXT` ist eine **Allowlist** von 17 Codes (§11). Alles andere — auch ein
unbekannter, künftig hinzukommender Code — gilt als permanent und wird **nicht** wiederholt.

Die Richtung ist Absicht: Ein fälschlich als permanent behandelter Fehler kostet einen
`archive_failed`-Outcome, den ein späterer Lauf nachholt. Ein fälschlich als transient
behandelter Fehler lässt uns gegen einen gemeinnützigen Dienst hämmern, der uns gerade gesagt
hat, dass es keinen Zweck hat.

### 4.4 Das Polling-Limit zählt Versuche, nicht Sekunden

`max_polls = max(1, int(poll_timeout_seconds // poll_interval_seconds))` — bei den Defaults 60
Durchläufe à 3 s. Kein Uhrzeit-Vergleich, kein injizierter Clock: Mit der bereits vorhandenen
`sleep`-Injektion (R-TEST-03) ist der Abbruch damit deterministisch testbar, ohne dass ein Test
real wartet.

Der Timeout ist **permanent** (`transient=False`). SPN2 begrenzt eine Capture-Dauer selbst auf
2 Minuten; wer nach 3 Minuten nicht fertig ist, wird es beim zweiten Anlauf auch nicht. Transient
hieße hier 3 × 180 s pro Quelle — ein hängender Lauf mit anderem Namen, also genau das, was AC6
verbietet.

### 4.5 Zugangsdaten: Pflicht am Composition-Root, nicht im Archiver

Der Archiver nimmt `credentials: IaCredentials | None` und arbeitet ohne sie technisch weiter
(er schickt dann keinen Header und bekommt die 401 als permanenten Fehler). Die **Pflicht** wird
im CLI durchgesetzt, vor dem ersten Fetch — dort, wo die Konfiguration ohnehin gelesen wird.

Grund für die Trennung: Der Archiver bleibt ein Baustein ohne Meinung über den Lauf; der
Composition-Root entscheidet, ob ein Lauf ohne Zugangsdaten überhaupt sinnvoll ist. `--dry-run`
archiviert nicht und bleibt deshalb erlaubt; `--no-preflight` schaltet den Health-Check ab, nicht
die Zugangsdaten-Pflicht.

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)

- [ ] **AC1** Given `WORTLAUT_ARCHIVE_IA_ACCESS_KEY=k-abc-1` und `WORTLAUT_ARCHIVE_IA_SECRET=s-xyz-2`,
  When `ArchiveSettings()` gebaut wird, Then liefern `ia_access_key.get_secret_value()` und
  `ia_secret.get_secret_value()` genau diese Werte; ohne gesetzte ENV sind beide `None`.
- [ ] **AC2** Given genau **eines** der beiden Felder gesetzt, When `ArchiveSettings()` gebaut wird,
  Then wird eine `ValidationError` geworfen — je ein Testfall pro Richtung.
- [ ] **AC3** Given ein `WaybackArchiver` mit Zugangsdaten und ein Transport, der jeden Request
  aufzeichnet, When `archive("https://beispiel.test/x")` läuft, Then geht Request 1 als **POST**
  an `https://web.archive.org/save` mit `Content-Type: application/x-www-form-urlencoded`,
  Header `Accept: application/json` und `Authorization: LOW <access>:<secret>`, und
  `parse_qs(request.content.decode())["url"] == ["https://beispiel.test/x"]`.
  **Achtung:** Der Body geht prozent-kodiert über die Leitung
  (`url=https%3A%2F%2Fbeispiel.test%2Fx`) — ein Substring-Vergleich auf die rohe URL ist rot.
  Deshalb `urllib.parse.parse_qs`, nicht `in request.content`.
- [ ] **AC4** Given derselbe Aufbau **ohne** Zugangsdaten, When `archive(...)` läuft, Then trägt
  der Request **keinen** `Authorization`-Header.
- [ ] **AC5** Given ein Transport, der auf den POST `{"url":…,"job_id":"spn2-abc"}` liefert und auf
  `GET /save/status/spn2-abc` zuerst zweimal `{"status":"pending"}` und dann
  `{"status":"success","timestamp":"20260827142259","original_url":"https://beispiel.test/x"}`,
  When `archive("https://beispiel.test/x")` läuft, Then ist das Ergebnis exakt
  `https://web.archive.org/web/20260827142259/https://beispiel.test/x` und es wurden **drei**
  Status-Abrufe abgesetzt.
- [ ] **AC6** Given der POST antwortet mit **HTTP 200** und
  `{"status":"error","status_ext":"error:too-many-daily-captures","message":"…"}`,
  When `archive(...)` läuft, Then wird `ArchiveError` mit `service=="wayback"`,
  `reason=="error:too-many-daily-captures"` und `transient is False` geworfen, und es wurde
  **kein** Status-Abruf abgesetzt.
- [ ] **AC7** Given der Status-Abruf antwortet mit HTTP 200 und
  `{"status":"error","status_ext":"error:no-browsers-available"}`, When `archive(...)` läuft,
  Then trägt der `ArchiveError` `transient is True`.
- [ ] **AC8** Given der Status-Abruf antwortet mit HTTP 200 und
  `{"status":"error","status_ext":"error:brandneu-unbekannt"}`, When `archive(...)` läuft,
  Then ist `transient is False` — unbekannte Codes werden nicht wiederholt.
- [ ] **AC9** Given der Status-Abruf liefert dauerhaft `{"status":"pending"}`, When `archive(...)`
  mit `poll_interval_seconds=3.0` und `poll_timeout_seconds=9.0` läuft, Then wird nach genau
  **3** Status-Abrufen `ArchiveError` mit `reason=="capture_timeout"` und `transient is False`
  geworfen, und der injizierte `sleep` wurde nie mit einem realen `asyncio.sleep` bedient.
- [ ] **AC10** Given der POST antwortet mit **HTTP 401**, When `archive(...)` läuft, Then wird
  `ArchiveError` mit `reason=="unauthorized"`, `status_code==401` und `transient is False`
  geworfen — und `with_retry` hat den Aufruf **genau einmal** abgesetzt.
- [ ] **AC11** Given eine Erfolgsantwort mit `"timestamp":"2026"` (kein 14-stelliger Stempel),
  When `archive(...)` läuft, Then wird `ArchiveError` mit `reason=="invalid_snapshot_url"`
  geworfen und **keine** URL zurückgegeben.
- [ ] **AC12** Given `IaCredentials(access_key="A"*16, secret="S"*16)`, When `repr(...)` und
  `str(...)` gebildet werden, Then enthält **keines** der beiden die Zeichenketten `"A"*16` oder
  `"S"*16`. Zusätzlich: `repr(ArchiveSettings(...))` mit gesetzten Schlüsseln enthält den
  Secret-Wert nicht.
- [ ] **AC13** Given ein `WaybackArchiver` mit Zugangsdaten und ein Transport, der HTTP 500
  liefert, When `archive(...)` läuft und dabei alles auf `logging.DEBUG` mitgeschnitten wird,
  Then enthält `caplog.text` weder den Access-Key noch das Secret noch die Zeichenkette `"LOW "`.
- [ ] **AC14** Given ein Transport, der auf `GET /save/status/user` mit
  `{"processing":0,"available":3,"daily_captures":0,"daily_captures_limit":30000}` antwortet,
  When `probe_archive(wayback)` läuft, Then liefert es eine Zusammenfassung, die `available=3`
  und `daily_captures=0/30000` enthält, und es wurde **kein** Capture abgesetzt.
- [ ] **AC15** Given derselbe Aufbau, aber HTTP 401, When `probe_archive(wayback)` läuft, Then
  wird `ArchiveError` mit `reason=="unauthorized"` geworfen.
- [ ] **AC16** Given `WORTLAUT_ARCHIVE_IA_ACCESS_KEY`/`_IA_SECRET` **nicht** gesetzt, When
  `main(["ingest", …])` ohne `--dry-run` läuft, Then ist der Rückgabewert **2**, der stderr-Text
  nennt beide ENV-Namen, und es wurde **kein** DIP-Call und **kein** Archiv-Call abgesetzt.
- [ ] **AC17** Given dieselbe Lage **mit** `--dry-run`, When `main([...])` läuft, Then ist der
  Rückgabewert **0** — Dry-Run archiviert nicht und braucht keine Zugangsdaten.
- [ ] **AC18** `60 / ArchiveSettings().wayback_min_interval_seconds <= 7` — die Voreinstellung
  bleibt unter dem SPN2-Limit von 7 Captures/Minute für authentifizierte Nutzer.
- [ ] **AC19** CI vollständig grün (Lint/Type/Test/Coverage, Security-Gate, Architektur-Fitness,
  Docker inkl. Serve-Smoke) + **0 neue Sonar-Issues**.

## 6. Testplan (Test-zu-AC-Mapping)

- **Unit (`tests/unit/test_spn2_protocol.py`, neu)** — reines Protokoll, keine Fakes, kein Netz:
  AC6/AC7/AC8→`test_status_ext_transienz` (parametrisiert über permanent · transient · unbekannt),
  AC11→`test_ungueltiger_zeitstempel_wirft`, AC12→`test_zugangsdaten_nicht_in_repr_und_str`,
  AC14→`test_user_status_zusammenfassung`, plus `test_job_id_ohne_formatpruefung` (belegt, dass
  `spn2-<hex>` durchgeht — die gemessene Wirklichkeit, §0a (1)).
- **Unit (`tests/unit/test_archiver_spn2.py`, neu)** — gegen einen `httpx.MockTransport`:
  AC3→`test_post_auf_save_mit_auth_header`, AC4→`test_ohne_zugangsdaten_kein_header`,
  AC5→`test_polling_bis_success_baut_snapshot_url`, AC9→`test_polling_timeout`,
  AC10→`test_401_ist_permanent_und_ohne_retry`, AC13→`test_secret_nicht_im_log`.
- **Unit (`tests/unit/test_preflight.py`, ersetzen)**: AC14→`test_probe_liest_user_status`,
  AC15→`test_probe_401_wirft`. Die bisherigen Capture-Probe-Tests entfallen mit der Probe.
- **Unit (`tests/unit/test_settings.py`, erweitern)**: AC1→`test_ia_zugangsdaten_aus_env`,
  AC2→`test_nur_ein_schluessel_ist_fehler` (zwei Richtungen), AC18→`test_drosselung_unter_limit`.
- **Unit (`tests/unit/test_cli.py`, erweitern)**: AC16→`test_ohne_zugangsdaten_exit_2`,
  AC17→`test_dry_run_ohne_zugangsdaten_ok`.
- **Live (`tests/live/test_archive_live.py`, ändern)**: Der Test läuft nur unter `-m live` und
  archiviert heute `https://example.com/` — das schlägt seit dem Tageslimit systematisch fehl
  (§0a (4)). Ziel-URL auf `https://dserver.bundestag.de/btp/21/21089.pdf` umstellen und den Test
  überspringen, wenn keine Zugangsdaten in der Umgebung stehen.
- **Integration:** keine neue nötig. Der Archiver hat keinen DB- oder WORM-Bezug; die bestehenden
  Integrationstests benutzen Fake-Archiver und bleiben unberührt.

## 7. Recht / Security

- **R-SEC-01 (keine Interna nach außen):** Die Zugangsdaten sind ein echtes Secret. `IaCredentials`
  hat ein redigierendes `__repr__`, `ArchiveSettings` hält sie als `SecretStr`, und AC13 prüft mit
  einem Log-Mitschnitt auf `DEBUG`, dass weder Schlüssel noch die Zeichenkette `"LOW "` je in einer
  Logzeile landen. Der Header wird **nie** geloggt, auch nicht im Fehlerfall.
- **R-SEC-05 (kein DNS-Rebinding):** Der gepinnte Client aus #36 bleibt in Benutzung. Alle drei
  SPN2-Endpunkte liegen auf `web.archive.org` — derselbe Host, derselbe gepinnte Transport, kein
  zweiter Client.
- **`follow_redirects=False` bleibt.** Der POST-Pfad antwortet mit 200; ein Redirect wäre ein
  Signal, dass wir nicht dort sind, wo wir denken.
- **R-CORE-02 (Provenienz vor Verarbeitung):** unberührt. Der Pflicht-Anker bleibt, die Reihenfolge
  in `pipeline/ingest.py` bleibt, die Snapshot-URL wird weiterhin nur aus einer Erfolgsantwort
  akzeptiert — nur ist „Erfolg" jetzt `status: success` statt eines 2xx mit Header.
- **Fairness gegenüber dem Dienst:** Drosselung unter 7/min (AC18), Allowlist für Wiederholungen
  (§4.3), kein Umgehen von Limits (§2). Wir sind Gast.

## 8. Risiken & offene Fragen

- **Risiko: Der Anker ist zunächst unscharf.** Bis der CDX-Index nachzieht, löst die gespeicherte
  Snapshot-URL auf den nächstgelegenen älteren Snapshot auf (§0a (3)). Bewusst in Kauf genommen:
  Ein Auflösungs-Gate wäre falsch rot, und die zeitgleiche Bezeugung leistet seit #76 der
  RFC-3161-Zeitstempel. **Nicht** stillschweigend hinnehmen, sondern als Kommentar an der
  URL-Konstruktion festhalten.
- **Risiko: 33 s pro Quelle statt weniger Sekunden.** Ein Backfill über viele Quellen dauert damit
  spürbar länger. Kein Gegenmittel in diesem Increment — Parallelisierung wäre bei 3 gleichzeitigen
  Sessions und 7 Captures/min ohnehin schnell am Limit und gehört in ein eigenes Ticket.
- **Kein Risiko, obwohl es so aussieht:** Der zweite POST auf dieselbe URL innerhalb einer Stunde
  liefert dieselbe `job_id` (§0a (1)). Ein `with_retry`-Durchlauf trifft damit denselben Auftrag
  und pollt ihn zu Ende, statt einen zweiten Capture auszulösen — das ist das gewünschte Verhalten.
- **Offen, bewusst vertagt:** Ob der Pflicht-Anker angesichts #76 überhaupt noch bei einem einzigen
  Anbieter liegen muss, ist eine Architektur-Entscheidung mit ADR (siehe #108 Teil C, #78).

## 9. Definition of Done (Verweis)

Erfüllt [../docs/rules.md](../docs/rules.md) DoD: AC grün, alle Gates grün, Review, keine
Gott-Klassen, kein Secret/Pickle/LLM-Freitext.

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu:**
1. `src/wortlaut/archive/spn2.py`
2. `tests/unit/test_spn2_protocol.py`
3. `tests/unit/test_archiver_spn2.py`

**Ändern:**
4. `src/wortlaut/archive/archiver.py` — `WaybackArchiver` auf SPN2, `_http_error_or_none`.
5. `src/wortlaut/archive/preflight.py` — `ArchiveHealth`-Protokoll, `probe_archive` ohne URL.
6. `src/wortlaut/archive/settings.py` — Zugangsdaten, Polling, Drosselung; `preflight_url` raus.
7. `src/wortlaut/cli.py` — Zugangsdaten bauen, Pflicht durchsetzen, `_preflight_ok` anpassen.
8. `tests/unit/test_preflight.py` — auf die neue Probe umstellen.
9. `tests/unit/test_settings.py` — AC1, AC2, AC18.
10. `tests/unit/test_cli.py` — AC16, AC17.
11. `tests/unit/test_archiver.py` — die Wayback-Tests des Browser-Pfads entfernen; die
    archive.today-Tests und die `archive_all`-Tests bleiben **unverändert**.
12. `tests/live/test_archive_live.py` — Ziel-URL und Skip-Bedingung.
13. `deploy/env.example` — neue Variablen dokumentieren, `5.0` auf `10.0` korrigieren.

**Nicht anfassen:** `src/wortlaut/pipeline/ingest.py`, `src/wortlaut/archive/throttle.py`,
`src/wortlaut/archive/retry.py`, `src/wortlaut/archive/pinned.py`, `src/wortlaut/archive/ssrf.py`,
`src/wortlaut/archive/errors.py`, `pyproject.toml`, `uv.lock`, `Dockerfile`,
`.github/workflows/ci.yml`, `.importlinter`, `migrations/**`.

## 11. Umsetzungsdetails je Datei

### `src/wortlaut/archive/spn2.py` (neu)

Modul-Docstring: Protokoll-Schicht der SPN2-API, **nur stdlib** (kein httpx, kein await), damit
die Fehler-Taxonomie ohne Netz testbar bleibt. Verweis auf §0a der Spec für die gemessenen
Eigenheiten.

Konstanten:

```python
SPN2_SAVE_URL = "https://web.archive.org/save"
SPN2_STATUS_URL = "https://web.archive.org/save/status/"
SPN2_USER_STATUS_URL = "https://web.archive.org/save/status/user"
_SNAPSHOT_BASE = "https://web.archive.org/web/"
_TIMESTAMP_RE = re.compile(r"\d{14}")
```

`TRANSIENT_STATUS_EXT` — genau diese 17 Codes, als `frozenset[str]`, mit einem Kommentar, dass
alles andere permanent ist (§4.3):

```
error:bad-gateway            error:browsing-timeout        error:cannot-fetch
error:capture-location-error error:celery                  error:gateway-timeout
error:internal-server-error  error:invalid-server-response error:job-failed
error:no-browsers-available  error:protocol-error          error:proxy-error
error:read-timeout           error:service-unavailable     error:soft-time-limit-exceeded
error:too-many-requests      error:user-session-limit
```

`IaCredentials` als `@dataclass(frozen=True, repr=False)` mit `access_key: str`, `secret: str`,
einem `__repr__`, das `"IaCredentials(access_key=<redacted>, secret=<redacted>)"` liefert, und
`authorization_header() -> str` mit `f"LOW {self.access_key}:{self.secret}"`. Kein `__str__`
definieren — Python fällt ohne `__str__` auf `__repr__` zurück, das genügt und ist weniger Code.

`CaptureStatus` als `@dataclass(frozen=True)` mit `state: Literal["pending","success"]`,
`timestamp: str | None`, `original_url: str | None`.

Funktionen:

- `_error_from_payload(payload) -> ArchiveError` — liest `status_ext` (fehlt es, `"unknown"`),
  setzt `transient = status_ext in TRANSIENT_STATUS_EXT` und baut
  `ArchiveError("wayback", status_ext, transient=transient)`. Der **Grund ist der Code selbst** —
  damit trägt `ArchiveError.label()` ihn bis in die Summary. `message` NICHT in den Fehler
  übernehmen: Es ist Fremdtext unbekannter Länge, und `label()` wird aggregiert.
- `job_id_from_payload(payload) -> str` — ist `payload.get("status") == "error"`, wirf
  `_error_from_payload(...)`. Sonst `job_id` lesen; fehlt es oder ist es kein nicht-leerer `str`,
  wirf `ArchiveError("wayback", "no_job_id")`. **Keine** Formatprüfung (§0a (1)).
- `capture_status_from_payload(payload) -> CaptureStatus` — `status == "error"` ⇒ wirf.
  `status == "success"` ⇒ `timestamp` und `original_url` lesen; fehlt eines, wirf
  `ArchiveError("wayback", "no_snapshot_url")`; sonst `CaptureStatus("success", ts, ou)`.
  Jeder andere Wert (inklusive `"pending"` und fehlendem `status`) ⇒
  `CaptureStatus("pending", None, None)`.
- `snapshot_url(timestamp, original_url) -> str` — `_TIMESTAMP_RE.fullmatch` prüfen, sonst
  `ArchiveError("wayback", "invalid_snapshot_url")`; danach
  `f"{_SNAPSHOT_BASE}{timestamp}/{original_url}"`. Darüber ein Kommentar, dass diese URL bis zum
  Nachziehen des CDX-Index auf den nächstgelegenen älteren Snapshot auflöst (§0a (3)) und **nicht**
  im Ingest-Pfad gegengeprüft werden darf.
- `user_status_summary(payload) -> str` — baut
  `f"available={a} processing={p} daily_captures={d}/{limit}"` aus `available`, `processing`,
  `daily_captures`, `daily_captures_limit`, mit `"?"` für fehlende Felder. Reine Log-Zeile,
  **keine** Bewertung (§0b).

### `src/wortlaut/archive/archiver.py` (ändern)

- `WAYBACK_SAVE_URL` und `_snapshot_from_wayback` **ersatzlos entfernen**.
- `_snapshot_or_error` aufteilen (§4.2):

```python
def _http_error_or_none(response, *, service) -> ArchiveError | None:
    status = response.status_code
    if status == 401:
        return ArchiveError(service, "unauthorized", status_code=401, transient=False)
    if status == 429 or status == 408 or 500 <= status <= 599:
        return ArchiveError(service, "http_status", status_code=status, transient=True)
    if not 200 <= status <= 399:
        return ArchiveError(service, "http_status", status_code=status, transient=False)
    return None
```

  `_snapshot_or_error` ruft das zuerst auf und behält seinen Rest unverändert. Der Docstring dort
  wird auf die neue Aufteilung nachgezogen; die Tabelle bleibt, ergänzt um die 401-Zeile.
- `WaybackArchiver.__init__` um `credentials`, `poll_interval_seconds`, `poll_timeout_seconds`
  und `sleep` erweitern (Signatur in §3). Alle keyword-only, alle mit Default — bestehende
  Aufrufe bleiben gültig.
- Ein privates `_headers()`: `{"Accept": "application/json"}`, ergänzt um
  `{"Authorization": self._credentials.authorization_header()}` **nur** wenn Zugangsdaten
  gesetzt sind.
- `_attempt(origin_url)` neu:
  1. Limiter wie bisher, Client wie bisher (`pinned_client(WAYBACK_HOST)`, `SsrfBlocked` →
     `ArchiveError("wayback","transport",transient=True)` — unverändert übernehmen).
  2. `await client.post(SPN2_SAVE_URL, data={"url": origin_url}, headers=self._headers())`,
     eingefasst in dieselben `httpx.TimeoutException` / `httpx.TransportError`-Handler wie heute.
  3. `_http_error_or_none` → bei Fehler werfen.
  4. `payload = self._json_or_error(response)` → `job_id_from_payload(payload)`.
  5. Polling-Schleife, `max_polls` nach §4.4:
     `await self._sleep(self._poll_interval_seconds)`, dann
     `client.get(f"{SPN2_STATUS_URL}{job_id}", headers=self._headers())`, `_http_error_or_none`,
     JSON, `capture_status_from_payload`. Bei `state == "success"` die Schleife verlassen.
  6. Nach der Schleife ohne Erfolg: `ArchiveError("wayback","capture_timeout", transient=False)`.
  7. `url = snapshot_url(status.timestamp, status.original_url)`, danach
     `_validate_snapshot_url(url, service="wayback", host=WAYBACK_HOST)`, dann `return url`.
- `_json_or_error(response) -> Mapping[str, object]`: `response.json()` in `try/except Exception`;
  bei Fehler **oder** wenn das Ergebnis kein `dict` ist, `ArchiveError("wayback","invalid_response")`
  (permanent). **Den Antworttext nicht ins Log schreiben** — er ist Fremdinhalt.
- `user_status()` neu: gepinnter Client, `GET f"{SPN2_USER_STATUS_URL}?_t={time.time_ns()}"` mit
  `self._headers()` (der Cache-Buster steht so in der API-Doku), `_http_error_or_none`, JSON,
  `user_status_summary(payload)` zurückgeben. **Kein** Retry, **keine** Bewertung von `available`
  (§0b). Der Klassen-Docstring wird auf SPN2 nachgezogen.

### `src/wortlaut/archive/preflight.py` (ändern)

`PROBE_URL` entfernen. Neues `ArchiveHealth`-Protokoll mit `async def user_status(self) -> str`.
`probe_archive(wayback: ArchiveHealth) -> str` gibt `await wayback.user_status()` zurück.

Der Docstring muss **umgeschrieben** werden: Die heutige Begründung („genau der Endpunkt, von dem
der Backfill abhängt") beschreibt einen Aufbau, den es nicht mehr gibt, und wäre nach der Änderung
schlicht falsch. Der neue Text übernimmt §0b: dass die Regel aus #77 gilt, dass die Capture-Probe
seit SPN2 aber einen URL-abhängigen Falsch-Rot-Modus hat, und was `status/user` belegt und was
nicht. Die beiden alten Gegenbeispiele (Site-Root, `wayback/available`) bleiben stehen — sie sind
weiter gültig.

### `src/wortlaut/archive/settings.py` (ändern)

Import von `PROBE_URL` und das Feld `preflight_url` entfernen. `SecretStr` aus `pydantic`
importieren. Felder wie in §3; `wayback_min_interval_seconds` von `5.0` auf `10.0` mit Kommentar
„SPN2 erlaubt authentifiziert 7 Captures/Minute — 10 s Abstand bleibt darunter".

Ein `@model_validator(mode="after")`, der wirft, wenn genau eines der beiden Schlüsselfelder
gesetzt ist (AC2). Die Fehlermeldung nennt **nur die Feldnamen**, nie einen Wert.

### `src/wortlaut/cli.py` (ändern)

- Import `IaCredentials` aus `wortlaut.archive.spn2`.
- Ein `_ia_credentials(settings) -> IaCredentials | None`: beide gesetzt ⇒ `IaCredentials` aus den
  `get_secret_value()`, sonst `None` (der Einzelfall ist bereits in den Settings abgefangen).
- In `_run` **direkt nach dem Settings-Block** (vor `create_async_engine_from`):

```python
credentials = _ia_credentials(archive_settings)
if credentials is None and not args.dry_run:
    print(
        "Konfiguration fehlgeschlagen: keine Internet-Archive-Zugangsdaten "
        "(WORTLAUT_ARCHIVE_IA_ACCESS_KEY / WORTLAUT_ARCHIVE_IA_SECRET). "
        "Save Page Now lehnt anonyme Aufrufe mit 401 ab; ohne Archivierung "
        "kein Insert.",
        file=sys.stderr,
    )
    return 2
```

- `_build_archivers(settings, credentials)` reicht `credentials`, `poll_interval_seconds` und
  `poll_timeout_seconds` an `WaybackArchiver` durch. Die archive.today-Zeilen bleiben unverändert.
- `_preflight_ok`: `probe_archive(wayback)` ohne `probe_url`; bei Erfolg
  `logger.info("Pre-Flight: Fremdarchiv bereit (%s)", summary)`. Die Skip-Bedingung
  (`no_preflight` · `dry_run` · `preflight_enabled`) und der Fehlerpfad bleiben **wie sie sind**.

### `deploy/env.example` (ändern)

Im Abschnitt „Fremdarchivierung": `5.0` auf `10.0` korrigieren, die beiden Schlüssel als
**Pflichtfelder** aufnehmen (leer, wie `WORTLAUT_DIP_API_KEY`) mit dem Hinweis, dass sie unter
`https://archive.org/account/s3.php` erzeugt werden und ein Lauf ohne sie mit Exit 2 abbricht,
sowie die beiden Polling-Variablen auskommentiert mit ihren Defaults.

### Tests

Deutsche Testnamen wie im Bestand.

**Injektionspunkt wie im Bestand, Attrappe bewusst anders.** `tests/unit/test_archiver.py` setzt
`archiver._client` auf ein `AsyncMock()` — der Injektionspunkt bleibt derselbe, aber für den
SPN2-Fluss wird stattdessen ein echter Client mit Mock-Transport eingehängt:

```python
client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
archiver._client = client
```

Grund: Der Fluss besteht aus **mehreren** Requests mit **unterschiedlichen** Antworten (POST,
dann N Status-Abrufe), und AC3/AC4 behaupten etwas über die tatsächlich gesetzten Header. Ein
`AsyncMock` liefert dafür keine echten `httpx.Request`-Objekte; der Handler von `MockTransport`
bekommt sie und kann sie in einer **lokalen** Liste sammeln (Closure, kein Modulzustand — S8997).
`MockTransport` nimmt einen ganz normalen synchronen Handler, auch am `AsyncClient`.

Die bestehenden `AsyncMock`-Tests in `test_archiver.py` werden **nicht** auf dieses Muster
umgebaut — sie betreffen archive.today und bleiben, wie sie sind.

Sonar-Fallen, die in diesem Repo schon Zyklen gekostet haben und hier drohen:
- **`python:S5778`** — in einem `pytest.raises`-Block darf **nur** der Aufruf unter Test stehen.
  Hilfsaufrufe (Client-Aufbau, Payload-Bau) **vor** den Block hoisten.
- **`python:S8997`** — keinen Modul- oder Klassenzustand für die Aufzeichnung der Requests
  benutzen; die Liste lokal in der Testfunktion halten und per Closure füllen.
- **`python:S9081`** — in `patch()` `return_value=` statt eines Lambda.
- **`python:S6698`** — keine ausgeschriebenen Zugangsdaten-artigen Literale. Testschlüssel
  zusammensetzen (z. B. `"k-" + "abc-1"`), damit die Secret-Regel nicht greift.

## 12. Do-NOT (hart)

- **KEIN** `GET /save/<url>` mehr — der Browser-Pfad ist tot (§0).
- **KEINE** Formatprüfung auf `job_id` (§0a (1)).
- **KEIN** Statuscode-only-Check: `status: "error"` kommt mit HTTP 200 (§0a (2)).
- **KEINE** Snapshot-Auflösung, kein `HEAD`/`GET` auf die gebaute Snapshot-URL, kein CDX-Abruf —
  weder im Archiver noch im Ingest-Pfad (§0a (3)).
- **KEINE** Bewertung von `available`/`daily_captures` im Pre-Flight — nur loggen (§0b).
- **KEIN** Wiederholen unbekannter `status_ext`-Codes (§4.3).
- **KEIN** Logging des `Authorization`-Headers, der Zugangsdaten, der Zeichenkette `"LOW "` oder
  des rohen Antworttextes — auch nicht auf `DEBUG`, auch nicht im Fehlerpfad.
- **KEINE** Änderung an `pipeline/ingest.py`, `archive_all`, `ArchiveResult`, `IngestOutcome`,
  am Datenmodell oder an Migrationen.
- **KEINE** Änderung an `ArchiveTodayArchiver` und an dessen Tests.
- **KEIN** zweiter httpx-Client und **kein** Umgehen von `pinned_client` im Produktionscode.
- **KEIN** `follow_redirects=True`.
- **KEIN** `# type: ignore`, **kein** `noqa` ohne Regelcode und Begründung.
- **KEINE** neuen Dependencies, **keine** Änderung an `pyproject.toml`, `uv.lock`, `Dockerfile`,
  `.importlinter` oder der CI-Datei.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate (ruff · mypy · lint-imports · pytest) fährt der Reviewer selbst — ein Selbstbericht des
Coders ersetzt es nicht. Falls du doch lokal testen willst, ist der Marker-Ausdruck
`-m "not integration and not live"` zu benutzen: Ein bloßes `-m "not integration"` **ersetzt** den
`-m "not live"`-Ausdruck aus `addopts` in `pyproject.toml`, statt ihn zu ergänzen — dann laufen die
echten Fremdarchiv-Calls mit und der Lauf wird aus Netzgründen rot.
