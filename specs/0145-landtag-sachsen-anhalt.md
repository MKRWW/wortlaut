# Increment-Spec: Adapter für den Landtag von Sachsen-Anhalt (#145)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Ändere bzw. lege die Dateien aus **§10** wirklich auf der Platte an.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #145 · **Epic:** #94 (Schritt 6b, erster Landtag) · baut auf #143 auf ·
  **Status:** Reviewed (autonom; Entscheidungen des Stakeholders vom 04.10. in §0b)
- **Phase/Layer:** `ingest` (neuer Adapter + Parser), `pipeline`/`store` (Rolle je Beitrag), Doku, Tests
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Recht: [../docs/legal.md](../docs/legal.md)

## 0. Ausgangslage (gemessen am 04.10.2026)

- Stenografische Berichte als PDF mit Textlayer, fortlaufend nummeriert:
  `https://padoka.landtag.sachsen-anhalt.de/files/plenum/wp8/118stzg.pdf`. Eine nicht vorhandene
  Nummer liefert **404** (HEAD geprüft). Die Links auf `www.landtag.sachsen-anhalt.de/fileadmin/…`
  leiten per 301 dorthin; die Sitzungsperioden-Seiten verlinken nicht alle Protokolle (50, 51 ohne)
  und taugen nicht zur Entdeckung.
- Impressum des Landtags: Plenarprotokolle sind amtliche Werke nach § 5 Abs. 2 UrhG, mit
  Änderungsverbot (§ 62) und Quellenangabe (§ 63). robots.txt von PADOKA: `Disallow: /files/`.
- Protokoll 8/118 mit dem vorhandenen Segmentierer (`protokoll_parse.segment_speeches`): 57
  Beiträge, alle Offsets korrekt. Kopf: `118. Sitzung, Freitag, 26.06.2026` und
  `Stenografischer Bericht 8/118` — `parse_header` (Bundestag) findet das Datum nicht.
- **Fehler mit Rechtsfolge:** Lange Amtsbezeichnungen brechen in der Rednerzeile um:
  `Dr. Tamara Zieschang (Ministerin für Inneres` / `und Sport):`. `SPEAKER_MARKER` lässt keinen
  Zeilenumbruch in der Klammer zu → kein Rednerwechsel erkannt, die Rede der Ministerin wird dem
  Vorredner zugeordnet.
- Klammerinhalte, die keine Fraktion sind: `(Berichterstatter)`, `(Staats- und Kulturminister)`,
  `(Ministerin für Inneres und Sport)`.

### 0b. Entscheidungen

1. **Abruf aus, bis der Landtag antwortet** (Stakeholder): Schalter `enabled` (ENV
   `WORTLAUT_LANDTAG_ST_ENABLED`), Default `False`. Ohne ihn wirft `discover` sofort einen
   `AdapterError` und stellt **keine** Anfrage. `fetch` einer bekannten Ref bleibt möglich
   (Reparse, Tests).
2. **Vertrauen `secondary`** (Stakeholder) — Spans gelten als `machine`; Anhebung später.
3. **Regierungsmitglieder aufnehmen** (Stakeholder): Fraktion leer, Rolle = Amtsbezeichnung. Dafür
   darf `speaker_hint` einen optionalen Schlüssel `"role"` tragen; fehlt er, gilt
   `adapter.mandate_role`. Berichterstatter sind Abgeordnete: Fraktion leer, keine eigene Rolle.
4. **Mandatssuche unterscheidet Rollen** — sonst fielen „Ministerin, ohne Fraktion“ und
   „Berichterstatterin, ohne Fraktion“ derselben Person in ein Mandat. Für den Bundestag ändert
   sich nichts (Rolle dort immer `MdB`).
5. **Entdeckung über die Nummer:** höchste vorhandene Nummer per HEAD suchen (exponentiell, dann
   binär; ≤ 21 Anfragen je Wahlperiode), dann die letzten `lookback` Protokolle als Refs. `since`
   wird nicht ausgewertet — die Quelle nennt das Datum erst im PDF; Doppelte fängt der Kern über
   den Inhalts-Hash ab (`skipped_duplicate`). Zusätzlich wird Wahlperiode `wahlperiode + 1`
   geprüft (neue Wahlperiode nach der Wahl im September 2026).
