# Deployment — die Read-API auf einem Dedicated fahren

> Diese Seite beschreibt den **Zielzustand**: wie der Dienst aus dem veröffentlichten
> Image reproduzierbar hochkommt. Wie der Betrieb heute von Hand läuft, gehört in
> `docs/betrieb.md` (Issue #91) — das ist bewusst ein eigenes Dokument.
>
> Vorlagen: [`deploy/compose.example.yml`](../deploy/compose.example.yml) und
> [`deploy/env.example`](../deploy/env.example).

## Was hier läuft

Vier Dienste: die **Lese-API** aus dem prod-Image, **Postgres mit pgvector**, ein
**Object-Lock-fähiger Objektspeicher** (WORM) und ein **Tunnel** nach außen.

Die API bekommt **keinen offenen Port am Host**. Der Zugang läuft über den Tunnel, die
Zugangskontrolle über Cloudflare Access — die Anwendung selbst kennt keine Auth
(Spec 0081 §2).

## Vier Dinge, die man vorher wissen muss

**1. `serve` migriert nicht.** `upgrade_head` läuft in `ingest` und `timestamp`, nicht im
Serving-Entrypoint. Auf einer frischen Datenbank startet `serve` zwar, aber `/readyz`
bleibt rot, weil das Schema fehlt. Der Bootstrap unten erledigt das.

**2. Den WORM-Bucket niemals von Hand anlegen.** Object-Lock ist **nur bei der
Bucket-Erstellung** setzbar. Ein manuell erzeugter Bucket sieht identisch aus, hat die
Unveränderbarkeits-Garantie aber still verloren — und damit die Beweiskette. Der Bootstrap
legt ihn korrekt an; existiert er bereits, passiert nichts.

**3. Das Image auf einen Commit pinnen.** `develop` und `latest` wandern bei jedem Push.
Wer darauf läuft, startet nach einem Neustart unbemerkt eine andere Version.

**4. `WORTLAUT_API_CORS_ORIGINS` ist eine vollständige Liste, keine Ergänzung.** Wer sie
setzt und die Produktionsdomain vergisst, sperrt sie aus. Kommagetrennt, nicht JSON, und
**ohne** abschließenden Schrägstrich — der Origin-Vergleich ist exakt.

## Erstinbetriebnahme

**Schritt 1 — Konfiguration ablegen.** Außerhalb des Repos, Rechte 600:

```
cp deploy/env.example /srv/wortlaut/.env
chmod 600 /srv/wortlaut/.env
# Werte eintragen
```

**Schritt 2 — Datenbank und Objektspeicher hochfahren:**

```
docker compose --env-file /srv/wortlaut/.env -f compose.yml up -d db worm
```

**Schritt 3 — Bootstrap: Schema anlegen und Bucket erzeugen.**

Es gibt (noch) kein eigenes `migrate`-Kommando. Der Weg führt über den
Zeitstempel-Lauf im Trockenmodus — der migriert, legt den Bucket an und beendet sich,
**ohne** etwas zu verändern:

```
docker compose --env-file /srv/wortlaut/.env -f compose.yml \
  run --rm api python -m wortlaut timestamp --dry-run
```

Erwartete Ausgabe: eine Zeile `pending=<n> dry_run=True`, Exit 0. Damit ist bewiesen,
dass Datenbank **und** Objektspeicher erreichbar sind und das Schema steht — ein
Verbindungstest und die Migration in einem Schritt.

**Schritt 4 — API und Tunnel starten:**

```
docker compose --env-file /srv/wortlaut/.env -f compose.yml up -d api tunnel
```

**Schritt 5 — prüfen:**

```
docker compose -f compose.yml exec api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/readyz').read())"
```

`{"status":"ready"}` heißt: Prozess läuft **und** Datenbank antwortet.

## Die beiden Health-Endpunkte unterscheiden sich absichtlich

| Endpunkt | Prüft | Verwendung |
|---|---|---|
| `/healthz` | nur, dass der Prozess lebt — **kein** DB-Zugriff | Container-Healthcheck, Neustart-Entscheidung |
| `/readyz` | eine echte, billige DB-Abfrage; **503** wenn nicht bereit | Tunnel/Loadbalancer: darf Verkehr kommen? |

Ein Dienst, der `/healthz` mit 200 und `/readyz` mit 503 beantwortet, ist **gesund, aber
nicht bereit** — typisch, wenn die Datenbank noch hochfährt. Kein Grund für einen Neustart.

Beide geben im Fehlerfall **keine Interna** preis, auch nicht die Fehlerursache.

## Aktualisieren

```
# neuen SHA in /srv/wortlaut/.env eintragen, dann:
docker compose --env-file /srv/wortlaut/.env -f compose.yml up -d api
```

Bringt eine Version neue Migrationen mit, läuft vorher der Bootstrap aus Schritt 3.

## Rollback

Den vorherigen SHA in die `.env` eintragen und `up -d api` erneut ausführen. **Deshalb
den alten Wert notieren, bevor man ihn überschreibt.**

Ein Rollback fährt nur die Anwendung zurück, **nicht** die Datenbank. Migrationen sind
additiv angelegt; ein Rückwärtsschritt über eine Migration hinweg ist nichts, was man
nebenbei macht.

## Erfassungs-Läufe

**Vorbedingung: Internet-Archive-Zugangsdaten.** Ohne
`WORTLAUT_ARCHIVE_IA_ACCESS_KEY` und `WORTLAUT_ARCHIVE_IA_SECRET` in der `.env` bricht
jeder Ingest-Lauf sofort mit **Exit 2** ab, bevor die erste Quelle geholt wird. Das ist
Absicht: Save Page Now lehnt anonyme Aufrufe mit 401 ab, und ohne Fremdarchivierung
entsteht kein Insert — ein Lauf ohne Schlüssel würde nur Zeit verbrennen und eine
irreführende Fehlerliste erzeugen. Die Schlüssel entstehen unter
`https://archive.org/account/s3.php`; ein Konto genügt, sie sind kostenlos. Nur
`--dry-run` kommt ohne aus.

Ingest und Zeitstempel laufen als einmalige Kommandos, nicht als Dienst:

```
docker compose --env-file /srv/wortlaut/.env -f compose.yml \
  run --rm api python -m wortlaut ingest --since 2024-01-01 --limit 5

docker compose --env-file /srv/wortlaut/.env -f compose.yml \
  run --rm api python -m wortlaut timestamp
```

**Immer erst mit kleinem `--limit`.** Erst wenn ein solcher Lauf `archive_failed=0`
meldet, lohnt der volle Durchgang. Der Pre-Flight-Check prüft vorab, ob die
Zugangsdaten akzeptiert werden und der Archivdienst antwortet — er existiert, weil ein
Vollbackfill schon einmal an einem Ausfall des Internet Archive gescheitert ist und
dabei 157 Quellen verlor. Er setzt bewusst **keinen** Probe-Capture ab: Ein einzelner
Capture pro Lauf würde nur ein Tageskontingent verbrauchen und wäre, sobald die
Probe-URL ihr Limit erreicht, dauerhaft rot — ohne dass mit dem Dienst etwas wäre.

Rechnen Sie mit **rund einer halben Minute pro Quelle**. Save Page Now nimmt einen
Auftrag nur entgegen und meldet den Abschluss später; der Lauf wartet darauf, weil die
Snapshot-URL erst dann feststeht.

## Logs

```
docker compose -f compose.yml logs -f api
docker compose -f compose.yml logs --since 1h api
```

Konfigurationsfehler melden sich als **Exit 2** mit den betroffenen Variablennamen —
**ohne** deren Werte, damit keine Zugangsdaten im Log landen.

## Was hier bewusst fehlt

Backups, Monitoring und Alarmierung sind nicht Teil dieser Seite. Sie gehören zum
Betriebs-Epic (#90) und brauchen eigene Entscheidungen.
