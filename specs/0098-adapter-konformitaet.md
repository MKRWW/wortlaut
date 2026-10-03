# Increment-Spec: Konformitäts-Testkit für Adapter (#98)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #98 · **Epic:** #94 (Quellen-Plugin-System, Schritt 4) ·
  **Status:** Reviewed (autonom; Durchsicht durch den Stakeholder steht aus — Punkte in §0b)
- **Phase/Layer:** `ingest` (neues Modul), `.importlinter`, Doku, Tests
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)

## 0. Ausgangslage

Das Protocol `IngestAdapter` sagt, **welche** Methoden es gibt, nicht, **was** sie zusichern müssen.
Die Zusicherungen stehen verstreut im Kern — teils so, dass ein Verstoß den ganzen Lauf abreißt
(gemessen in `src/wortlaut/pipeline/spans.py`, `write_spans`):

| Zusicherung | Folge bei Verstoß heute |
|---|---|
| `speaker_hint["name"]` existiert | `KeyError` — Lauf bricht ab |
| `spoken_at` ist leer **oder** ein ISO-Datum | leer: Span still übersprungen · anderes Format: `ValueError`, Lauf bricht ab |
| `normalized[text_start:text_end] == verbatim_text` | Span wird nie ausgespielt (Anti-Halluzinations-Filter, R-DATA-06) |
| `rights_basis` je Quelle auflösbar und im Enum (#97) | `ingest` endet mit Exit 2 |
| `fetch`-Fehler sind `AdapterError` (#95) | jede andere Exception bricht den Lauf ab |

### 0a. Offline-Muster (gemessen)

Der DIP-Adapter holt alles über `self._client`; die Tests setzen dort einen
`httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)` ein
(`tests/unit/test_dip_errors.py`). Fixtures: `tests/fixtures/dip/discover_plenarprotokoll.json`
(zwei Dokumente, `pdf_url` auf `dserver.bundestag.de`) und
`tests/fixtures/dip/plenarprotokoll_kontext.pdf` (liefert genau 2 Spans,
`tests/unit/test_dip_context.py`). Ein `SourceRef` mit fremdem Host lässt `fetch` ohne Netz mit
`DipHostNotAllowed` (einer `AdapterError`) scheitern.

### 0b. Entscheidungen, die zur Durchsicht stehen

1. **Kein pytest im Modul.** Das Testkit liegt in `src/wortlaut/ingest/conformance.py`, importiert
   nur stdlib und die beiden Vertragsmodule (`ingest.adapter`, `ingest.rights`) und liefert eine
   Liste von Verstößen. pytest ist nur Entwicklungsabhängigkeit; so bleibt das Modul überall
   importierbar. `assert_conformant` macht daraus einen `AssertionError` — in pytest ein normaler
   roter Test.
2. **Alle Verstöße auf einmal**, nicht beim ersten abbrechen. Jeder Verstoß trägt eine stabile
   Prüf-ID (§4.3); die Doku listet sie.
3. **Beispieldaten kommen vom Autor.** Das Testkit kann kein Netz abschalten; der Autor übergibt
   eine offline verdrahtete Instanz (wie oben) und optional eine Rohquelle und eine Ref, an der
   `fetch` scheitern muss.
4. **Leeres `spoken_at` ist kein Verstoß** (der Kern überspringt den Span bewusst, „nie
   Falsch-Datum"); ein nicht-leeres Nicht-ISO-Datum schon.
5. **Leerlauf-Schutz:** `parse` muss auf der Beispielquelle mindestens `min_spans` Spans liefern
   (Default 1). Sonst bestünde ein Adapter, der nie etwas parst, jede Offset-Prüfung.

## 1. Ziel

Ein Adapter-Autor ruft eine Funktion mit seiner Adapter-Instanz und Beispieldaten auf und bekommt
gesagt, welche Zusicherungen des Vertrags er verletzt — ohne Kern-Wissen, ohne Netz, ohne Datenbank.

## 2. Nicht-Ziele

- **Keine** Prüfung fachlicher Qualität (ob der Parser *gut* parst).
- **Keine** Änderung am Vertrag, an der Pipeline oder am DIP-Adapter.
- **Keine** Entry Points, kein Laden fremder Pakete (Epic-Schritt 5).

## 3. Öffentliche Signaturen

```python
# src/wortlaut/ingest/conformance.py  (stdlib + wortlaut.ingest.adapter + wortlaut.ingest.rights)
TRUST_LEVELS: tuple[str, ...]           # = ("verified_primary", "secondary", "low") — wie das DB-Enum

@dataclass(frozen=True)
class ConformanceSamples:
    since: datetime                      # Argument für discover
    raw: RawSource | None = None         # Beispielquelle für normalize/parse; sonst fetch(erste Ref)
    failing_ref: SourceRef | None = None # an dieser Ref MUSS fetch mit AdapterError scheitern
    min_spans: int = 1                   # parse muss auf der Beispielquelle mindestens so viele liefern

@dataclass(frozen=True)
class Violation:
    check: str                           # stabile Prüf-ID aus §4.3
    message: str                         # was falsch ist, für Menschen; NIE Quellinhalt über 80 Zeichen

async def check_adapter(adapter: object, samples: ConformanceSamples) -> list[Violation]: ...
async def assert_conformant(adapter: object, samples: ConformanceSamples) -> None: ...
```

`assert_conformant` ruft `check_adapter`; bei mindestens einem Verstoß `AssertionError` mit einer
Zeile je Verstoß im Format `[<check>] <message>`, sonst `None`.

## 4. Design

### 4.1 Ablauf von `check_adapter`

1. **Vertrag:** `isinstance(adapter, IngestAdapter)`. Falls nein → Verstoß `protocol` mit den
   fehlenden Mitgliedern (Namen aus der festen Liste `name, version, trust_level, rights_basis,
   discover, fetch, normalize, parse, aclose`, per `hasattr`) und **sofort** zurückgeben — weitere
   Aufrufe würden mit `AttributeError` scheitern.
2. **Identität:** `name`, `version` nicht-leere `str`; `trust_level` in `TRUST_LEVELS` → sonst `identity`.
   `rights_basis` ist `None` oder in `RIGHTS_BASES` → sonst `rights_basis`.
3. **discover:** `await adapter.discover(samples.since)`. Wirft es irgendetwas → `discover`
   (Meldung nennt den Ausnahmetyp). Ergebnis keine `Sequence` (oder ein `str`) oder ein Element
   kein `SourceRef` → `discover`. Leere Sequenz ist **erlaubt**. Für jede Ref: aufgelöste
   Rechtsgrundlage (`ref.rights_basis`, sonst `adapter.rights_basis`) fehlt → `ref_rights`;
   vorhanden, aber nicht in `RIGHTS_BASES` → `ref_rights`. Höchstens **ein** `ref_rights`-Verstoß
   je Grund (Meldung nennt die Anzahl), damit 500 Refs nicht 500 Zeilen erzeugen.
4. **fetch:** Gibt es mindestens eine Ref, `await adapter.fetch(refs[0])`. Exception → `fetch`.
   Ergebnis kein `RawSource` oder `raw_bytes` leer → `fetch_raw`. `mime_type` passt nicht auf
   `^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$` → `fetch_mime` (der Wert wird unverändert
   geprüft, ohne vorheriges `.lower()`); ist er `application/pdf`, müssen die
   Bytes mit `%PDF-` beginnen → sonst `fetch_mime`.
5. **Fehlerpfad:** Ist `samples.failing_ref` gesetzt: `await adapter.fetch(failing_ref)` muss
   `AdapterError` (oder Unterklasse) werfen. Keine Exception → `fetch_error` („kein Fehler");
   andere Exception → `fetch_error` (Meldung nennt den Typ).
6. **Beispielquelle:** `samples.raw`, sonst das Ergebnis aus Schritt 4, falls gültig. Gibt es
   keine → Verstoß `no_sample` und die Schritte 7–8 entfallen.
7. **normalize:** zweimal aufrufen. Exception → `normalize`; kein `str` → `normalize`; beide
   Ergebnisse verschieden → `normalize_deterministic`.
8. **parse:** `list(adapter.parse(raw, normalized))`. Exception → `parse`. Weniger als
   `samples.min_spans` Elemente → `parse_min_spans`. Je Draft (Element kein `SpanDraft` →
   `parse`):
   - `span_offsets`: `0 <= text_start < text_end <= len(normalized)` **und**
     `normalized[text_start:text_end] == verbatim_text`.
   - `span_speaker`: `speaker_hint` ist ein `dict` mit nicht-leerem `str` unter `"name"`.
   - `span_date`: `spoken_at` ist `""` oder `date.fromisoformat(spoken_at)` gelingt.
   - `span_locator`: `locator` ist ein `dict` und `json.dumps(locator)` gelingt.
   - `span_permalink`: `permalink` ist ein nicht-leerer `str`.
   Je Prüf-ID höchstens **ein** Verstoß über alle Drafts (Meldung: Anzahl betroffener Drafts und
   Index des ersten).
9. **aclose:** am Ende der Schritte 2–8 (nach einem Abbruch in Schritt 1 **nicht**):
   **zweimal** aufrufen; wirft einer der Aufrufe → `aclose` (Idempotenz-Zusage aus #95).

Fremde Ausnahmen aus Adapter-Code werden **immer** gefangen und als Verstoß gemeldet; das Testkit
selbst wirft nie wegen eines kaputten Adapters.

### 4.2 Struktur

Je Schritt eine private Hilfsfunktion (`_check_identity`, `_check_discover`, `_check_fetch`,
`_check_failing_ref`, `_check_normalize`, `_check_parse`, `_check_drafts`, `_check_aclose`),
`check_adapter` reiht sie nur auf. Keine Funktion mit mehr als ~25 Zeilen (Sonar S3776).
Meldungen nennen nie mehr als 80 Zeichen Fremdinhalt (R-SEC-07: Ingest-Content ist Daten).

### 4.3 Prüf-IDs (stabil, in der Doku gelistet)

`protocol`, `identity`, `rights_basis`, `discover`, `ref_rights`, `fetch`, `fetch_raw`,
`fetch_mime`, `fetch_error`, `no_sample`, `normalize`, `normalize_deterministic`, `parse`,
`parse_min_spans`, `span_offsets`, `span_speaker`, `span_date`, `span_locator`, `span_permalink`,
`aclose`.

### 4.4 Architektur

Neuer import-linter-Vertrag: `wortlaut.ingest.conformance` importiert weder `wortlaut.store`,
`wortlaut.pipeline`, `wortlaut.archive`, `wortlaut.timestamp`, `wortlaut.serving` noch
`wortlaut.ingest.dip` (generisch bleiben, kein DB-Pfad — AC6).

## 5. Testbare Akzeptanzkriterien

- **AC1 — Importierbar.** `from wortlaut.ingest.conformance import ConformanceSamples,
  assert_conformant, check_adapter` funktioniert; ein konformer Referenz-Adapter (im Test) liefert
  `[]`, `assert_conformant` wirft nicht.
- **AC2 — Abgedeckte Zusicherungen.** Für **jede** Prüf-ID aus §4.3 außer `no_sample` gibt es einen
  kaputten Test-Adapter, der genau diese ID auslöst (Test: ID ist in `{v.check for v in result}`).
  `no_sample`: Adapter, dessen `discover` leer ist, ohne `samples.raw` → `no_sample`. Leere
  `discover`-Sequenz **mit** `samples.raw` → `[]`.
- **AC3 — Fehlerpfad.** `failing_ref`, an der `fetch` `ValueError` wirft → `fetch_error`; an der
  `fetch` nichts wirft → `fetch_error`; an der `fetch` eine `AdapterError`-Unterklasse wirft → kein
  `fetch_error`.
- **AC4 — DIP-Adapter besteht.** Der DIP-Adapter mit `MockTransport` (Discover-Fixture, Kontext-PDF
  für jede PDF-URL) und `failing_ref` auf fremden Host liefert `[]`; `parse` hat dabei ≥ 2 Spans
  geprüft (`min_spans=2`).
- **AC5 — Suite greift.** Ein Test-Adapter mit um 1 verschobenen Offsets liefert `span_offsets`;
  `assert_conformant` wirft `AssertionError`, dessen Text `[span_offsets]` enthält.
- **AC6 — Ohne Netz und Datenbank.** `conformance.py` importiert nur stdlib, `wortlaut.ingest.adapter`
  und `wortlaut.ingest.rights` (AST-Test); import-linter-Vertrag aus §4.4 grün. `TRUST_LEVELS`
  stimmt mit `_TRUST_LEVEL.enums` aus `wortlaut.store.models` überein (Drift-Test).
- **AC7 — Doku.** `docs/adapter-konformitaet.md`: eine Seite — importieren, Adapter offline
  verdrahten, `assert_conformant` in einem pytest-Test aufrufen, Tabelle aller Prüf-IDs mit
  Bedeutung. `MITWIRKEN.md` verweist mit einer Zeile darauf.
- **AC8 — Bestand.** Alle bestehenden Tests grün, keine bestehende Datei unter `src/` außer
  `.importlinter` geändert. CI grün, 0 neue Sonar-Issues.

## 6. Testplan

| AC | Datei | Art |
|---|---|---|
| AC1, AC2, AC3, AC5 | `tests/unit/test_conformance.py` (neu) | Unit |
| AC4 | `tests/unit/test_conformance_dip.py` (neu) | Unit (MockTransport, Fixtures) |
| AC6 | `tests/unit/test_conformance.py` | Unit (AST + Drift) |
| AC6, AC8 | import-linter, bestehende Tests | — |

## 7. Recht / Security

- R-SEC-07: Fremdinhalt nur gekürzt (≤ 80 Zeichen) in Meldungen.
- Prüft `ref_rights` mit — ein Adapter ohne auflösbare Rechtsgrundlage fällt schon im Testkit auf,
  nicht erst beim Lauf (#97).

## 8. Risiken

- Ein Autor kann das Testkit mit Beispieldaten füttern, die gerade nicht kaputt sind. Das Testkit
  prüft den Vertrag an den übergebenen Daten, keine Vollständigkeit. Die Doku sagt das.
- Neue Zusicherungen im Kern müssen hier nachgezogen werden; die Doku nennt `pipeline/spans.py`
  als Quelle.

## 9. Definition of Done

Siehe `docs/engineering.md`. Kein Betriebsschritt.

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu:** `src/wortlaut/ingest/conformance.py` · `tests/unit/test_conformance.py` ·
`tests/unit/test_conformance_dip.py` · `docs/adapter-konformitaet.md`

**Ändern:** `.importlinter` (Vertrag anhängen) · `MITWIRKEN.md` (eine Zeile mit Verweis)

## 11. Umsetzungsdetails

### `src/wortlaut/ingest/conformance.py`

Modul-Docstring: Konformitäts-Testkit (#98) — prüft eine Adapter-Instanz gegen die Zusicherungen
des Vertrags, ohne Netz und Datenbank; Nutzung siehe `docs/adapter-konformitaet.md`. Importe:
`from __future__ import annotations`, `json`, `re`, `collections.abc.Sequence`, `dataclasses`,
`datetime` (`date`, `datetime`), dazu `AdapterError, IngestAdapter, RawSource, SourceRef, SpanDraft`
aus `wortlaut.ingest.adapter` und `RIGHTS_BASES` aus `wortlaut.ingest.rights`.

Interne Sammelstelle: eine kleine Klasse `_Findings` mit `add(check, message)` und `items`
(Liste von `Violation`), die je Prüf-ID nur den **ersten** Eintrag behält, wenn
`once=True` übergeben wird (für `ref_rights` und die `span_*`-Prüfungen; deren Meldung bildet der
Aufrufer mit Anzahl und erstem Index). Hilfsfunktion `_short(value: object) -> str`:
`repr(value)` auf 80 Zeichen gekürzt.

Ausnahmen aus Adapter-Aufrufen: `except Exception as exc` und Verstoß mit `type(exc).__name__` —
**nie** den Ausnahmetext ungekürzt.

### `.importlinter` — anhängen

```ini
# Spec 0098 §4.4: Das Konformitaets-Testkit ist reine Vertragspruefung — kein Datenbank-,
# Pipeline-, Netzdienst- oder Ausgabe-Code und kein konkreter Adapter, auch nicht indirekt.
[importlinter:contract:konformitaet-ohne-kern]
name = Konformitaets-Testkit importiert keinen Kern- oder Adapter-Code
type = forbidden
source_modules =
    wortlaut.ingest.conformance
forbidden_modules =
    wortlaut.store
    wortlaut.pipeline
    wortlaut.archive
    wortlaut.timestamp
    wortlaut.serving
    wortlaut.ingest.dip
```

### `tests/unit/test_conformance.py`

- Referenz-Adapter `_GoodAdapter` **ohne Netz**: fester Text
  `_TEXT = "Präsidentin: Die Sitzung ist eröffnet. Dr. Max Mustermann (AfD): Ich beginne."`;
  `discover` liefert eine Ref `https://example.org/q1.txt` (ohne eigene Rechtsgrundlage);
  `rights_basis = "lizenz"`; `fetch` liefert `RawSource` mit `_TEXT.encode()` und
  `mime_type="text/plain"`, wirft `AdapterError` für jede Ref mit Host `fremd.example`;
  `normalize` = `raw.raw_bytes.decode("utf-8")`; `parse` liefert genau einen `SpanDraft` für
  `"Ich beginne."` mit korrekt berechneten Offsets (`_TEXT.index(...)`), `speaker_hint={"name":
  "Dr. Max Mustermann", "party": "AfD"}`, `spoken_at="2024-07-05"`, `locator={"sitzung": "88"}`,
  `permalink` = Ref-URL; `aclose` tut nichts.
- Kaputte Varianten als **Unterklassen** von `_GoodAdapter`, je genau eine überschriebene Stelle,
  eine Klasse je Prüf-ID (z. B. `_ShiftedOffsets`, `_RandomNormalize` mit Zähler im Text,
  `_ValueErrorOnFailingRef`, `_NoErrorOnFailingRef`, `_EmptyBytes`, `_BadMime` (`"pdf"`),
  `_PdfMimeWithoutMagic`, `_NoSpeakerName`, `_GermanDate` (`"05.07.2024"`), `_SetLocator`
  (`locator={"x": {1, 2}}` — nicht JSON-fähig), `_EmptyPermalink`, `_NoSpans`, `_DiscoverRaises`,
  `_DiscoverReturnsString`, `_BadTrustLevel` (`"hoch"`), `_BadRightsBasis` (`"gemeinfrei"`),
  `_NoRightsAnywhere` (`rights_basis = None`, Ref ohne Angabe), `_FetchRaises` (`RuntimeError`),
  `_NormalizeRaises`, `_ParseRaises`, `_AcloseRaisesSecondTime`). Für `protocol`: eine **eigenständige**
  Klasse ohne `aclose`.
- Ein parametrisierter Test `test_broken_adapter_is_reported` über Paare `(Klasse, Prüf-ID)`;
  Samples: `ConformanceSamples(since=datetime(2024, 1, 1), failing_ref=SourceRef("https://fremd.example/x",
  "rede", {}))`.
- Einzeltests: `test_good_adapter_passes` (AC1), `test_empty_discover_with_sample_passes`,
  `test_empty_discover_without_sample_is_no_sample`, `test_failing_ref_adapter_error_subclass_ok`
  (AC3), `test_assert_conformant_lists_all` (AC5: `pytest.raises(AssertionError, match=r"\[span_offsets\]")`,
  `samples` und Adapter **vor** dem Block bauen), `test_messages_are_short` (Adapter, dessen
  `fetch` eine Ausnahme mit 10 000 Zeichen Text wirft → keine Meldung länger als 200 Zeichen),
  `test_only_contract_imports` (AST über `src/wortlaut/ingest/conformance.py`: alle
  `wortlaut.*`-Importe ⊆ `{wortlaut.ingest.adapter, wortlaut.ingest.rights}`),
  `test_trust_levels_match_db_enum`.
- **Sonar-Muster:** ein Aufruf je `pytest.raises`-Block; ein Assert je Zeile; Literale ≥ 3× als
  Konstante; `async def` nur, wo awaited wird.

### `tests/unit/test_conformance_dip.py`

DIP-Adapter mit `DipSettings(api_key="test-key", api_base_url="https://search.dip.bundestag.de/api/v1",
pdf_host="dserver.bundestag.de")`, `adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
follow_redirects=False)`. `handler`: Pfad endet auf `/plenarprotokoll` (oder Host
`search.dip.bundestag.de`) → JSON aus `discover_plenarprotokoll.json` **ohne** `cursor`-Weiterlauf
(zweite Seite: gleiche Antwort, der Adapter stoppt bei stabilem Cursor — vorher in
`src/wortlaut/ingest/dip.py` nachsehen und so bauen, dass genau eine bzw. zwei Seiten geholt werden);
Host `dserver.bundestag.de` → Kontext-PDF mit `content-type: application/pdf`. Samples:
`since=datetime(2023, 1, 1)`, `failing_ref=SourceRef("https://fremd.example/x.pdf", "plenarprotokoll", {})`,
`min_spans=2`. Erwartung `[]` (AC4). Zweiter Test: dieselbe Instanz, aber `handler` liefert für das
PDF `content-type: text/html` und HTML-Bytes → der Ergebnis-Verstoß ist **nicht** leer (zeigt, dass
der DIP-Test nicht leer durchläuft).

### `docs/adapter-konformitaet.md`

Eine Seite, Deutsch, ohne Hostnamen oder Zugangsdaten: Zweck (2–3 Sätze), Minimalbeispiel als
pytest-Test (Adapter offline verdrahten, `ConformanceSamples`, `await assert_conformant(...)`),
Tabelle aller Prüf-IDs aus §4.3 mit einer Zeile Bedeutung, Hinweis „prüft die übergebenen Daten,
nicht jede denkbare Quelle", Verweis auf `src/wortlaut/pipeline/spans.py` als Quelle der
Zusicherungen. `MITWIRKEN.md`: an passender Stelle eine Zeile mit Link auf diese Seite.

## 12. Do-NOT (hart)

- **Keine** Änderung an `src/` außer der neuen Datei `conformance.py`.
- **Kein** pytest-Import in `src/`.
- **Kein** Import aus `wortlaut.store`, `wortlaut.pipeline`, `wortlaut.ingest.dip` in `conformance.py`.
- **Keine** Live-Netz-Aufrufe in Tests.
- **Keine** neuen Abhängigkeiten, **keine** Dateien außerhalb von §10.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate fährt der Reviewer selbst.