6. **Erkennbarer User-Agent** `wortlaut-ingest/1.0 (+<contact>)`.
7. **Rednerzeile als eigene Zeile:** Der Sachsen-Anhalt-Marker verlangt, dass die Zeile mit `):`
   bzw. `:` endet, und erlaubt **einen** Zeilenumbruch in der Klammer. Der Bundestag-Marker bleibt
   unverändert.

## 1. Ziel

`python -m wortlaut ingest --adapter landtag-st` holt (nach Freigabe) die jüngsten Stenografischen
Berichte, und jeder Redebeitrag landet mit richtiger Person, Fraktion bzw. Amtsbezeichnung beim
Parlament `landtag-sachsen-anhalt`.

## 2. Nicht-Ziele

- Kein Freischalten des Abrufs, kein Ausrollen, keine Timer/Cron-Einträge.
- Kein Backfill-Kommando; kein Präsidium als Sprecher (wie Bundestag).
- Keine Änderung am Bundestag-Marker oder am DIP-Adapter.

## 3. Öffentliche Signaturen

```python
# src/wortlaut/ingest/protokoll_parse.py — nur ein neuer Keyword-Parameter
def segment_speeches(
    normalized: str, *, marker: re.Pattern[str] = SPEAKER_MARKER
) -> list[SpeechSegment]: ...

# src/wortlaut/ingest/landtag_st_parse.py (neu)
SPEAKER_MARKER_ST: re.Pattern[str]
@dataclass(frozen=True)
class StSpeech:
    verbatim_text: str
    text_start: int
    text_end: int
    name: str
    party: str | None
    role: str | None
    tagesordnungspunkt: str | None
def classify(paren: str) -> tuple[str | None, str | None]: ...  # (party, role)
def parse_header_st(normalized: str) -> tuple[str, dict[str, object]]: ...
def segment_speeches_st(normalized: str) -> list[StSpeech]: ...

# src/wortlaut/ingest/settings.py
class LandtagStSettings(BaseSettings):  # env_prefix="WORTLAUT_LANDTAG_ST_"
    enabled: bool = False
    base_url: str = "https://padoka.landtag.sachsen-anhalt.de/files/plenum"
    wahlperiode: int = 8
    lookback: int = 3
    contact: str = "https://github.com/MKRWW/wortlaut"

# src/wortlaut/ingest/landtag_st.py (neu)
class LandtagStError(AdapterError): ...
class LandtagStDisabled(LandtagStError): ...
class LandtagSachsenAnhaltAdapter:
    name = "landtag-st"; version = "1.0.0"; trust_level = "secondary"
    parliament = "landtag-sachsen-anhalt"; mandate_role = "MdL"
    rights_basis = "amtliches_werk_p5"
    def __init__(self, settings: LandtagStSettings) -> None: ...
    @classmethod
    def from_env(cls) -> LandtagSachsenAnhaltAdapter: ...
    # discover / fetch / normalize / parse / aclose wie IngestAdapter
```

## 4. Design

### 4.1 Kern: Rolle je Beitrag
- `write_spans`: je Draft `role_raw = draft.speaker_hint.get("role")`;
  `role = str(role_raw) if role_raw else adapter.mandate_role`;
  `Chamber(parliament=adapter.parliament, role=role)` **je Draft** (statt einmal vor der Schleife).
- `resolve_or_create_mandate`: zusätzlich `Mandate.role == chamber.role` in der Suche.
- Testkit `_speaker_ok`: Ist `"role"` im Hint, muss der Wert ein nicht-leerer `str` sein (sonst
  Prüf-ID `span_speaker`).

### 4.2 Parser
- `SPEAKER_MARKER_ST` (MULTILINE):
  `^(?:(?P<name>[^(\n]+?)\s+\((?P<party>[^)\n]+(?:\n[^)\n]+)?)\):$|(?P<pres>Vizepräsident(?:in)?|Präsident(?:in)?)\b[^:\n]*:$)`
