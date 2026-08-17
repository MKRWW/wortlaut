# Increment-Spec: Serving-ASGI-Entrypoint — Read-API im Container fahrbar (#81)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #81 · **Status:** Reviewed · **Phase/Layer:** phase/1-mvp · `serving` · CLI
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#43** (`create_app`), **#64** (Ingest-Composition-Root), **#66** (prod-Image), **#76**
  (Zeitstempel in `/verify`).

## 0. Ausgangslage

`create_app(sessionmaker, worm) -> FastAPI` existiert und ist getestet — aber **niemand kann sie
starten**. Das Image kennt nur `python -m wortlaut ingest|timestamp`; es gibt keinen Weg von
„ENV im Container" zu „ASGI-Server, der antwortet". Dieser Increment schließt genau diese Lücke
und **nichts sonst**.

### 0a. Vorklärung 1: `uvicorn` ist noch **gar keine** Dependency

Gemessen (`grep -c uvicorn pyproject.toml uv.lock` → beide `0`): weder Runtime- noch Dev-Dependency.
Das prod-Image baut mit `uv sync --frozen --no-dev`, also muss `uvicorn` in `[project].dependencies`
(nicht `dev`) und `uv.lock` muss neu erzeugt werden. **Das macht der Architekt** (§10) — der Coder
darf kein `uv` aufrufen.

### 0b. Vorklärung 2: `workers` ist die Falle in dieser Aufgabe

uvicorn ignoriert `workers`, wenn ihm eine **App-Instanz** statt eines **Import-Strings** übergeben
wird. Ein naives `uvicorn.run(create_app(...), workers=n)` würde also `WORTLAUT_API_WORKERS=4`
stillschweigend auf 1 kappen — ein *silent cap*, und damit genau die Klasse Halbumsetzung, die der
Review-Katalog (CLAUDE.md §2.1) mit Veto belegt. Die Konsequenz steht in §4.1.

## 1. Ziel

Ein Betreiber startet die Read-API mit **einem** Kommando (`python -m wortlaut serve`) aus dem
vorhandenen Image, verdrahtet **ausschließlich über ENV**, und ein Tunnel/Deploy davor kann an
einem Health-Endpunkt erkennen, ob die Instanz Verkehr bekommen soll.

## 2. Nicht-Ziele (Scope-Grenze)

- **Keine Änderung am Endpunkt-Verhalten aus #43.** `create_app`s Signatur bleibt **unverändert**;
  `/v1/*` verhält sich byte-gleich. Additiv sind nur die zwei Health-Routen.
- **Keine Migration und kein `ensure_bucket` im Serve-Pfad** (§4.4) — die Read-API ist read-only.
- **Keine Auth, kein Rate-Limit, kein TLS, kein CORS-Umbau** — macht Cloudflare (Access/Tunnel).
- **Kein Deploy, keine compose-Datei, kein cloudflared-Setup** (Ops, privat).
- **Kein Schreibpfad**, kein neuer Query, kein neues Feld in den Antwort-Schemas der `/v1/*`.
- **Kein `uvicorn[standard]`** (§4.6), **kein Gunicorn**, **kein Reload-/Dev-Modus** im Image.
- **Keine strukturierte Logging-/Metrics-Schicht** — eigener Increment, wenn gewünscht.

## 3. Betroffene Interfaces / Öffentliche Signaturen

```python
# ── NEU: src/wortlaut/serving/settings.py ───────────────────────────────
class ApiSettings(BaseSettings):
    """Bind-Adresse und Worker-Zahl des ASGI-Servers (Prefix WORTLAUT_API_). Keine Secrets."""
    model_config = SettingsConfigDict(env_prefix="WORTLAUT_API_")
    host: str = "0.0.0.0"                              # ENV WORTLAUT_API_HOST
    port: int = Field(default=8000, ge=1, le=65535)    # ENV WORTLAUT_API_PORT
    workers: int = Field(default=1, ge=1)              # ENV WORTLAUT_API_WORKERS

# ── NEU: src/wortlaut/serving/asgi.py (ASGI-Composition-Root) ───────────
def create_asgi_app() -> FastAPI:
    """ENV → DbSettings/WormSettings → Engine → Sessionmaker → MinioWormStore → create_app.

    FACTORY, kein Modul-Level-``app``: wird je uvicorn-Worker-Prozess einmal aufgerufen.
    """

# ── GEÄNDERT: src/wortlaut/serving/schemas.py (additiv) ─────────────────
class HealthStatus(BaseModel):
    status: Literal["ok", "ready"]     # bewusst KEINE Interna (R-SEC-01)

# ── GEÄNDERT: src/wortlaut/serving/app.py (additiv, in create_app) ──────
# GET /healthz -> HealthStatus(status="ok")          — Liveness, KEIN DB-Zugriff
# GET /readyz  -> HealthStatus(status="ready") | 503 — Readiness, SELECT 1 mit Timeout

# ── GEÄNDERT: src/wortlaut/cli.py ───────────────────────────────────────
# neues Subcommand "serve" (ohne Flags, ENV-only) -> _run_serve() -> int
```

