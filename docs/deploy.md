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

Ingest und Zeitstempel laufen als einmalige Kommandos, nicht als Dienst:

```
docker compose --env-file /srv/wortlaut/.env -f compose.yml \
  run --rm api python -m wortlaut ingest --since 2024-01-01 --limit 5

docker compose --env-file /srv/wortlaut/.env -f compose.yml \
  run --rm api python -m wortlaut timestamp
```

**Immer erst mit kleinem `--limit`.** Erst wenn ein solcher Lauf `archive_failed=0`
meldet, lohnt der volle Durchgang. Der Pre-Flight-Check prüft vorab, ob die
Fremdarchive überhaupt antworten — er existiert, weil ein Vollbackfill schon einmal
an einem Ausfall des Internet Archive gescheitert ist und dabei 157 Quellen verlor.

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