- `classify(paren)`: `text = " ".join(paren.split())`; enthält `text` (ohne Groß/Klein) `minister`
  oder `staatssekret` → `(None, text)`; ist `text` genau `Berichterstatter` oder
  `Berichterstatterin` → `(None, None)`; sonst `(text, None)`.
- `segment_speeches_st`: `segment_speeches(normalized, marker=SPEAKER_MARKER_ST)`, je Segment
  `classify(seg.party)`; Name mit `" ".join(seg.name.split())`.
- `parse_header_st`: `(\d{1,4})\. Sitzung, \w+, (\d{2})\.(\d{2})\.(\d{4})` → `spoken_at` ISO
  (ungültiges Datum → `""`), `locator["sitzung"]`; `Stenografischer Bericht (\d{1,2}/\d{1,4})` →
  `locator["protokoll"]`.

### 4.3 Adapter
- Client: `httpx.AsyncClient(follow_redirects=False, timeout=30, headers={"User-Agent": …})`,
  lazy wie im DIP-Adapter; `aclose` idempotent.
- `_url(wp, n) = f"{base_url}/wp{wp}/{n:03d}stzg.pdf"`.
- `_exists(url)`: HEAD; 200 → `True`, 404 → `False`, alles andere (inkl. 3xx, Netzfehler) →
  `LandtagStError` (Meldung ohne Antworttext).
- `_highest(wp)`: `_exists(n=1)` falsch → 0. Sonst `hi = 1`; solange `hi * 2 <= 999` und
  `_exists(hi * 2)`: `hi *= 2`. Dann binär im Intervall `[hi, min(hi * 2, 1000))` die größte
  vorhandene Nummer suchen.
- `discover`: nicht `enabled` → `LandtagStDisabled("Abruf nicht freigegeben (WORTLAUT_LANDTAG_ST_ENABLED)")`.
  Sonst für `wp` in `(wahlperiode, wahlperiode + 1)`: `top = _highest(wp)`; Refs für
  `n` in `range(max(1, top - lookback + 1), top + 1)` aufsteigend, `source_type="plenarprotokoll"`,
  `hint={"wahlperiode": str(wp), "sitzung": str(n)}`.
- `fetch`: Host von `ref.origin_url` muss gleich dem Host von `base_url` sein, sonst
  `LandtagStError` **ohne** Anfrage. GET; Redirect, Status ≠ 200, fehlendes `%PDF-` oder ein
  Content-Type ohne `application/pdf` → `LandtagStError`. Ergebnis `RawSource(…,
  mime_type="application/pdf", retrieved_at=datetime.now(UTC))`.
- `normalize` = `extract_text(raw.raw_bytes)`.
- `parse`: `parse_header_st`, `segment_speeches_st`; je Segment `SpanDraft` mit
  `speaker_hint={"name": name, "party": party}` plus `"role": role`, falls `role`;
  `locator={**base_locator, "tagesordnungspunkt": seg.tagesordnungspunkt}`;
  `permalink=raw.origin_url`.

## 5. Testbare Akzeptanzkriterien

1. **AC1** Registry enthält `landtag-st` mit `trust_level="secondary"`,
   `rights_basis="amtliches_werk_p5"`; Klasse nennt `landtag-sachsen-anhalt` / `MdL`.
2. **AC2** `enabled=False`: `discover` wirft `LandtagStDisabled` (ist `AdapterError`), und der
   Transport hat **null** Anfragen gesehen.
3. **AC3** Nummern 1–118 vorhanden (wp8), wp9 leer, `lookback=3` → genau drei Refs auf
   `…/wp8/116stzg.pdf`, `117`, `118`; höchstens 30 HEAD-Anfragen; jede Anfrage trägt einen
   User-Agent, der `wortlaut` enthält.
4. **AC4** wp9 hat 1–2 → zusätzlich Refs für wp9 `001` und `002`.
5. **AC5** HEAD liefert 500 → `discover` wirft `LandtagStError`.
6. **AC6** `fetch`: fremder Host → `LandtagStError`, null Anfragen; 301 → Fehler; HTML statt PDF →
   Fehler; gültiges PDF → `RawSource` mit `application/pdf`.
7. **AC7** Synthetisches Protokoll: Datum `2026-06-26`, `locator` `protokoll="8/118"`,
   `sitzung="118"`.
