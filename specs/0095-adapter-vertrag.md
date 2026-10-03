# Increment-Spec: Adapter-Vertrag vervollständigen — `aclose` und gemeinsame Fehlerbasis (#95)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #95 · **Epic:** #94 (Quellen-Plugin-System, Schritt 1) · **Status:** Reviewed (autonom; Durchsicht durch den Stakeholder steht aus)
- **Phase/Layer:** `ingest` (Vertrag + DIP-Adapter), `cli`, Tests
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)

## 0. Ausgangslage

Der Kern ruft Dinge auf, die der Vertrag `IngestAdapter` (`src/wortlaut/ingest/adapter.py`) nicht
zusagt, und fängt Fehler, die nur **ein** Adapter kennt:

| Der Kern nutzt | Im Protocol? |
|---|---|
| `discover`, `fetch`, `normalize`, `parse`, `name`, `version`, `trust_level` | ja |
| `aclose` (in `cli.py`, Ingest und Reparse) | **nein** |
| `except (DipFetchError, ValueError)` (in `cli.py`, zweimal) | adapterspezifisch |

Ein fremder Adapter, der das Protocol buchstabengetreu erfüllt, scheitert am Ende jedes Laufs an
`aclose`, und seine Fehler werden nicht als „Quelle überspringen" behandelt.

### 0a. Vorklärung: woher die `ValueError` kommen

`cli.py` fängt neben `DipFetchError` auch `ValueError`. Gemessen im DIP-Adapter:

- `fetch` meldet einen **nicht erlaubten Host** (SSRF-Schutz) als `ValueError`;
  `tests/unit/test_dip_adapter.py` erwartet genau diesen Typ.
- `discover` ruft `response.json()` — kaputtes JSON wirft `json.JSONDecodeError`, eine
  `ValueError`-Unterklasse. `response.raise_for_status()` wirft `httpx.HTTPStatusError`, und
  Netzfehler (`httpx.TransportError`, `httpx.TimeoutException`) laufen heute **ganz ungefangen**
  durch und reißen den Lauf ab.

Fängt der Kern künftig **nur** die gemeinsame Basis, muss der DIP-Adapter diese Fälle selbst in
`DipFetchError` übersetzen. Für den Host-Fall eine eigene Klasse, die **zugleich** `ValueError`
ist — dann bleibt der bestehende Test gültig.

### 0b. Vorklärung: was das Verengen des Fangs bewirkt

Im Ingest-Loop umschließt `except (DipFetchError, ValueError)` heute den **ganzen** Aufruf von
`ingest_source` — also auch Kern-Code. Ein `ValueError` aus dem Kern würde still als `fetch_error`
gezählt. Nach dieser Spec fängt der Loop nur `AdapterError`; ein Kernfehler bricht den Lauf sichtbar
ab. Das ist gewollt: Fehler im Kern dürfen nicht als „Quelle übersprungen" verschwinden.

## 1. Ziel

1. `aclose` ist Teil des Vertrags.
2. Es gibt eine gemeinsame Fehlerbasis `AdapterError` im Adapter-Modul; alle Fehler, mit denen ein
   Adapter „diese Quelle geht gerade nicht" meldet, erben davon.
3. Der Kern fängt **ausschließlich** `AdapterError` und importiert keinen adapterspezifischen Fehlertyp.

## 2. Nicht-Ziele