- **Layering (R-ARCH-02):** `serving.asgi` ist der **äußerste** Layer und darf `serving`+`store`
  konsumieren; `serving → store` ist seit #43 ausdrücklich erlaubt (Kommentar im Contract).
  `wortlaut.cli` steht in **keiner** `source_modules`-Liste der bestehenden Contracts — der
  Composition-Root braucht daher **keine Aufweichung** (§4.5, beantwortet die dritte
  Refinement-Frage aus #81).

## 4. Design (kurz) — die drei Refinement-Entscheidungen

**4.1 `python -m wortlaut serve` (gewählt) — implementiert über einen Import-String auf die
Factory.** Beide im Issue genannten Varianten werden damit *ein* Mechanismus statt zwei:

- Der **unterstützte, dokumentierte** Weg ist das Subcommand. Begründung: identisches
  Entrypoint-Muster wie `ingest`/`timestamp` (#64), identische ENV-Fehlerbehandlung (**Exit 2**),
  eine einzige Docker-CMD-Story (`command: ["python","-m","wortlaut","serve"]`), und die Pflicht-ENV
  wird **einmal im Elternprozess** geprüft statt N-mal als Worker-Traceback.
- Intern ruft `serve` **nicht** `uvicorn.run(app_instance)`, sondern
  `uvicorn.run("wortlaut.serving.asgi:create_asgi_app", factory=True, …)`. Damit wirkt `workers`
  **wirklich** (§0b) — und jeder Worker-Prozess baut seine **eigene** Engine samt eigenem
  asyncpg-Pool, was ohnehin die einzig korrekte Variante ist (ein Pool darf nicht über
  `fork` geteilt werden).
- Nebeneffekt, bewusst mitgenommen: `uvicorn wortlaut.serving.asgi:create_asgi_app --factory`
  funktioniert dadurch ebenfalls, ohne dass es ein zweiter *gepflegter* Pfad wäre.

**4.2 Factory statt Modul-Level-`app`.** Ein `app = create_asgi_app()` auf Modulebene würde beim
**Import** Settings lesen und eine Engine bauen: jeder Import (Test, `--help`, Tooling) bräuchte
dann vollständige ENV, und ein Konfigurationsfehler käme als Import-Traceback statt als saubere
Meldung. Die Factory hat **keine Import-Zeit-Nebenwirkungen**.

**4.3 Health: zwei getrennte Endpunkte — `/healthz` (Liveness) und `/readyz` (Readiness).**

| Endpunkt | Prüft | Antwort |
|---|---|---|
| `GET /healthz` | nichts — nur „der Prozess lebt" | immer **200** `{"status":"ok"}` |
| `GET /readyz` | **DB**: `SELECT 1`, Timeout 2 s | **200** `{"status":"ready"}` / **503** `{"detail":"not_ready"}` |

Warum getrennt und nicht ein einziger DB-prüfender `/healthz`: ein Liveness-Check, der die DB
anfasst, **killt den Container bei jedem DB-Ausfall** (Restart-Policy sieht „unhealthy") — obwohl
der Prozess kerngesund ist und nur die Abhängigkeit fehlt. Getrennt bedeutet: Instanz aus der
Rotation nehmen (503), aber nicht neu starten.

**Bewusst NICHT geprüft: WORM/MinIO.** MinIO wird nur von `/v1/spans/{id}/verify` gebraucht. Ein
MinIO-Blip würde bei Mitprüfung die **komplette** Read-API (Suche, Spans, Kontext) aus der Rotation
nehmen, obwohl sie voll funktioniert. Ein MinIO-Ausfall gehört an die Stelle, an der er wirkt:
in die `/verify`-Antwort. *(Diese Entscheidung ist bewusst und darf nicht „nachgebessert" werden.)*

**4.4 Serve macht kein Bootstrap.** Kein `upgrade_head`, kein `ensure_bucket`. Gründe: (a) `workers=N`
würde N gleichzeitige Migrationen starten; (b) ein read-only Dienst darf das Schema nicht ändern;
(c) `ensure_bucket` ist ein **Schreib**-Zugriff auf den WORM-Speicher. Migration bleibt Sache des
`ingest`-/`timestamp`-Laufs. AC2 nagelt das fest.

**4.5 import-linter: eine Erweiterung ist nicht nötig, ein *neuer* Contract ist der Gewinn.**
Gemessen an den bestehenden Contracts: `serving → store/pipeline` ist erlaubt, und `wortlaut.cli`
kommt in keiner `source_modules`-Liste vor — der Composition-Root verletzt also nichts. Statt
aufzuweichen wird die **neue Naht gepinnt**: `wortlaut.serving` darf `wortlaut.cli` **nicht**
importieren (die Abhängigkeit zeigt vom Entrypoint zur API, nie zurück).
> **Nicht** ergänzt wird ein Contract „serving importiert nicht timestamp/archive": der wäre
> **rot**. `serving.app → pipeline.verify → timestamp.verify` ist seit #76 ein gewollter Pfad, und
> `forbidden`-Contracts greifen auch über indirekte Ketten. (Gemessen, nicht vermutet.)

**4.6 `uvicorn` schlank, nicht `[standard]`.** `uvicorn[standard]` zieht uvloop, httptools,
websockets, watchfiles, PyYAML nach — C-Extensions und Supply-Chain-Fläche für eine read-only
JSON-API hinter Cloudflare, deren Lastprofil das nicht braucht. Wer später Durchsatz misst und
braucht, macht daraus ein eigenes Increment mit Zahlen.

**4.7 Fail-fast im Elternprozess, gebaut wird im Worker.** `_run_serve` konstruiert `DbSettings()`,
`WormSettings()`, `ApiSettings()` **nur zur Validierung** und wirft sie weg; bei Fehlkonfiguration
eine Zeile nach stderr + **Exit 2** (wie `ingest`). Der echte Bau passiert je Prozess in der Factory.

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)

- [ ] **AC1** *(Composition-Root aus ENV)* — *Given* gesetzte `WORTLAUT_DB_DSN`/`WORTLAUT_WORM_*`,
      *When* `create_asgi_app()`, *Then* wird `create_app` **genau 1×** mit einem aus
      `DbSettings.dsn` gebauten Sessionmaker und einem `MinioWormStore` aus `WormSettings`
      aufgerufen, es gibt **keine** hartkodierte Config im Modul, und es wird **kein** Netz-/DB-Call
      abgesetzt (Engine ist lazy). `[unit]`
- [ ] **AC2** *(kein Bootstrap im Serve-Pfad)* — *Given* gepatchtes `uvicorn.run`, *When*
      `main(["serve"])`, *Then* wurde `upgrade_head` **0×** und `MinioWormStore.ensure_bucket`
      **0×** aufgerufen. `[unit]`
- [ ] **AC3** *(uvicorn-Verdrahtung — Worker wirken wirklich, §0b)* — *Given*
      `WORTLAUT_API_HOST=1.2.3.4`, `WORTLAUT_API_PORT=9999`, `WORTLAUT_API_WORKERS=3`, *When*
      `main(["serve"])`, *Then* wurde `uvicorn.run` **genau 1×** aufgerufen, mit dem **Import-String**
      `"wortlaut.serving.asgi:create_asgi_app"`, `factory=True`, `host="1.2.3.4"`, `port=9999`,
      `workers=3`, und der Rückgabewert von `main` ist **0**. `[unit]`
- [ ] **AC4** *(Fehlkonfiguration ⇒ Exit 2, kein Leak)* — *Given* `WORTLAUT_WORM_ACCESS_KEY` fehlt,
      während `WORTLAUT_DB_DSN` ein Passwort enthält, *When* `main(["serve"])`, *Then* ist der
      Rückgabewert **2**, `uvicorn.run` wurde **0×** aufgerufen, und die Ausgabe (stdout+stderr)
      enthält **weder** das DSN-Passwort **noch** einen WORM-Secret-Wert. `[unit]`
- [ ] **AC5** *(Liveness ohne DB)* — *Given* eine App, deren Session bei jeder Query wirft, *When*
      `GET /healthz`, *Then* **200** mit `{"status":"ok"}` und die Session-Dependency wurde **0×**
      benutzt. `[unit]`
- [ ] **AC6** *(Readiness rot — sauber und ohne Interna)* — *Given* eine Session, deren `execute`
      einen `SQLAlchemyError` wirft, *When* `GET /readyz`, *Then* **503**, und der Response-Body
      enthält **weder** DSN, Host, Passwort **noch** den Exception-Text. *Und:* *Given* eine
      Session, deren `execute` 30 s blockiert, *Then* antwortet `/readyz` in **< 5 s** mit **503**
      (Timeout greift, der Probe hängt nicht). `[unit]`
- [ ] **AC7** *(echte Endpunkte über den neuen Root)* — *Given* ein echtes Postgres (Testcontainer,
      `upgrade_head`) und ENV, die darauf zeigt, *When* die App über **`create_asgi_app()`** gebaut
      und via `ASGITransport` befragt wird, *Then* liefert `GET /readyz` **200**,
      `GET /v1/search?q=…` **200** mit `total`-Feld, und `GET /v1/spans/<unbekannte-uuid>` **404**.
      `[integration]`
- [ ] **AC8** *(Container-Smoke, analog zum docker-Job)* — *Given* das gebaute Image, *When*
      `docker run … python -m wortlaut serve` mit Dummy-ENV (DB absichtlich **nicht** erreichbar),
      *Then* antwortet `GET /healthz` mit **200** `{"status":"ok"}`, `GET /readyz` mit **503**, und
      `python -m wortlaut serve --help` endet mit Exit **0**. `[ci]`
- [ ] **AC9** *(KI-frei + Layering + kein Secret)* — *When* CI, *Then* ist
      `scripts/check_no_llm_output.py` grün **und** zählt `serving/asgi.py` + `serving/settings.py`
      mit (Datei-Anzahl steigt), `lint-imports` ist grün **inklusive** des neuen Contracts
      `serving ↛ cli`, und der bestehende Image-Check „keine `WORTLAUT_*(KEY|SECRET|PASSWORD)`-ENV
      im Image" bleibt grün. `[ci]`
- [ ] **AC10** *(kein Regress)* — *Then* bleiben alle #43-Tests
      (`tests/integration/test_serving_api.py`, `tests/unit/test_serving_helpers.py`) **unverändert**
      grün, die Signatur von `create_app` ist unverändert, CI ist vollständig grün und der PR hat
      **0 neue Sonar-Issues**. `[ci]`

## 6. Testplan (Test-zu-AC-Mapping)

**Unit (rein, keine Netz-/DB-Calls — R-TEST-03):**
- `tests/unit/test_serving_asgi.py` — `test_factory_wires_from_env` → **AC1** ·
  `test_factory_uses_dsn_from_env` → AC1 · `test_factory_no_connection_on_build` → AC1
- `tests/unit/test_cli_serve.py` — `test_serve_passes_import_string_and_factory` → **AC3** ·
  `test_serve_reads_host_port_workers_from_env` → AC3 · `test_serve_skips_bootstrap` → **AC2** ·
  `test_serve_missing_env_exits_2` → AC4 · `test_serve_error_leaks_no_secret` → AC4
- `tests/unit/test_serving_health.py` — `test_healthz_ok_without_db` → **AC5** ·
  `test_readyz_503_on_db_error` → AC6 · `test_readyz_body_has_no_internals` → AC6 ·
  `test_readyz_times_out_instead_of_hanging` → AC6

**Integration (Testcontainers, echtes Postgres):**
- `tests/integration/test_serving_asgi.py` — `test_endpoints_via_composition_root` → **AC7**
  (nutzt `fresh_pg_dsn` + `upgrade_head` wie `test_serving_api.py`; ENV via `monkeypatch.setenv`)

**CI-Gates:** AC8 (docker-Job, Serve-Smoke) · AC9 (security + architecture) · AC10 (alle Jobs).

**Invarianten (R-DATA):** unverändert — dieser Increment hat **keinen** Schreibpfad; kein
UPDATE/DELETE, kein neuer WORM-Zugriff.

## 7. Recht / Security

- **R-SEC-01 (keine Secrets):** Alle Zugangsdaten kommen aus ENV; im Code/Image steht kein Wert.
  Die Fehlermeldung bei Fehlkonfiguration gibt **keine ENV-Werte** aus (AC4 prüft das gegen ein
  Passwort im DSN). Health-Bodies enthalten keine Interna (AC6). Der Dummy-ENV-Block im
  CI-Smoke enthält bewusst triviale Nicht-Secrets (`dummy`).
- **R-SEC-04 (KI-frei):** Der neue Code liegt unter `src/wortlaut/serving/**` und fällt damit
  **zusätzlich** unter `check_no_llm_output` — der Composition-Root steht bewusst dort, wo das Gate
  hinschaut, statt daneben.
- **R-CORE-01:** Ausgabe bleibt wörtlicher DB-Span; die Health-Endpunkte geben zwei Konstanten
  zurück und fassen keinen Span an.
- **Bind auf `0.0.0.0`:** im Container der einzig von außen erreichbare Bind; `127.0.0.1` als
  Default wäre ein stiller Betriebsfehler (Container antwortet nie). Die Exposition regelt das
  Deployment (Cloudflare Tunnel, kein öffentlicher Port). Bandit B104 ist im CI advisory; das
  Sonar-PR-Gate zählt `api/issues/search`, keine Security-Hotspots.
- **Kein neuer Angriffspfad:** keine Auth-Logik, kein neuer externer Fetch, kein SSRF-Vektor,
  kein Pickle. `/readyz` ist unauthentifiziert — es gibt genau zwei mögliche Bodies und keinen
  Parameter, also keine Informationspreisgabe über den Zustand hinaus.

## 8. Risiken & offene Fragen

- **🟠 `workers > 1` vervielfacht DB-Verbindungen.** Jeder Worker hält seinen eigenen Pool
  (SQLAlchemy-Default `pool_size=5`, `max_overflow=10`). `WORTLAUT_API_WORKERS=4` kann also bis
  60 Verbindungen ziehen. Default bleibt deshalb **1**; wer hochdreht, muss Postgres'
  `max_connections` kennen. Notiert, nicht gelöst (Pool-Tuning wäre ein eigener Increment).
- **🟡 Kein `engine.dispose()` beim Shutdown.** Bewusst: die Engine lebt so lange wie der Prozess,
  es gibt **keinen Schreibpfad** und damit nichts zu flushen; uvicorns Shutdown beendet den Prozess
  und Postgres räumt die Verbindungen ab. Ein `@app.on_event("shutdown")` wäre deprecated, ein
  `lifespan`-Parameter würde `create_app` anfassen — beides wäre teurer als der Nutzen.
- **🟡 `/readyz` erzeugt Last, wenn ein Probe zu eng getaktet wird.** `SELECT 1` ist billig, aber
  ein 1-Sekunden-Probe × N Instanzen ist Grundlast. Taktung ist Deploy-Sache (Nicht-Ziel).
- **🟡 gitleaks am CI-Smoke.** Der Dummy-ENV-Block enthält `WORTLAUT_WORM_SECRET_KEY=dummy`.
  Niedrige Entropie, sollte keinen Generic-Rule-Treffer geben — der Architekt prüft den
  `security`-Job im PR und weicht sonst auf einen anderen Platzhalter aus.
- **🟢 Offen (nicht Teil dieses Increments):** strukturierte Logs/Access-Log-Format, Metrics,
  Graceful-Drain-Zeit, Pool-Tuning. Alles eigene Tickets, sobald die Demo (#44) Betrieb sieht.

## 9. Definition of Done (Verweis)

[../docs/rules.md](../docs/rules.md) DoD: alle AC grün, alle Gates grün, Review durch Architekt,
Invarianten gewahrt, kein Secret, keine Live-Calls im CI-Gate. PR referenziert **#81**
(`Closes #81`) gegen `develop`.

---

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu anlegen:**
- `src/wortlaut/serving/settings.py`        — `ApiSettings`
- `src/wortlaut/serving/asgi.py`            — `create_asgi_app()` (Composition-Root)
- `tests/unit/test_serving_asgi.py`         — AC1
- `tests/unit/test_cli_serve.py`            — AC2, AC3, AC4
- `tests/unit/test_serving_health.py`       — AC5, AC6
- `tests/integration/test_serving_asgi.py`  — AC7

**Ändern (chirurgisch, nur additiv):**
- `src/wortlaut/serving/schemas.py`         — `HealthStatus` **anhängen**
- `src/wortlaut/serving/app.py`             — zwei Health-Routen **in** `create_app` ergänzen
- `src/wortlaut/cli.py`                     — `serve`-Subparser + `_run_serve()`

> **NICHT anfassen:** `pyproject.toml`, `uv.lock`, `.importlinter`, `Dockerfile`,
> `.github/workflows/**`, `scripts/**`, `migrations/**`, `src/wortlaut/store/**`,
> `src/wortlaut/pipeline/**`, `src/wortlaut/archive/**`, `src/wortlaut/timestamp/**`,
> `src/wortlaut/ingest/**`, `tests/integration/test_serving_api.py`,
> `tests/unit/test_serving_helpers.py`, `tests/unit/test_cli.py`, alle übrigen Tests.
>
> **Architekt zieht nach (nicht der Coder):** `pyproject.toml` + `uv.lock` (uvicorn, §0a),
> `.importlinter` (neuer Contract, §4.5), `.github/workflows/ci.yml` (Serve-Smoke, AC8),
> `Dockerfile` (`EXPOSE 8000`).

## 11. Umsetzungsdetails je Datei

### `src/wortlaut/serving/settings.py` (neu)

Muster exakt wie `src/wortlaut/store/settings.py` (lesen!). Modul-Docstring: „API-Einstellungen aus
der Umgebung (Prefix ``WORTLAUT_API_``)". Klasse `ApiSettings(BaseSettings)` mit
`model_config = SettingsConfigDict(env_prefix="WORTLAUT_API_")` und den drei Feldern aus §3.
`port`/`workers` als `Field(...)` mit den genannten Grenzen (`from pydantic import Field`) — eine
kaputte ENV muss zu **Exit 2** führen, nicht zu einem halb gestarteten Server. Beim `host`-Feld
ein Kommentar, **warum** `0.0.0.0` (§7). **Kein** `from __future__ import annotations` nötig, aber
erlaubt; keine Secrets, keine DSN in dieser Klasse.

### `src/wortlaut/serving/asgi.py` (neu)

```python
def create_asgi_app() -> FastAPI:
    engine = create_async_engine_from(DbSettings())
    return create_app(make_sessionmaker(engine), MinioWormStore(WormSettings()))
```
- Imports: `from fastapi import FastAPI`, `from wortlaut.serving.app import create_app`,
  `from wortlaut.store.db import create_async_engine_from, make_sessionmaker`,
  `from wortlaut.store.settings import DbSettings, WormSettings`,
  `from wortlaut.store.worm import MinioWormStore`.
- **Kein** Modul-Level-`app`, **kein** `upgrade_head`, **kein** `ensure_bucket`, **kein**
  `asyncio.run`, **keine** Logging-Konfiguration, **kein** try/except (eine fehlende ENV soll hier
  laut hochgehen — die freundliche Meldung macht `_run_serve`).
- Der Modul-Docstring ist der Wert der Datei: er nennt (a) warum **Factory** statt Modul-`app`
  (§4.2) und (b) dass je Worker-Prozess **eine eigene Engine** entsteht (§4.1).

### `src/wortlaut/serving/schemas.py` (ändern, anhängen)

`HealthStatus(BaseModel)` mit `status: Literal["ok", "ready"]` **ans Ende** anhängen
(`from typing import Literal` ergänzen). Docstring: enthält bewusst keine Interna (R-SEC-01).
Bestehende Klassen **unverändert**.

### `src/wortlaut/serving/app.py` (ändern, additiv)

Modulkonstante neben `_WORD`: `_READY_TIMEOUT_SECONDS = 2.0`.
Zusätzliche Imports: `import asyncio`, `from sqlalchemy import text`,
`from sqlalchemy.exc import SQLAlchemyError`, `HealthStatus` aus `schemas`.
**Direkt nach** der Zeile `SessionDep = Annotated[...]` und **vor** `@app.get("/v1/search")`:

```python
    @app.get("/healthz")
    async def healthz() -> HealthStatus:
        """Liveness: nur 'der Prozess lebt' — bewusst OHNE DB-Zugriff (§4.3)."""
        return HealthStatus(status="ok")

    @app.get("/readyz", responses={503: {"description": "Abhaengigkeit nicht bereit"}})
    async def readyz(session: SessionDep) -> HealthStatus:
        """Readiness: eine echte, billige DB-Abfrage. 503 ohne Interna (R-SEC-01)."""
        try:
            async with asyncio.timeout(_READY_TIMEOUT_SECONDS):
                await session.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError, TimeoutError) as e:
            raise HTTPException(status_code=503, detail="not_ready") from e
        return HealthStatus(status="ready")
```
- `detail` ist **exakt** `"not_ready"` — **nie** `str(e)` (das würde Host/DSN durchreichen).
- Die Exception-Liste ist **genau diese drei** (SQLAlchemy wickelt DBAPI-Fehler ein, `OSError`
  deckt rohe Socket-/DNS-Fehler, `TimeoutError` ist der eigene Timeout). **Kein** blankes
  `except Exception`.
- **Sonst nichts** in dieser Datei ändern: `_find_match`, `_span_result`, alle `/v1/*`-Routen,
  die CORS-Middleware und die Signatur von `create_app` bleiben **wörtlich** wie sie sind.
- Der Hinweis im Modul-Docstring zu „kein `from __future__ import annotations`" gilt weiter —
  **nicht** hinzufügen.

### `src/wortlaut/cli.py` (ändern)

1. `import uvicorn` in den Third-Party-Importblock; `from wortlaut.serving.settings import ApiSettings`
   zu den wortlaut-Imports.
2. In `main`: `p_serve = subparsers.add_parser("serve")` — **keine** Argumente (ENV-only).
3. Die Subcommand-Prüfung erweitern, sodass auch `serve` gültig ist, und die Fehlermeldung auf
   `"Fehler: Subcommand 'ingest', 'timestamp' oder 'serve' erforderlich"` ändern. Dispatch:
   `serve` → `return _run_serve()` **ohne** `asyncio.run` (uvicorn bringt seinen eigenen Loop mit —
   `asyncio.run` drumherum würde krachen).
4. Neue Funktion:
```python
def _run_serve() -> int:
    """Composition-Root fuer ``serve``: ENV pruefen, dann uebernimmt uvicorn.

    Die Settings werden hier NUR validiert (fail-fast, Exit 2 wie ``ingest``); gebaut
    wird je Worker-Prozess in ``wortlaut.serving.asgi.create_asgi_app`` — deshalb der
    Import-String statt einer App-Instanz (sonst ignoriert uvicorn ``workers``).
    """
    try:
        DbSettings()      # Validierung, kein toter Code: fehlende ENV -> Exit 2
        WormSettings()    # dito
        api = ApiSettings()
    except Exception as e:
        print(f"Konfiguration fehlgeschlagen: {e}", file=sys.stderr)
        return 2

    uvicorn.run(
        "wortlaut.serving.asgi:create_asgi_app",
        factory=True,
        host=api.host,
        port=api.port,
        workers=api.workers,
    )
    return 0
```
5. `_run`/`_run_timestamp`, die Summary-Zeilen, Exit-Codes und der Preflight bleiben **unangetastet**.

### Tests

**Allgemein:** keine Netz-/DB-Calls in Unit-Tests, keine echten Wartezeiten außer dem
Timeout-Test (der darf max. wenige Sekunden dauern). Pflicht-ENV immer via `monkeypatch.setenv`
setzen und mit `monkeypatch.delenv(..., raising=False)` gezielt entfernen — **nie** `os.environ`
direkt schreiben (sonst leckt der Zustand in andere Tests).

- **`tests/unit/test_serving_asgi.py` (AC1):** `monkeypatch.setattr` auf
  `wortlaut.serving.asgi.create_app` (Aufrufe mitschreiben) und auf
  `wortlaut.serving.asgi.MinioWormStore` (Fake, der die Settings festhält). Prüfen: `create_app`
  1× aufgerufen; das erste Argument ist ein `async_sessionmaker`; der Worm-Fake bekam
  `endpoint/access_key/bucket` aus der ENV. Dass kein Connect passiert, ist implizit dadurch
  belegt, dass der Test ohne laufende DB/MinIO durchläuft — **keinen** Netz-Mock bauen.
- **`tests/unit/test_cli_serve.py` (AC2–AC4):** `uvicorn.run` per
  `monkeypatch.setattr("wortlaut.cli.uvicorn.run", fake)` ersetzen (Aufrufe + kwargs sammeln).
  Für AC2 zusätzlich `wortlaut.cli.upgrade_head` durch einen Zähler ersetzen und prüfen `== 0`;
  `ensure_bucket` wird über einen `MinioWormStore`-Fake gezählt (oder: `serve` konstruiert gar
  keinen Store — dann genügt der Nachweis, dass `create_asgi_app` nicht aufgerufen wurde).
  AC4: DSN mit Passwort setzen (`postgresql+asyncpg://u:sup3rgeheim@h/db`),
  `WORTLAUT_WORM_ACCESS_KEY` löschen, `capsys` einsammeln und prüfen, dass `"sup3rgeheim"`
  **nicht** in der Ausgabe steht und rc `== 2` ist.
- **`tests/unit/test_serving_health.py` (AC5–AC6):** App per `create_app(fake_sessionmaker,
  fake_worm)` bauen und über `httpx.ASGITransport` + `AsyncClient` befragen (Muster aus
  `tests/integration/test_serving_api.py` abschauen, aber **ohne** Container). Der Fake-Sessionmaker
  liefert eine Fake-Session, deren `execute` je nach Test (a) `SQLAlchemyError` wirft, (b) 30 s
  schläft (`await asyncio.sleep(30)`), (c) gar nicht aufgerufen werden darf (`/healthz`). Für den
  Timeout-Test die Wanduhr messen (`time.monotonic()`) und `< 5` behaupten.
- **`tests/integration/test_serving_asgi.py` (AC7):** `pytestmark = pytest.mark.integration`.
  `fresh_pg_dsn`-Fixture + `await upgrade_head(dsn)`; ENV via `monkeypatch.setenv`
  (`WORTLAUT_DB_DSN` = DSN, WORM-Werte als Dummy — MinIO wird auf diesen Pfaden **nicht**
  angefasst); App über `create_asgi_app()`; dann `/readyz` → 200, `/v1/search?q=test` → 200 mit
  `total` im JSON, `/v1/spans/<zufällige uuid4>` → 404. Danach die Engine der App **nicht** manuell
  schließen müssen — der Test darf `engine.dispose()` weglassen (Prozess-Lebensdauer, §8).

## 12. Do-NOT (hart)
- KEINE git-, docker-, uv-, npm-, alembic- oder pytest-Befehle ausführen — nur das in §13.
- KEINE anderen als die in §10 genannten Dateien anlegen oder ändern. **Insbesondere nicht**
  `pyproject.toml`, `uv.lock`, `.importlinter`, `Dockerfile`, `.github/workflows/**`.
- KEIN Modul-Level-`app`-Objekt in `asgi.py`; KEIN `uvicorn.run(<App-Instanz>)` (das kappt
  `workers` still, §0b); KEIN `--reload`.
- KEIN `upgrade_head`, KEIN `ensure_bucket`, KEIN Schreibzugriff im Serve-Pfad.
- KEINE Änderung an der Signatur von `create_app`, an den `/v1/*`-Routen, an der CORS-Middleware
  oder an bestehenden Schemas.
- KEIN MinIO/WORM-Check in `/readyz` (§4.3). KEIN `str(e)`/Exception-Text/DSN/Host in einer
  HTTP-Antwort oder Log-Zeile. KEIN blankes `except Exception` in `/readyz`.
- KEINE Netz-/DB-Calls in Unit-Tests. KEIN `os.environ`-Schreiben ohne `monkeypatch`.
- KEINE erfundenen Feld-/Parameternamen — die genannten bestehenden Dateien vorher lesen.

## 13. Abschluss (und NUR das an Kommandos ausführen)
- `git status --porcelain` ausgeben.