8. **AC8** Umbrochene Rollenangabe: Die Rede der Ministerin ist ein eigenes Segment mit
   `role="Ministerin für Inneres und Sport"`, `party is None`; der Text des **Vorredners** enthält
   ihren Redetext **nicht**.
9. **AC9** `(Berichterstatter)` → `party is None`, `role is None`; `(AfD)` → `party == "AfD"`,
   `role is None`; Präsidium kein Segment; Zwischenruf `(Beifall bei der AfD)` bleibt im Text des
   Redners und ist kein Segment; für jedes Segment gilt die Offset-Invariante.
10. **AC10** `assert_conformant` ist grün für den Adapter (offline verdrahtet).
11. **AC11** Kern: Draft mit `"role": "Minister für Finanzen"` → Mandat mit genau dieser Rolle;
    dieselbe Person ohne Rolle und ohne Fraktion im selben Parlament → **zweites** Mandat mit Rolle
    `MdL`.
12. **AC12** Testkit: `speaker_hint` mit `"role": ""` → Verstoß `span_speaker`.
13. **AC13** Bundestag-Marker unverändert: bestehende Parser-Tests bleiben grün.

## 6. Testplan

Unit: `test_landtag_st_parse.py` (AC7–AC9, AC13 implizit), `test_landtag_st_adapter.py`
(AC1–AC6), `test_conformance_landtag_st.py` (AC10), `test_conformance.py` (AC12).
Integration: `test_parliament_scoping.py` (AC11). Fixture: synthetische PDF aus Generator.

## 7. Recht / Security

- § 5 UrhG laut Impressum; Wortlaut unverändert, Permalink je Span (§§ 62, 63).
- robots.txt: Abruf per Schalter aus, bis der Landtag zustimmt (§0b.1); identifizierbarer
  User-Agent; wenige Anfragen je Lauf.
- SSRF: Host-Pin, keine Redirects, PDF-Magic, Größen-/Seitenlimit über `extract_text`.
- Falschzuordnung (legal.md §5.1): AC8 sichert den umbrochenen Fall.
- Fixture synthetisch, keine echten Namen.

## 8. Risiken

- Lücke in der Nummernfolge → binäre Suche kann neuere Protokolle übersehen (nicht beobachtet).
- Weitere Rollenformen (z. B. „Staatssekretärin im …“) laufen über `staatssekret`; unbekannte
  Formen landen als Fraktionstext — sichtbar in der Stichprobe vor der Anhebung.

## 9. Definition of Done

Alle AC grün, Gates grün (ruff, format, mypy strict, import-linter, Unit + Integration,
Coverage ≥ 80 %), Sonar ohne neue Issues, Doku-Seite zur Quelle.

## 10. Files (NUR diese anlegen bzw. ändern)

- ändern: `src/wortlaut/ingest/protokoll_parse.py`, `src/wortlaut/ingest/settings.py`,
  `src/wortlaut/ingest/registry.py`, `src/wortlaut/ingest/conformance.py`,
  `src/wortlaut/ingest/adapter.py` (nur Docstring), `src/wortlaut/pipeline/spans.py`,
  `src/wortlaut/store/spans.py`, `docs/adapter-konformitaet.md`,
  `tests/unit/test_conformance.py`, `tests/integration/test_parliament_scoping.py`,
  `tests/unit/test_adapter_registry.py`
- neu: `src/wortlaut/ingest/landtag_st_parse.py`, `src/wortlaut/ingest/landtag_st.py`,
  `tests/fixtures/landtag_st/_make_protokoll.py`, `tests/unit/test_landtag_st_parse.py`,
  `tests/unit/test_landtag_st_adapter.py`, `tests/unit/test_conformance_landtag_st.py`,
  `docs/quelle-landtag-sachsen-anhalt.md`
- Die PDF `tests/fixtures/landtag_st/protokoll.pdf` erzeugt der Reviewer mit dem Generator.

## 11. Umsetzungsdetails