- **Keine** Registry, keine Auswahl über `--adapter`, keine neuen Adapter (#96 ff.).
- **Keine** Änderung an `rights_basis` (#97).
- **Keine** Änderung am Ablauf von `ingest`, `reparse` oder an der Pipeline.

## 3. Betroffene Interfaces / Öffentliche Signaturen

```python
# src/wortlaut/ingest/adapter.py
class AdapterError(Exception):
    """Ein Adapter kann eine Quelle (oder die Entdeckung) gerade nicht liefern.

    Der Kern behandelt das als „diese Quelle überspringen" (bei fetch) bzw. als
    Abbruch der Entdeckung (bei discover) — nie als Programmfehler.
    """

class IngestAdapter(Protocol):
    ...                                   # bestehende Mitglieder unverändert
    async def aclose(self) -> None: ...   # NEU

# src/wortlaut/ingest/dip.py
class DipFetchError(AdapterError): ...                    # erbt jetzt von AdapterError
class DipHostNotAllowed(DipFetchError, ValueError): ...   # NEU, für den SSRF-Host-Check
```

## 4. Design

### 4.1 `aclose` im Vertrag

Docstring im Protocol: wird vom Kern **genau einmal** am Ende eines Laufs aufgerufen, auch wenn der
Lauf mit einem Fehler endet; muss idempotent sein und darf nicht werfen, wenn nichts zu schließen
ist. Der DIP-Adapter erfüllt das bereits.

### 4.2 Fehler im DIP-Adapter übersetzen

- `fetch`, Host nicht erlaubt: `raise DipHostNotAllowed(...)` statt `ValueError` (Meldung unverändert).
- `fetch` und `discover`: `httpx.TransportError` und `httpx.TimeoutException` (und deren
  Unterklassen) werden als `DipFetchError` weitergereicht (`raise … from exc`).
- `discover`: `httpx.HTTPStatusError` aus `raise_for_status()` und eine nicht dekodierbare
  JSON-Antwort (`ValueError` aus `response.json()`) werden als `DipFetchError` weitergereicht.
  Die Meldung nennt Status bzw. „invalid JSON", **nie** den Antworttext (R-SEC-07).
- Bestehende `DipFetchError`-Stellen bleiben unverändert.

### 4.3 Kern fängt nur die Basis

`cli.py`: beide `except (DipFetchError, ValueError)` → `except AdapterError`. Import von
`DipFetchError` entfernen. Meldungstexte und Exit-Codes bleiben.

## 5. Testbare Akzeptanzkriterien

- **AC1 — `aclose` im Vertrag.** `IngestAdapter` deklariert `async def aclose(self) -> None`; ein
  Objekt ohne `aclose` erfüllt `isinstance(obj, IngestAdapter)` **nicht** mehr
  (`runtime_checkable`), eines mit allen Mitgliedern schon.
- **AC2 — Fehlerbasis.** `issubclass(DipFetchError, AdapterError)` und
  `issubclass(DipHostNotAllowed, DipFetchError)` und `issubclass(DipHostNotAllowed, ValueError)`.
- **AC3 — Kern fängt nur die Basis.** `cli.py` importiert nichts aus `wortlaut.ingest.dip` außer dem
  Adapter selbst (`DipPlenarprotokollAdapter`); kein `except` in `cli.py` nennt `DipFetchError` oder
  `ValueError` für Adapter-Aufrufe (Test per AST oder Textsuche über `cli.py`).
- **AC4 — Minimal-Adapter läuft durch.** Ein Test-Adapter, der **nur** das Protocol erfüllt (keine
  weiteren Methoden, keine Vererbung vom DIP-Adapter), läuft durch `_run` (ingest) vollständig
  inklusive Herunterfahren: `aclose` genau einmal aufgerufen, kein `AttributeError`.
- **AC5 — Quelle überspringen.** Wirft `fetch` des Minimal-Adapters bei der zweiten von drei Quellen
  `AdapterError`, dann: Exit 0, `fetch_error=1`, die anderen beiden Quellen werden verarbeitet.
- **AC6 — Kernfehler bleiben sichtbar.** Wirft `ingest_source` einen `ValueError` (Patch), bricht
  `_run` mit dieser Ausnahme ab, statt ihn als `fetch_error` zu zählen.
- **AC7 — Entdeckung scheitert sauber.** Wirft `discover` `AdapterError`, endet `_run` mit Exit 2 und
  der Meldung `discover fehlgeschlagen: …`; `aclose` wird trotzdem genau einmal aufgerufen.
- **AC8 — DIP-Übersetzung.** Mit `httpx.MockTransport`: Netzfehler bei `fetch` → `DipFetchError`;
  Netzfehler, HTTP 500 und kaputtes JSON bei `discover` → `DipFetchError`; nicht erlaubter Host →
  `DipHostNotAllowed`. Der Antworttext steht nicht in der Meldung.
- **AC9 — Architektur.** import-linter grün; `ingest/adapter.py` importiert weiterhin nur stdlib.
- **AC10 — Bestand.** Alle bestehenden Tests grün; der Test mit `pytest.raises(ValueError, match="not
  in the allowed set")` bleibt **unverändert** grün. Einzige erlaubte Änderung an bestehenden Tests:
  die vier Fake-Adapter aus §11 bekommen ein leeres `aclose` (vorab gemessen: ohne das meldet mypy
  14 Fehler in genau diesen Dateien).

## 6. Testplan

| AC | Test | Datei | Art |
|---|---|---|---|
| AC1, AC2 | `test_protocol_requires_aclose` · `test_error_hierarchy` | `tests/unit/test_adapter_contract.py` (neu) | Unit |
| AC3 | `test_cli_catches_only_adapter_error` | `tests/unit/test_adapter_contract.py` | Unit |
| AC4–AC7 | `test_minimal_adapter_runs_through` · `test_adapter_error_skips_source` · `test_core_value_error_not_swallowed` · `test_discover_adapter_error_exit_2` | `tests/unit/test_cli_adapter_contract.py` (neu) | Unit |
| AC8 | `test_fetch_transport_error` · `test_discover_errors_translated` (parametrisiert) · `test_host_not_allowed_type` | `tests/unit/test_dip_errors.py` (neu) | Unit |
| AC9, AC10 | import-linter, bestehende Tests | — | — |

## 7. Recht / Security

- SSRF-Schutz unverändert (nur der Fehlertyp wird spezifischer).
- Fremdinhalt nie in Fehlermeldungen (R-SEC-07).

## 8. Risiken

- **Sichtbarere Abbrüche** bei Kernfehlern (0b) — gewollt.
- Ein künftiger Adapter, der eigene Fehler **nicht** von `AdapterError` ableitet, reißt den Lauf ab.
  Das Konformitäts-Testkit (#98) soll genau das prüfen.

## 9. Definition of Done

Siehe `docs/engineering.md`. Kein Betriebsschritt nötig.

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu:**
- `tests/unit/test_adapter_contract.py`
- `tests/unit/test_cli_adapter_contract.py`
- `tests/unit/test_dip_errors.py`

**Ändern:**
- `tests/integration/test_pipeline_ingest.py`, `tests/unit/test_pipeline_order.py`,
  `tests/unit/test_reparse_pipeline.py`, `tests/unit/test_span_hash.py` — **nur** ein leeres `aclose`
  in den Fake-Adaptern (`FakeIngestAdapter`, `FakeAdapter`, `_FakeAdapter`, `_RaisingAdapter`)
- `src/wortlaut/ingest/adapter.py`
- `src/wortlaut/ingest/dip.py`
- `src/wortlaut/cli.py`

## 11. Umsetzungsdetails je Datei

### `src/wortlaut/ingest/adapter.py`

`AdapterError` nach §3 **vor** den Dataclasses definieren. Im Protocol `aclose` nach `parse`
ergänzen, mit Docstring nach §4.1. Modul-Docstring: Satz ergänzen, dass Adapter Fehler als
`AdapterError` (oder Unterklassen) melden. Weiterhin nur stdlib-Importe.

### `src/wortlaut/ingest/dip.py`

- `from wortlaut.ingest.adapter import AdapterError` (neben den bestehenden Importen aus diesem Modul).
- `class DipFetchError(AdapterError)`; neue `class DipHostNotAllowed(DipFetchError, ValueError)` mit
  kurzem Docstring (SSRF-Host-Check; zugleich `ValueError` für bestehende Aufrufer).
- `fetch`: Host-Check wirft `DipHostNotAllowed` mit **unveränderter** Meldung; den `get`-Aufruf in
  `try/except (httpx.TransportError, httpx.TimeoutException) as exc: raise DipFetchError(f"network
  error for {ref.origin_url}: {type(exc).__name__}") from exc` einschließen.
- `discover`: den `get`/`raise_for_status`/`json`-Block je Seite so einschließen, dass
  `httpx.TransportError`/`httpx.TimeoutException` → `DipFetchError("DIP network error: <Typ>")`,
  `httpx.HTTPStatusError` → `DipFetchError(f"DIP status {status}")`, `ValueError` aus `json()` →
  `DipFetchError("DIP returned invalid JSON")`. Jeweils `from exc`.

### `src/wortlaut/cli.py`

- Import auf `from wortlaut.ingest.dip import DipPlenarprotokollAdapter` reduzieren;
  `from wortlaut.ingest.adapter import AdapterError` ergänzen.
- Beide `except (DipFetchError, ValueError) as e:` → `except AdapterError as e:`. Sonst nichts.

### Tests

- **Bestehende Fakes:** in `FakeIngestAdapter` (`test_pipeline_ingest.py`), `FakeAdapter`
  (`test_pipeline_order.py`), `_FakeAdapter` (`test_reparse_pipeline.py`) und `_RaisingAdapter`
  (`test_span_hash.py`) jeweils genau diese Methode ergänzen — sonst nichts an diesen Dateien:
  ```python
      async def aclose(self) -> None:
          return None
  ```
- **Minimal-Adapter** in `tests/unit/test_cli_adapter_contract.py`: eine schlichte Klasse mit genau
  den Protocol-Mitgliedern (`name`, `version`, `trust_level`, `discover`, `fetch`, `normalize`,
  `parse`, `aclose`), zählt Aufrufe. `_run` testen nach dem Muster von `tests/unit/test_cli.py`
  (Composition-Root-Deps per `patch`, `DipPlenarprotokollAdapter` durch den Minimal-Adapter
  ersetzen, `ingest_source` per `patch` mit `side_effect=`).
- **DIP-Fehler** mit `httpx.MockTransport` nach dem Muster von `tests/unit/test_dip_fetch_validation.py`.
- **Sonar-Muster:** ein Aufruf je `pytest.raises`-Block (Hilfsaufrufe vorher in Variablen); keine
  zusammengesetzten Asserts; kein Lambda in `patch()`; Literale, die dreimal vorkommen, als
  Konstante; `async def` nur, wo awaited wird.

## 12. Do-NOT (hart)

- **Keine** Änderung an Pipeline, Store, Serving, Migrationen oder anderen Kommandos als `ingest`
  (Reparse nutzt `aclose` bereits — nichts ändern).
- **Keine** bestehenden Tests ändern — außer dem leeren `aclose` in den vier Fakes aus §10.
- **Kein** wortlaut-Eigenimport in `ingest/adapter.py`.
- **Keine** Antworttexte in Fehlermeldungen.
- **Keine** neuen Abhängigkeiten, **keine** Dateien außerhalb von §10.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate fährt der Reviewer selbst.
