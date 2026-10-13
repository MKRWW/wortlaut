# Betrieb — der heutige Handbetrieb, aufgeschrieben

> **Was diese Seite ist:** die Beschreibung dessen, **was heute tatsächlich läuft und wie es
> gepflegt wird** — nicht, wie es sein sollte. Der Zielzustand steht in
> [`deploy.md`](deploy.md); wo beides auseinandergeht, ist es hier ausdrücklich vermerkt.
> Verbessert wird in eigenen Stories (Betriebs-Epic #90), nicht auf dieser Seite.
>
> **Was hier bewusst fehlt:** Hostnamen, Adressen, Zugangswege, Schlüssel und konkrete Pfade auf
> dem Server. Das Repo ist öffentlich. Diese Angaben stehen im **internen Betriebsdokument**, das
> der Maintainer führt. Hier stehen Platzhalter:
>
> | Platzhalter | Bedeutung |
> |---|---|
> | `$STACK` | Verzeichnis mit der Compose-Datei des Betriebs (nur root lesbar) |
> | `$DATA` | Datenverzeichnis auf dem verschlüsselten Volume |
> | `$LOGS` | Verzeichnis für die Protokolle der Erfassungs-Läufe (unter `$DATA`) |
>
> Offene Punkte sind als `> TODO:` markiert — nicht mit Vermutungen gefüllt.

Stand: 2026-10-03 (Image `d700dab`, Migration `0008`, 9 Quellen, Sicherung aktiv).

---

## 1. Was läuft wo

Ein einzelner Dedicated-Server. Alles läuft in Docker Compose aus **einer** Compose-Datei in
`$STACK`. Zugriff hat heute nur der Maintainer, per SSH mit Schlüssel.

| Dienst | Image | Zweck | Läuft dauerhaft? |
|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | Ledger (Quellen, Spans, Zeitstempel, Attestierungen) | ja |
| `minio` | `minio/minio:RELEASE.2025-09-07T16-13-09Z` | WORM-Speicher für die Rohbytes (Object Lock) | ja |
| `api` | `ghcr.io/mkrww/wortlaut:<sha>` | Lese-API (`serve`) | ja |
| `app` | dasselbe Image wie `api` | Einmal-Kommandos (`ingest`, `attest`, …); Compose-Profil `tools` | nein, nur `run --rm` |

**Abweichungen vom Zielzustand in `deploy.md`:**

- **Kein Tunnel.** Die API ist heute **nicht öffentlich erreichbar**. Alle Ports sind nur an
  `127.0.0.1` gebunden; von außen geht es nur per SSH-Portweiterleitung.
- **Der Image-SHA steht direkt in der Compose-Datei**, nicht in einer `.env`. Er kommt zweimal
  vor (`api` und `app`) und wird bei jedem Update in beiden Zeilen getauscht.
- **Die Dienste heißen `postgres`/`minio`/`api`/`app`**, nicht `db`/`worm`/`api`/`tunnel` wie in
  der Vorlage.
- **MinIO läuft noch aus dem lokalen Docker-Cache** mit dem ursprünglichen Upstream-Tag. Das
  Image ist upstream nicht mehr abrufbar (#119); eine unveränderte Kopie liegt unter
  `ghcr.io/mkrww/minio` (siehe `deploy/compose.example.yml`).

> TODO: Compose-Datei des Betriebs auf `ghcr.io/mkrww/minio@sha256:a16cad48…` umstellen, damit ein
> Neuaufbau nicht vom lokalen Cache abhängt. Bis dahin: **auf dem Server nie `docker image prune`**.

**Kein Autostart.** Alle Dienste haben `restart: "no"`. Die Datenverzeichnisse liegen auf einem
verschlüsselten Volume, das nach einem Neustart des Servers **von Hand entsperrt** wird. Würden
Postgres und MinIO vorher starten, legten sie still einen zweiten, leeren Datenbestand an (siehe
Kommentar in `deploy/compose.example.yml`).

> TODO: Der genaue Entsperr-Ablauf steht nur im internen Betriebsdokument. Er gehört dort
> vollständig hin (Wer? Womit? Was prüft man danach?).

---

## 2. Der Erfassungs-Ablauf

Alle Erfassungs-Läufe sind **Einmal-Kommandos** über den `app`-Dienst; nichts davon läuft
zeitgesteuert. Reihenfolge:

```
ingest → timestamp → capture → attest → reparse
```

| Schritt | Was er tut | Spricht mit | Braucht |
|---|---|---|---|
| `ingest` | DIP-Liste holen, PDF laden, Rohbytes hashen, in WORM ablegen, Text einfrieren, eintragen | DIP (Bundestag) | `WORTLAUT_DIP_API_KEY` |
| `timestamp` | RFC-3161-Zeitstempel über den Hash | Zeitstempeldienste | — |
| `capture` | fordert beim Internet Archive einen Snapshot an — nur, wenn es noch keinen byte-gleichen gibt | Internet Archive (lesend + Save Page Now) | `WORTLAUT_ARCHIVE_IA_*` |
| `attest` | prüft, ob ein Wayback-Snapshot **byte-gleich** ist, und hält das fest | Internet Archive (nur lesend) | — |
| `reparse` | erzeugt Spans für attestierte Quellen ohne Spans | nur Datenbank und WORM | — |
| `status` | zählt den Rückstand je Stufe | nur Datenbank | — |

Erst nach `attest` **und** `reparse` wird eine neue Quelle zitierbar. Das ist Absicht
(ADR-0009): Ausgespielt wird nur, was fremdbezeugt ist.

### So wird ein Lauf heute gefahren

Als root auf dem Server, mit einer kurzen Schreibhilfe:

```
C="docker compose -f $STACK/compose.yaml --project-directory $STACK"
```

**Jeder Schritt zuerst mit `--dry-run`**, dann echt, und die Ausgabe in eine Protokolldatei:

```
{ echo "=== Start $(date -u +%FT%TZ) — ingest ==="
  $C run --rm -T app ingest --since 2026-09-01 --limit 1
  echo "EXIT=$?"
  echo "=== Ende $(date -u +%FT%TZ) ==="
} >> $LOGS/ingest.log 2>&1
```

- **Beim ersten Lauf nach längerer Pause: `--limit 1`.** Erst wenn der sauber durchläuft, ohne
  Limit.
- Danach `timestamp`, `capture`, `attest`, `reparse` in dieser Reihenfolge, jeweils ohne Argumente.
- Am Ende `status`. Ausgeglichener Zustand:

  ```
  sources=<n> unstamped=0 unattested=0 unattested_capture_failed=0 attested_without_spans=0
  ```

- **`unattested` > 0 direkt nach einem Ingest ist normal.** Ein frischer Capture ist erst Stunden
  bis Tage später abrufbar. `capture` und `attest` werden dann später erneut gefahren;
  `capture` kühlt jede Quelle ab (72 h nach Erfolg, 6 h nach Fehlschlag) und fragt nicht doppelt an.

**Protokolle nach `$LOGS`, nicht nach `/tmp`.** Der Kernel-Schutz `fs.protected_regular`
verhindert, dass root dort eine Datei überschreibt, die einem anderen Nutzer gehört — ein
`>>`-Anhängen scheitert dann still.

### Exit-Codes, die man kennen muss

| Exit | Bedeutung | Was tun |
|---|---|---|
| 0 | ok (auch bei `failed`/`snapshot_unavailable` — das sind protokollierte Ergebnisse) | nichts |
| 1 | mindestens eine Quelle mit Fehler | Protokoll lesen, später erneut fahren |
| 2 | Konfiguration fehlt (Meldung nennt die Variable, nie den Wert) | `.env`/Compose-Datei prüfen |
| 3 | Pre-Flight gescheitert oder Circuit-Breaker | Archivdienst gestört — **warten**, nicht wiederholen |
| 4 | **Alarm:** Bytes passen nicht zum Ledger-Hash (`attest`, `capture`, `reparse`, `timestamp`) | **anhalten**, Quelle prüfen, nichts überschreiben |

---

## 3. Eine neue Version ausrollen

Heutiger Ablauf, als root:

1. **Compose-Datei sichern**, mit Zeitpunkt und Ziel-SHA im Namen:
   ```
   cp -p $STACK/compose.yaml $STACK/compose.yaml.bak-$(date -u +%Y%m%dT%H%M%SZ)-vor-<neuer-sha>
   ```
2. **SHA tauschen** — beide Vorkommen (`api` und `app`):
   ```
   sed -i "s/<alter-sha>/<neuer-sha>/g" $STACK/compose.yaml
   ```
3. **Image ziehen:** `docker pull ghcr.io/mkrww/wortlaut:<neuer-sha>`
4. **Migrationen auslösen.** `serve` migriert nicht; das tut jedes Erfassungs-Kommando beim Start.
   Der Trockenlauf genügt:
   ```
   $C run --rm -T app reparse --dry-run
   ```
   Bricht er mit einer Migrationsmeldung ab, **nicht weitermachen** — die Meldung nennt, was vorher
   zu tun ist (Beispiel: Migration `0006` verlangt, dass vorher `attest` lief).
5. **Nur die API neu erzeugen:**
   ```
   $C up -d --no-deps api
   ```
   **`--no-deps` ist Pflicht.** Ohne es würde Compose Postgres und MinIO mit anfassen (siehe §5).
6. **Prüfen:** `/readyz` liefert 200, eine Suche liefert Treffer, `status` stimmt.

---

## 4. Wenn etwas schiefgeht

### API neu starten

```
$C up -d --no-deps api
```

`/healthz` 200 und `/readyz` 503 heißt: Prozess lebt, Datenbank nicht erreichbar — **kein Grund
für einen Neustart der API**, sondern für einen Blick auf Postgres.

### Rollback

Die letzte Sicherung der Compose-Datei zurückkopieren (die `.bak-…-vor-<sha>`-Datei des
fehlgeschlagenen Updates) und `$C up -d --no-deps api`. Das rollt **nur die Anwendung** zurück,
nicht die Datenbank. Migrationen sind additiv; ein Schritt zurück über eine Migration ist kein
Handgriff für nebenbei.

### Nach einem Neustart des Servers

1. Volume entsperren (internes Betriebsdokument).
2. Prüfen, dass `$DATA` die erwarteten Verzeichnisse enthält — **vor** dem Start.
3. `$C up -d postgres minio`, dann `$C up -d --no-deps api`.
4. `status` und eine Suche als Rauchtest.

### Logs

| Was | Wo |
|---|---|
| Lese-API | `$C logs --since 1h api` |
| Erfassungs-Läufe | `$LOGS/*.log` (je Lauf ein Abschnitt mit Start, Ausgabe, `EXIT=`, Ende) |
| Sicherung und Restore-Test | `$LOGS/backup.log` |
| Ausgerollte Versionen | die `compose.yaml.bak-…`-Dateien in `$STACK` |

### Sicherung

Seit 2026-10-03 läuft eine automatische Sicherung (systemd-Timer):

- **Täglich**, und nur bei entsperrtem Volume — vor dem Entsperren nach einem Neustart wird der
  Lauf übersprungen, statt einen leeren Bestand zu sichern.
- **Gesichert:** ein logischer Datenbank-Dump, die WORM-Objekte **dateibasiert samt Versionen**
  (die gespeicherten Fundstellen `raw_bytes_ref` hängen an den Version-IDs) und die
  Betriebskonfiguration. **Nicht** gesichert: der Header des verschlüsselten Volumes.
- **Verschlüsselt, bevor die Daten den Server verlassen** (restic), Ablage off-site bei einem
  externen Speicheranbieter. Der Anbieter sieht nur Chiffrat.
- **Geprüft:** nach jedem Lauf `restic check`; einmal im Monat ein **Restore-Test**, der eine
  Wegwerf-Datenbank und einen Test-Bucket sichert, zurückholt und bitgenau vergleicht.
  Ein Backup, dessen Wiederherstellung nicht geprüft wird, zählt nicht.
- Der Datenbank-Dump läuft aus einem frischen Einmal-Container (siehe §5, `docker exec` geht nicht).

Zielort, Zugang und Repo-Passwort stehen im internen Betriebsdokument.

> TODO: Den Ablauf einer **echten** Wiederherstellung (nicht nur des Tests) aufschreiben — auf
> einem frischen Server, Schritt für Schritt.

---

## 5. Bekannte Eigenheiten

**Postgres und MinIO stehen dauerhaft auf `unhealthy`.** Seit einem Docker-Upgrade zeigt
`docker ps` beide als „Up", `docker exec` findet sie aber nicht mehr. Die Healthchecks laufen
deshalb ins Leere. Netz, Ports und Daten funktionieren; neue Container (`run --rm`) sind nicht
betroffen. Folgen für den Betrieb:

- **Kein `docker exec` in diese beiden Container.** Datenbank-Abfragen laufen über einen
  `app`-Container mit `run --rm -T --entrypoint python app -c '…'` und dem DSN aus der Umgebung.
- **Postgres und MinIO nicht neu erzeugen**, solange das nicht als eigener, geplanter Schritt
  passiert (`--no-deps` beim API-Update).

> TODO: Neuaufbau beider Container als geplanter Schritt — mit Sicherung vorher und dem
> MinIO-Image aus der eigenen Registry.

**SSH sperrt schnell.** Wenige fehlgeschlagene oder abgebrochene Verbindungsversuche führen zu
einer Sperre der Quelladresse (Port 22 läuft dann in einen Timeout). Verbindungsprobleme deshalb
mit **einem** ausführlichen Versuch (`ssh -v`) eingrenzen, nicht mit einer Serie.

---

## 6. Fremdarchive: Diagnose ohne Last

Aus #114 übernommen. **Eine URL, mit Abstand, nie als Serie.**

1. **Erst lesen, dann anfragen.** Ob das Archiv eine Quelle erreicht, beantworten CDX-Index und
   Playback-Header, ohne einen einzigen Capture auszulösen:
   ```
   curl -s "https://web.archive.org/cdx/search/cdx?url=<url>&output=json&fl=timestamp,statuscode,digest,mimetype"
   curl -s -o /dev/null -D - -r 0-0 "https://web.archive.org/web/<timestamp>id_/<url>" | grep -i x-archive-src
   ```
   `x-archive-src: spn2-…` ist ein direkter Save-Page-Now-Capture; `…-zeno-k8s-…` sind die
   Crawler des Internet Archive. **Beide zählen** für `attest`, solange die Bytes gleich sind.
2. **Status `-` mit Typ `warc/revisit` ist ein gültiger Snapshot** (dedupliziert, Bytes sind da).
3. **„Fehlt im Index" ist kein Fehlschlag.** Der Index zieht teils mehrere Tage nach.
4. **Wenn doch gemessen werden muss: genau ein Capture** (`capture` mit `--limit 1`), nach einer
   Ruhephase. Eine Serie wäre als Gast unangemessen, und eine Ratensperre wäre von einer echten
   Störung nicht mehr zu unterscheiden.

Bezugsfall: Am 28.08.2026 bekam Save Page Now für bundestag.de 404, obwohl die Quelle erreichbar
war. Ursache war eine vorübergehende Störung allein des direkten Save-Page-Now-Pfads; die Crawler
holten dieselben Hosts zur selben Zeit. Rund zehn Tage später ging ein einzelner Capture sofort durch.

---

## 7. Umgebungsvariablen

Nur Namen und Bedeutung — **nie Werte**. Die Werte stehen in der Konfiguration des Betriebs
(Rechte 600, außerhalb des Repos). Defaults kommen aus den Settings-Klassen im Code.

### Datenbank und WORM (alle Kommandos)

| Variable | Zweck | Herkunft |
|---|---|---|
| `WORTLAUT_DB_DSN` | Verbindung zur Datenbank, Treiber `postgresql+asyncpg` | wird im Betrieb in der Compose-Datei aus Benutzer, Passwort und Datenbankname zusammengesetzt |
| `WORTLAUT_WORM_ENDPOINT` | Host:Port des Objektspeichers, ohne Schema | Compose-Netz |
| `WORTLAUT_WORM_ACCESS_KEY`, `WORTLAUT_WORM_SECRET_KEY` | Zugang zum Objektspeicher | beim Einrichten von MinIO vergeben |
| `WORTLAUT_WORM_BUCKET` | Bucket-Name (Default `wortlaut-worm`) | — |
| `WORTLAUT_WORM_SECURE` | TLS zum Objektspeicher (im Compose-Netz `false`) | — |

### Lese-API (`serve`)

| Variable | Zweck |
|---|---|
| `WORTLAUT_API_CORS_ORIGINS` | erlaubte Herkünfte, kommagetrennt, vollständige Liste |
| `WORTLAUT_API_WORKERS` | Worker-Zahl (Default 1); jeder hält einen eigenen DB-Pool |
| `WORTLAUT_API_HOST`, `WORTLAUT_API_PORT` | Bind-Adresse im Container (Defaults lassen) |

### Erfassung

| Variable | Zweck | Herkunft |
|---|---|---|
| `WORTLAUT_DIP_API_KEY` | Zugang zur DIP-API des Bundestags (`ingest`) | über das DIP-Portal des Bundestags (> TODO: genauen Bezugsweg ergänzen) |
| `WORTLAUT_ARCHIVE_IA_ACCESS_KEY`, `WORTLAUT_ARCHIVE_IA_SECRET` | Save Page Now (`capture`) | `https://archive.org/account/s3.php` |
| `WORTLAUT_ARCHIVE_WAYBACK_MIN_INTERVAL_SECONDS` | Mindestabstand zwischen Anfragen (Default 10) | — |
| `WORTLAUT_ARCHIVE_PREFLIGHT_ENABLED` | Pre-Flight vor `capture` (Default an) | — |
| `WORTLAUT_ARCHIVE_CONSECUTIVE_FAILURE_LIMIT` | Circuit-Breaker (Default 5) | — |
| `WORTLAUT_ARCHIVE_CAPTURE_COOLDOWN_CAPTURED_HOURS` / `…_FAILED_HOURS` | Abkühlzeiten von `capture` (72 / 6) | — |
| `WORTLAUT_ARCHIVE_ATTEST_MAX_CANDIDATES` | Snapshot-Abrufe je Quelle in `attest` (Default 3) | — |
| `WORTLAUT_TSA_PROFILES` | Zeitstempeldienste in Reihenfolge (Default `freetsa,sigstore`) | — |

> TODO: Wer außer dem Maintainer Zugang zu den Werten hat, und wo sie gesichert sind.

---

## 8. Offene Fragen

> TODO: **Überwachung.** Niemand wird benachrichtigt, wenn die API steht oder ein Lauf scheitert —
> auch nicht, wenn die Sicherung oder der Restore-Test fehlschlägt. Geplant: Benachrichtigung per
> Mail über den Webserver des Projekts.

> TODO: **Regelmäßige Läufe.** Alle Erfassungs-Läufe werden heute von Hand angestoßen. Wann,
> wie oft und von wem, ist nicht festgelegt.

> TODO: **Veröffentlichung.** Wann der Tunnel kommt und die API öffentlich wird (Zielzustand in
> `deploy.md`).

> TODO: **Gegenlesen (#91, AC6).** Eine dritte Person liest diese Seite gegen und markiert jede
> Stelle, an der sie hängen bleibt.
