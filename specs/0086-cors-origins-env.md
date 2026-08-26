# Increment-Spec: CORS-Origins aus der Umgebung (#86)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #86 · **Status:** Reviewed · **Phase/Layer:** phase/1-mvp · `serving`
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#43** (`create_app`), **#81** (ENV-getriebener Serving-Entrypoint, `ApiSettings`).
- Entblockt **#44** (Demo-Unterseite) für den Bau gegen ein echtes Staging-API.

## 0. Ausgangslage

`create_app` verdrahtet die erlaubte Herkunft hart:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://wortlaut.io"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
```

Genau eine Herkunft, im Code. Das ist der **letzte Rest im Serving-Layer, der nicht aus der
Umgebung kommt** — seit #81 laufen Bind, Port, Worker, DB und WORM über ENV. Folge: kein
`localhost` beim Entwickeln, keine Staging-Domain, kein Vorschau-Deploy. Jede zusätzliche
Herkunft wäre heute ein Code-Change plus Release statt einer Variablen.

### 0a. Vorklärung: die Falle ist das Listen-Parsing, nicht die Middleware

Ein naives `cors_origins: list[str]` in `ApiSettings` **funktioniert nicht** mit der natürlichen
Schreibweise. Gemessen gegen `pydantic-settings 2.14.2`:

| `WORTLAUT_API_CORS_ORIGINS` | `list[str]` (naiv) |
|---|---|
| *nicht gesetzt* | `['https://wortlaut.io']` ✅ |
| `https://wortlaut.io,http://localhost:8080` | **`SettingsError`** ❌ |
| `["https://wortlaut.io","http://localhost:8080"]` | `[...]` ✅ |

`EnvSettingsSource` versucht komplexe Typen **als JSON** zu dekodieren. Wer also die naheliegende
kommagetrennte Form in eine compose-Datei schreibt, bekommt einen Startfehler; funktionieren würde
nur JSON mit Anführungszeichen — betriebsfeindlich und fehleranfällig.

**Ein `field_validator(mode="before")` allein rettet das nicht** (ebenfalls gemessen): die
JSON-Dekodierung passiert in der Settings-Quelle, *bevor* irgendein Validator den Wert sieht.
Die Lösung ist die Annotation `NoDecode` — sie schaltet die Dekodierung für dieses Feld ab und
übergibt den Rohstring an den Validator. Details in §4.1.

### 0b. Vorklärung: ein Trailing Slash ist ein stiller Totalausfall

Starlettes `CORSMiddleware` vergleicht den `Origin`-Header **exakt** gegen die Allowlist. Browser
senden Origins **ohne** abschließenden Schrägstrich. Ein konfiguriertes `https://wortlaut.io/`
matcht deshalb **nie** — der Server startet sauber, antwortet sauber, und die Demo bleibt trotzdem
leer. Genau die Klasse Fehler, die man erst am Ops-Tag findet. Konsequenz in §4.2.

## 1. Ziel

Ein Betreiber erlaubt der Read-API zusätzliche Herkünfte (lokale Entwicklung, Staging) über **eine
ENV-Variable**, in der Schreibweise, die man in einer compose-Datei erwartet — und eine falsch
gesetzte Variable bricht den Start mit **Exit 2** ab, statt still eine Allowlist zu bauen, die
nichts matcht.

## 2. Nicht-Ziele (Scope-Grenze)