### `src/wortlaut/ingest/protokoll_parse.py`
Nur: `segment_speeches` bekommt den Keyword-Parameter `marker` (§3) und nutzt ihn statt
`SPEAKER_MARKER`. Docstring-Satz: „``marker`` erlaubt parlamentsspezifische Rednerzeilen (#145).“

### `src/wortlaut/ingest/landtag_st_parse.py`
Modul-Docstring (Zweck, Quelle 8/118 als Vorlage, Verweis auf Spec 0145 §0). Importiert nur stdlib
und `wortlaut.ingest.protokoll_parse` (`segment_speeches`). Regexe mit begrenzten Ziffern-Quantoren
wie in `protokoll_parse.py` (S8786). Umsetzung nach §4.2.

### `src/wortlaut/ingest/settings.py`
`LandtagStSettings` unter `DipSettings` anhängen; Docstring nennt den Schalter und dass ohne ihn
nichts abgerufen wird.

### `src/wortlaut/ingest/landtag_st.py`
Nach §4.3, Aufbau wie `dip.py` (Logger, Fehlerklassen oben, `from_env`, lazy Client). Meldungen
nie mit Antworttext. Methoden kurz halten (Sonar-Komplexität): `_exists`, `_highest`, `_refs_for`.

### `src/wortlaut/ingest/registry.py`
In `default_registry` nach dem DIP-Eintrag ein zweiter `AdapterEntry` für
`LandtagSachsenAnhaltAdapter` (gleiches Muster, `create=LandtagSachsenAnhaltAdapter.from_env`).
Modul-Docstring unverändert lassen.

### `src/wortlaut/ingest/adapter.py`
Nur ein Kommentar über `speaker_hint` in `SpanDraft`:
`# Schlüssel: "name" (Pflicht), "party" (optional), "role" (optional, #145)`.

### `src/wortlaut/pipeline/spans.py` und `src/wortlaut/store/spans.py`
Nach §4.1. Docstring von `resolve_or_create_mandate`: „get-or-create per ``(speaker, parliament,
role, party)``; ``party`` frei (R-CORE-03).“

### `src/wortlaut/ingest/conformance.py`
`_speaker_ok` nach §4.1 erweitern (eigene kleine Hilfsfunktion `_role_ok(hint)` ist erlaubt).

### `docs/adapter-konformitaet.md`
Zeile `span_speaker` ergänzen: „; ist `"role"` vorhanden, ein nicht-leerer `str`“.

### `tests/fixtures/landtag_st/_make_protokoll.py`
Muster `tests/fixtures/dip/_make_zweispaltiges_protokoll.py`, aber **einspaltig** (alle Zeilen
bei x=60, Zeilenabstand 14, Seite A4, bei Bedarf zweite Seite). Aufruf
`python tests/fixtures/landtag_st/_make_protokoll.py <ausgabe.pdf>`. Zeilen genau:

```
LANDTAG VON SACHSEN-ANHALT
Stenografischer Bericht 8/118
118. Sitzung, Freitag, 26.06.2026
Tagesordnungspunkt 27
Präsident Dr. Paul Beispiel:
Ich eröffne die Sitzung.
Max Mustermann (Berichterstatter):
Der Ausschuss empfiehlt die Annahme.
Erika Musterfrau (AfD):
Wir lehnen den Antrag ab.
(Beifall bei der AfD)
Das ist unsere Haltung.
Dr. Anna Beispielhaft (Ministerin für Inneres
und Sport):
Die Landesregierung sieht das anders.
Karl Probe (Minister für Finanzen):
Der Haushalt trägt das.
```

### `tests/unit/test_landtag_st_parse.py`
Lädt `tests/fixtures/landtag_st/protokoll.pdf` (Pfad relativ zu `__file__`), `extract_text`, dann:
`test_header` (AC7), `test_wrapped_role_is_own_speaker` (AC8: Segment „Dr. Anna Beispielhaft“ mit
Rolle und `party is None`; `"Die Landesregierung sieht das anders." not in` Text von „Erika
Musterfrau“), `test_rapporteur_and_party` (AC9), `test_presidium_and_heckle_not_speakers` (AC9),
`test_offsets` (AC9), `test_classify` (parametrisiert: `"AfD"` → `("AfD", None)`,
`"Berichterstatterin"` → `(None, None)`, `"Staats- und Kulturminister"` → `(None, …)`,
`"Ministerpräsident"` → `(None, "Ministerpräsident")`, `"Staatssekretärin"` → `(None, …)`,
`"Ministerin für\nJustiz"` → `(None, "Ministerin für Justiz")`), `test_invalid_date_is_empty`
(String mit `118. Sitzung, Freitag, 31.02.2026` → `spoken_at == ""`).

### `tests/unit/test_landtag_st_adapter.py`
Muster `tests/unit/test_dip_errors.py` (`httpx.MockTransport`, Client in `adapter._client`
einsetzen — der eingesetzte Client muss den User-Agent-Header selbst setzen; deshalb eine Methode
`_new_client(transport=None)` im Adapter vorsehen, die Tests mit `transport=` aufrufen, und `_client`
damit belegen). Handler zählt Anfragen in einer **test-lokalen** Liste (kein Modul-Zustand, S8997).
Tests für AC1–AC6 einzeln; jede Prüfung ein eigenes `assert`; in `pytest.raises` genau ein Aufruf
(Refs/Adapter vorher bauen, S5778).

### `tests/unit/test_conformance_landtag_st.py`
Adapter mit `enabled=True`, Transport: HEAD `wp8/001`–`003` → 200, sonst 404; GET der drei URLs →
Bytes der Fixture-PDF mit `content-type: application/pdf`. `ConformanceSamples(since=…,
failing_ref=SourceRef("https://fremd.example/x.pdf", "plenarprotokoll", {}), min_spans=3)`.
`await assert_conformant(adapter, samples)`.

### `tests/unit/test_conformance.py`
Unterklasse `_EmptyRole(_GoodAdapter)`, deren `parse` den Draft mit
`speaker_hint={"name": "Dr. Max Mustermann", "party": "AfD", "role": ""}` liefert (sonst wie
`_GoodAdapter.parse`); `_BROKEN`-Eintrag `(_EmptyRole, "span_speaker")`.

### `tests/unit/test_adapter_registry.py`
Test `test_default_registry_has_landtag_st` (AC1).

### `tests/integration/test_parliament_scoping.py`
Test `test_role_per_draft_and_distinct_mandates` (AC11): eigener Fake-Adapter (Parlament
`landtag-sachsen-anhalt`, Rolle `MdL`, Name `landtag-rollen-probe`), dessen `parse` **zwei** Drafts
für denselben Namen „Karl Probe“ liefert: einen mit `party=None, role="Minister für Finanzen"`,
einen mit `party=None` ohne `role`. Text „Satz eins. Satz zwei.“, Offsets je Satz. Aufbau wie
`_write_probe_span` (ingest_adapter-Zeile mit dem neuen Namen, source, Attestierung). Danach
`SELECT m.role FROM span s JOIN mandate m ON m.id = s.mandate_id WHERE s.source_id = … ORDER BY
s.text_start` → `["Minister für Finanzen", "MdL"]`, und `count(DISTINCT s.mandate_id) == 2`.

### `docs/quelle-landtag-sachsen-anhalt.md`
Eine Seite, Deutsch: Quelle und URL-Muster, Rechtsgrundlage (Impressum § 5/§ 62/§ 63), robots.txt-
Lage und Schalter `WORTLAUT_LANDTAG_ST_ENABLED` (Default aus, erst nach Zustimmung des Landtags),
Entdeckung über die Nummer, Vertrauen `secondary` und Weg zur Anhebung
(`WORTLAUT_ADAPTER_VERIFIED` gilt nur für Plugins — für eingebaute Adapter per Code-Änderung nach
Stichprobe), Rollen-Regel (Regierung/Berichterstatter), bekannte Grenzen (§8). Keine Zugangsdaten.

## 12. Do-NOT (hart)

- **Keine** Änderung am Bundestag-Marker `SPEAKER_MARKER`, an `parse_header`, an `dip.py`.
- **Keine** Migration, **keine** Änderung an `cli.py`, `serving/`, `models.py`.
- **Keine** Live-Netz-Aufrufe in Tests; **keine** echten Personennamen in Fixtures/Tests.
- **Keine** bestehende Datei komplett neu schreiben — nur gezielte Edits.
- **Keine** neuen Abhängigkeiten.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate fährt der Reviewer selbst.