- **Keine Auth** — den Zugang regelt Cloudflare Access (#81).
- **Kein Schreibpfad** — `allow_methods` bleibt `["GET"]`.
- **Keine Änderung am Endpoint-Verhalten** (#43) — keine neue Route, kein geändertes Schema.
- **Kein `allow_origins=["*"]`**, auch nicht als Option. Ob eine spätere öffentliche API-Nutzung
  das will, ist eine eigene Entscheidung mit eigenem Ticket.
- **Kein `allow_credentials`** — die API kennt keine Cookies/Sessions.
- Kein Deploy/Cloudflare-Setup (Ops, privat).

## 3. Betroffene Interfaces / Öffentliche Signaturen

```python
# src/wortlaut/serving/settings.py — ApiSettings, additiv
cors_origins: Annotated[list[str], NoDecode] = ["https://wortlaut.io"]

# src/wortlaut/serving/app.py — create_app, neuer PFLICHT-Parameter (keyword-only)
def create_app(
    sessionmaker: async_sessionmaker[AsyncSession],
    worm: WormStore,
    *,
    allowed_origins: Sequence[str],
) -> FastAPI: ...
```

- **Layering (R-ARCH-02):** unverändert. Die Abhängigkeit zeigt weiter vom Composition-Root
  (`serving.asgi`, `cli`) **zur** API. `serving.app` liest **nichts** aus der Umgebung.

## 4. Design (kurz) — die drei Entscheidungen

### 4.1 `NoDecode` + zwei Validatoren statt JSON-Pflicht

`Annotated[list[str], NoDecode]` schaltet die JSON-Dekodierung ab; ein `mode="before"`-Validator
splittet den Rohstring an Kommas und trimmt Leerraum. Gemessenes Verhalten der Zielkonstruktion:

| Eingabe | Ergebnis |
|---|---|
| *nicht gesetzt* | `['https://wortlaut.io']` |
| `https://wortlaut.io, http://localhost:8080` | `['https://wortlaut.io', 'http://localhost:8080']` |
| `https://wortlaut.io` | `['https://wortlaut.io']` |
| leer / `" , , "` | `ValidationError` → Exit 2 |
| JSON-Form | `ValidationError` → Exit 2 |

Die JSON-Form wird damit bewusst **abgelehnt statt still zerlegt**: Ohne Prüfung ergäbe sie
`['["https://a"', '"https://b"]']` — eine Allowlist, die nichts matcht, ohne jede Fehlermeldung.
**Eine** Schreibweise, laut falsch bei jeder anderen.

### 4.2 Ein `mode="after"`-Validator, der echte Origins erzwingt

Jeder Eintrag muss mit `http://` oder `https://` beginnen, darf **keinen** Leerraum enthalten und
**nicht** auf `/` enden (§0b). Die Liste darf nicht leer sein. Jede Verletzung ist ein
`ValidationError`.

`_run_serve` in `cli.py` baut `ApiSettings()` bereits vor dem uvicorn-Start und fängt Fehler in
`_config_error` ab → **Exit 2 ohne ENV-Werte im Log** (R-SEC-01). Dafür ist an `cli.py`
**keine Änderung nötig**; das ist Absicht und beweist, dass das Muster aus #81 trägt.

### 4.3 Pflicht-Parameter statt Default in `create_app`

`allowed_origins` ist **keyword-only und ohne Default**. Ein Default wäre bequem, würde aber ein
vergessenes Verdrahten im Composition-Root **still** auf die Produktions-Herkunft zurückfallen
lassen — dieselbe Klasse stiller Halbumsetzung, die schon bei `workers` in #81 mit Veto belegt war.
Ohne Default ist Vergessen ein Typfehler, den mypy fängt.

Konsequenz: Die Aufrufstellen in den Tests müssen mitgezogen werden (§10) — gewollt, nicht
Kollateralschaden.

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)

- [ ] **AC1** Given `WORTLAUT_API_CORS_ORIGINS` ist **nicht** gesetzt, When `ApiSettings()` gebaut
  wird, Then ist `cors_origins == ["https://wortlaut.io"]` — unverändertes Verhalten gegenüber heute.
- [ ] **AC2** Given `WORTLAUT_API_CORS_ORIGINS` mit zwei kommagetrennten Origins und Leerraum, When
  `ApiSettings()` gebaut wird, Then stehen beide getrimmt in `cors_origins`.
- [ ] **AC3** Given eine App mit `allowed_origins=["https://wortlaut.io"]`, When ein `GET /healthz`
  mit Header `Origin: https://wortlaut.io` läuft, Then trägt die Antwort
  `access-control-allow-origin: https://wortlaut.io`.
- [ ] **AC4** Given dieselbe App, When ein `GET /healthz` mit einem fremden `Origin` läuft, Then
  enthält die Antwort **keinen** `access-control-allow-origin`-Header.
- [ ] **AC5** Given eine der fünf Fehlformen (leer · nur Kommas · ohne Schema · Trailing Slash ·
  JSON-Form), When `ApiSettings()` gebaut wird, Then wird eine `ValidationError` geworfen — je Fall
  ein Testfall.
- [ ] **AC6** Given eine kaputte `WORTLAUT_API_CORS_ORIGINS`, When `main(["serve"])` läuft, Then ist
  der Rückgabewert **2** und der stderr-Text enthält **keinen** ENV-**Wert** (nur Feldnamen).
- [ ] **AC7** Given `create_asgi_app()` mit gesetzter ENV, When die App gebaut wird, Then wurde
  `create_app` mit genau den Origins aus `ApiSettings` als `allowed_origins` aufgerufen.
- [ ] **AC8** Given die CORS-Middleware, Then bleibt `allow_methods == ["GET"]` und
  `allow_credentials` ist **nicht** gesetzt (kein Schreibpfad, keine Cookies).
- [ ] **AC9** CI vollständig grün (Lint/Type/Test/Coverage, Security-Gate, Architektur-Fitness,
  Docker inkl. Serve-Smoke) + **0 neue Sonar-Issues**.

## 6. Testplan (Test-zu-AC-Mapping)

- **Unit (`tests/unit/test_serving_cors.py`, neu):**
  AC1→`test_default_ohne_env`, AC2→`test_kommagetrennt_mit_leerraum`,
  AC5→`test_ungueltige_env_wirft` (parametrisiert über die fünf Fälle),
  AC8→`test_methods_bleiben_get_only`.
- **Unit (`tests/unit/test_serving_health.py`, erweitern):**
  AC3→`test_cors_header_bei_erlaubtem_origin`, AC4→`test_kein_cors_header_bei_fremdem_origin`.
  Die Datei hat bereits eine App ohne DB/Netz — dort andocken, **keine** neue Doppel-Infrastruktur.
- **Unit (`tests/unit/test_cli_serve.py`, erweitern):** AC6→`test_kaputte_cors_env_exit_2`.
- **Unit (`tests/unit/test_serving_asgi_factory.py`, erweitern):** AC7→`test_origins_durchgereicht`.
- **Integration:** keine neue nötig — CORS ist reine Middleware-Konfiguration ohne DB/WORM-Bezug.
  Die bestehende `tests/integration/test_serving_api.py` wird nur an die Signatur angepasst.

## 7. Recht / Security

- `rights_basis` / Provenienz / Immutability: **nicht betroffen** (read-only, keine Datenänderung).
- **R-SEC-01 (keine Interna nach außen):** Der Exit-2-Pfad nutzt das bestehende `_config_error` und
  nennt nur Feldnamen — die Allowlist selbst ist zwar kein Secret, aber die Regel bleibt einheitlich.
- **CORS ist hier keine Zugriffskontrolle.** Die Daten sind öffentlich und amtlich; den Zugang regelt
  Cloudflare Access. Die Allowlist dokumentiert, wer die Demo ausspielt, und verhindert, dass fremde
  Seiten die API im Browser-Kontext des Nutzers einbinden. Deshalb Allowlist statt `*` (§2).
- **KI-frei bleibt unberührt** — `check_no_llm_output` greift weiter auf `serving/*`.

## 8. Risiken & offene Fragen

- **Risiko:** Ein Deployment, das `WORTLAUT_API_CORS_ORIGINS` setzt und dabei die Produktions-Herkunft
  **vergisst**, sperrt wortlaut.io aus. Der Default hilft dann nicht (er gilt nur bei *ungesetzter*
  Variable). Gegenmaßnahme: in §11 als Kommentar an der Variablen festhalten, dass die Liste
  **vollständig** ist und nicht ergänzt wird.
- **Kein Risiko, obwohl es so aussieht:** Der neue Pflicht-Parameter ändert eine öffentliche Signatur.
  `create_app` ist kern-intern (`serving`), es gibt keinen externen Konsumenten.

## 9. Definition of Done (Verweis)

Erfüllt [../docs/rules.md](../docs/rules.md) DoD: AC grün, alle Gates grün, Review, keine
Gott-Klassen, kein Secret/Pickle/LLM-Freitext im Serving.

## 10. Files (NUR diese anlegen bzw. ändern)

**Ändern:**
1. `src/wortlaut/serving/settings.py` — `cors_origins` + zwei Validatoren.
2. `src/wortlaut/serving/app.py` — `create_app` bekommt `*, allowed_origins: Sequence[str]`.
3. `src/wortlaut/serving/asgi.py` — Origins aus `ApiSettings` durchreichen.
4. `tests/unit/test_serving_health.py` — Aufruf anpassen + zwei CORS-Tests.
5. `tests/unit/test_serving_asgi_factory.py` — Fakes auf die neue Signatur, ein Test für AC7.
6. `tests/unit/test_cli_serve.py` — ein Test für AC6.
7. `tests/integration/test_serving_api.py` — nur den `create_app`-Aufruf anpassen.

**Neu:**
8. `tests/unit/test_serving_cors.py`

**Nicht anfassen:** `src/wortlaut/cli.py` (§4.2 — bewusst keine Änderung nötig), `pyproject.toml`,
`uv.lock`, `Dockerfile`, `.github/workflows/ci.yml`, `.importlinter`.

## 11. Umsetzungsdetails je Datei

### `src/wortlaut/serving/settings.py` (ändern)

An `ApiSettings` anhängen. Imports ergänzen: `from typing import Annotated`,
`from pydantic import field_validator`, `NoDecode` aus `pydantic_settings`.

```python
    # Vollstaendige Allowlist, KEINE Ergaenzung des Defaults: wer die Variable setzt,
    # muss https://wortlaut.io mit aufzaehlen, sonst sperrt er die Produktion aus.
    # NoDecode schaltet die JSON-Dekodierung der Settings-Quelle ab — ohne sie waere
    # die kommagetrennte Schreibweise ein SettingsError und nur JSON zulaessig
    # (Spec 0086 Abschnitt 0a).
    cors_origins: Annotated[list[str], NoDecode] = ["https://wortlaut.io"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @field_validator("cors_origins", mode="after")
    @classmethod
    def _check_origins(cls, v: list[str]) -> list[str]:
        # Starlette vergleicht den Origin-Header exakt: ein Trailing Slash matcht nie,
        # ein Eintrag ohne Schema auch nicht — beides waere ein stiller Totalausfall
        # (Spec 0086 Abschnitt 0b). Lieber Exit 2 als eine Allowlist, die nichts trifft.
        if not v:
            raise ValueError("mindestens ein Origin erforderlich")
        for o in v:
            if not o.startswith(("http://", "https://")):
                raise ValueError(f"Origin ohne Schema: {o!r}")
            if any(c.isspace() for c in o) or o.endswith("/"):
                raise ValueError(f"kein gueltiger Origin: {o!r}")
        return v
```

### `src/wortlaut/serving/app.py` (ändern)

`Sequence` zu den bestehenden `collections.abc`-Importen ergänzen. Signatur und Middleware:

```python
def create_app(
    sessionmaker: async_sessionmaker[AsyncSession],
    worm: WormStore,
    *,
    allowed_origins: Sequence[str],
) -> FastAPI:
    """Baut die read-only Read-API. ``worm`` nur für /verify (Hash gegen WORM, #8).

    ``allowed_origins`` kommt aus dem Composition-Root (#86) — bewusst ohne Default:
    ein vergessenes Verdrahten soll ein Typfehler sein, kein stiller Rückfall auf die
    Produktions-Herkunft.
    """
    app = FastAPI(title="wortlaut Read-API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["GET"],
        allow_headers=["*"],
    )
```

Der Rest der Funktion bleibt **unverändert**.

### `src/wortlaut/serving/asgi.py` (ändern)

```python
from wortlaut.serving.settings import ApiSettings

def create_asgi_app() -> FastAPI:
    engine = create_async_engine_from(DbSettings())
    return create_app(
        make_sessionmaker(engine),
        MinioWormStore(WormSettings()),
        allowed_origins=ApiSettings().cors_origins,
    )
```

Den Modul-Docstring um einen Satz ergänzen: die Origins kommen ebenfalls aus der Umgebung.

### Tests

- `tests/unit/test_serving_cors.py` (neu): `ApiSettings` direkt gegen `monkeypatch.setenv`
  bzw. `monkeypatch.delenv` prüfen. AC5 als `pytest.mark.parametrize` über die fünf Fehlformen,
  jeweils `pytest.raises(ValidationError)`.
- `tests/unit/test_serving_health.py`: den bestehenden `create_app(sm, _FakeWorm())`-Aufruf um
  `allowed_origins=["https://wortlaut.io"]` ergänzen. Zwei neue Tests schicken `GET /healthz`
  einmal mit erlaubtem, einmal mit fremdem `Origin`-Header und prüfen die An-/Abwesenheit von
  `access-control-allow-origin`. **Header-Namen case-insensitiv** prüfen (httpx normalisiert).
- `tests/unit/test_serving_asgi_factory.py`: Die Fakes auf die neue Signatur ziehen —
  `allowed_origins` als Keyword annehmen. Neuer Test: ENV setzen, `create_asgi_app()` rufen,
  festhalten, dass der Fake genau diese Origins bekam.
- `tests/unit/test_cli_serve.py`: `WORTLAUT_API_CORS_ORIGINS` auf eine Fehlform setzen, DB/WORM-ENV
  gültig lassen, `main(["serve"])` muss **2** liefern; uvicorn darf **nicht** gestartet werden
  (bestehendes Patch-Muster der Datei wiederverwenden). Zusätzlich prüfen, dass der Fehlertext den
  gesetzten Wert **nicht** enthält.
- `tests/integration/test_serving_api.py`: nur den `create_app`-Aufruf um
  `allowed_origins=["https://wortlaut.io"]` ergänzen. **Sonst nichts** in dieser Datei ändern.

## 12. Do-NOT (hart)

- **KEIN** `allow_origins=["*"]`, auch nicht hinter einer Bedingung oder als Sonderwert.
- **KEIN** `allow_credentials=True`.
- **KEIN** Lesen von `os.environ` oder `ApiSettings()` in `serving/app.py` — die App bekommt
  Werte übergeben, sie holt sie nicht.
- **KEIN** Default für `allowed_origins` in `create_app`.
- **KEINE** neue Route, **kein** geändertes Antwortschema, **keine** Änderung an bestehenden
  Endpunkten.
- **KEINE** Änderung an `cli.py`, `pyproject.toml`, `uv.lock`, `Dockerfile`, `.importlinter`
  oder der CI-Datei.
- **KEIN** `# type: ignore`, **kein** `noqa` ohne Regelcode und Begründung.
- **KEINE** neuen Dependencies.

## 13. Abschluss (und NUR das an Kommandos ausführen)

```
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest -m "not integration" -q
```

Gib die Ausgabe **wörtlich** aus. Keine weiteren Kommandos.
