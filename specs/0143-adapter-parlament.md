# Increment-Spec: Adapter nennt sein Parlament — Mandate und Sprecher je Parlament getrennt (#143)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Ändere bzw. lege die Dateien aus **§10** wirklich auf der Platte an.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #143 · **Epic:** #94 (Quellen-Plugin-System, Schritt 6a — Voraussetzung für den
  ersten Landtag-Adapter) · **Status:** Reviewed (autonom)
- **Phase/Layer:** `ingest` (Vertrag, DIP, Testkit), `pipeline`, `store`, Doku, Tests
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)

## 0. Ausgangslage (gemessen)

- `src/wortlaut/pipeline/spans.py`: `_PARLIAMENT = "bundestag"`, an `resolve_or_create_mandate`
  übergeben. `src/wortlaut/store/spans.py`: `_DEFAULT_ROLE = "MdB"` als Rolle jedes Mandats.
- `resolve_or_create_speaker(session, full_name, external_ids=None)` sucht nur nach
  `Speaker.full_name == full_name` — quer über alle Parlamente.
- Spans entstehen über `reparse_source` → `write_spans(adapter=…)`; `reparse` wählt Quellen nach
  `adapter_name`, also parst jede Quelle mit ihrem eigenen Adapter. `adapter.parliament` ist damit
  an der richtigen Stelle verfügbar.
- Datenbank: `mandate.parliament` und `mandate.role` sind `Text NOT NULL` — **keine Migration**.
- mypy-Probe (Vertrag um zwei Felder erweitert, DIP-Adapter ergänzt): 14 Fehler, alle in
  Test-Fakes, die das Protocol eigenständig nachbauen. Unterklassen des DIP-Adapters erben die
  Werte.

### 0b. Entscheidungen

1. **Zwei Pflichtfelder im Vertrag**, wie `name`/`version`/`trust_level`: `parliament: str`
   (stabiler Kurzname: Kleinbuchstaben, Ziffern, Bindestriche, z. B. `bundestag`,
   `landtag-brandenburg`) und `mandate_role: str` (z. B. `MdB`, `MdL`).
2. **Sprecher je Parlament:** Wiederverwendet wird ein Sprecher mit exakt gleichem `full_name`,
   der ein Mandat in **diesem** Parlament hat **oder noch gar kein Mandat** (der Zustand zwischen
   Anlegen des Sprechers und seines Mandats in derselben Transaktion; hält die Funktion
   idempotent). Ein gleichnamiger Sprecher, dessen Mandate alle in anderen Parlamenten liegen, wird
   **nicht** wiederverwendet → neuer Sprecher. Lieber eine Person doppelt (später
   zusammenführbar) als zwei Personen in einer (falsch zugeordnetes Zitat, legal.md §5.1).
3. Bei mehreren Treffern gewinnt der älteste (`created_at`, dann `id`) — deterministisch.

## 1. Ziel

Ein Adapter für ein anderes Parlament legt Mandate mit seinem Parlament und seiner Rolle an, und
seine Redner werden nie mit gleichnamigen Rednern anderer Parlamente verschmolzen.

## 2. Nicht-Ziele

- Kein Landtag-Adapter (Schritt 6b). Kein Zusammenführen von Sprechern.
- Keine Änderung an `registry.py`, `cli.py`, Serving, Migrationen.

## 3. Öffentliche Signaturen

```python
# src/wortlaut/ingest/adapter.py — im Protocol IngestAdapter, direkt unter trust_level
parliament: str  # stabiler Kurzname, z. B. 'bundestag', 'landtag-brandenburg'
mandate_role: str  # Rolle der Redner, z. B. 'MdB', 'MdL'

# src/wortlaut/store/spans.py
async def resolve_or_create_speaker(
    session: AsyncSession,
    full_name: str,
    *,
    parliament: str,
    external_ids: dict[str, object] | None = None,
) -> UUID: ...

@dataclass(frozen=True)
class Chamber:
    """Parlament und Rolle eines Mandats (#143)."""

    parliament: str
    role: str

async def resolve_or_create_mandate(
    session: AsyncSession,
    *,
    speaker_id: UUID,
    party: str | None,
    active_from: date,
    chamber: Chamber,
) -> UUID: ...  # höchstens 5 Parameter (ruff PLR0913)

# src/wortlaut/ingest/conformance.py
PARLIAMENT_RE: re.Pattern[str]  # ^[a-z0-9]+(?:-[a-z0-9]+)*$
```

## 4. Design

- `write_spans` übergibt `parliament=adapter.parliament` an beide Resolver und
  `role=adapter.mandate_role` an `resolve_or_create_mandate`.
- Testkit: `_MEMBERS` um `"parliament"`, `"mandate_role"` erweitern (nach `"trust_level"`).
  `_check_identity` meldet mit Prüf-ID **`parliament`**: (a) `parliament` ist kein `str` oder
  passt nicht auf `PARLIAMENT_RE`; (b) `mandate_role` ist kein nicht-leerer `str`.

## 5. Testbare Akzeptanzkriterien

1. **AC1** `IngestAdapter` deklariert `parliament` und `mandate_role`; ein Objekt ohne
   `parliament` erfüllt das Protocol nicht (`isinstance` → `False`). Der DIP-Adapter liefert
   `bundestag` / `MdB`.
2. **AC2** `write_spans` mit einem Adapter `parliament="landtag-brandenburg"`,
   `mandate_role="MdL"`: Das Mandat jedes Spans hat `parliament == "landtag-brandenburg"` und
   `role == "MdL"` — ausdrücklich **nicht** `bundestag` / `MdB`.
3. **AC3** Gleicher `full_name`, Mandat in `bundestag` vorhanden → `resolve_or_create_speaker(...,
   parliament="landtag-brandenburg")` liefert eine **andere** id; zweimal mit
   `parliament="landtag-brandenburg"` (dazwischen Mandat angelegt) → dieselbe id.
4. **AC4** Idempotent ohne Mandat: zweimal derselbe Name und dasselbe Parlament, kein Mandat
   dazwischen → dieselbe id, genau eine `speaker`-Zeile.
5. **AC5** Bundestag unverändert: Sprecher mit Bundestagsmandat wird bei
   `parliament="bundestag"` wiederverwendet.
6. **AC6** `_PARLIAMENT` und `_DEFAULT_ROLE` kommen in `src/` nicht mehr vor.
7. **AC7** Testkit: `parliament="Landtag Brandenburg"`, `parliament=""` und `mandate_role=""`
   ergeben je einen Verstoß mit Prüf-ID `parliament`; der gute Adapter bleibt ohne Verstoß; ein
   Adapter ohne `parliament` ergibt `protocol`.
8. **AC8** `docs/adapter-konformitaet.md` führt `parliament`/`mandate_role` in der
   `protocol`-Zeile und eine neue Zeile für die Prüf-ID `parliament`.

## 6. Testplan

- Unit: `tests/unit/test_adapter_contract.py` (AC1), `tests/unit/test_conformance.py` (AC7).
- Integration (echtes Postgres): neue Datei `tests/integration/test_parliament_scoping.py`
  (AC2–AC5); `tests/integration/test_span_ingest.py` an neue Signatur anpassen.

## 7. Recht / Security

Verhindert falsche Zuordnung von Zitaten zwischen gleichnamigen Abgeordneten verschiedener
Parlamente (legal.md §5.1, §8 T8). Keine neuen Eingaben von außen, keine Geheimnisse.

## 8. Risiken

- Eine Person mit Mandaten in Bund und Land erscheint als zwei Sprecher — bewusst (§0b.2).
- Plugins (#141, offen) brauchen dieselben Felder; Rebase nach dessen Merge.

## 9. Definition of Done

Alle AC grün, Gates grün (ruff, format, mypy strict, import-linter, Unit + Integration,
Coverage ≥ 80 %), Sonar ohne neue Issues.

## 10. Files (NUR diese anlegen bzw. ändern)

- `src/wortlaut/ingest/adapter.py`, `src/wortlaut/ingest/dip.py`,
  `src/wortlaut/ingest/conformance.py`, `src/wortlaut/store/spans.py`,
  `src/wortlaut/pipeline/spans.py`, `docs/adapter-konformitaet.md`
- Tests mit eigenständigen Fake-Adaptern (nur die zwei Zeilen aus §11):
  `tests/integration/test_cli_ingest.py`, `tests/integration/test_pipeline_ingest.py`,
  `tests/unit/test_adapter_contract.py`, `tests/unit/test_adapter_registry.py`,
  `tests/unit/test_cli.py`, `tests/unit/test_cli_adapter_contract.py`,
  `tests/unit/test_cli_registry.py`, `tests/unit/test_cli_reparse.py`,
  `tests/unit/test_conformance.py`, `tests/unit/test_pipeline_order.py`,
  `tests/unit/test_reparse_pipeline.py`, `tests/unit/test_span_hash.py`
- `tests/integration/test_span_ingest.py` (Aufrufe anpassen)
- **neu:** `tests/integration/test_parliament_scoping.py`

## 11. Umsetzungsdetails

### `src/wortlaut/ingest/adapter.py`
Im Protocol direkt unter `trust_level: str  # ...` die zwei Zeilen aus §3. Modul-Docstring: einen
Satz ergänzen: „Der Adapter nennt sein Parlament und die Rolle seiner Redner (#143).“

### `src/wortlaut/ingest/dip.py`
Unter `trust_level = "verified_primary"`: `parliament = "bundestag"` und `mandate_role = "MdB"`.

### `src/wortlaut/store/spans.py`
- `_DEFAULT_ROLE` löschen. Modul-Docstring: „per exaktem ``full_name``“ ersetzen durch
  „per exaktem ``full_name`` je Parlament“.
- `resolve_or_create_speaker` (Signatur §3), Suche:

```python
has_mandate_here = exists().where(
    Mandate.speaker_id == Speaker.id, Mandate.parliament == parliament
)
has_any_mandate = exists().where(Mandate.speaker_id == Speaker.id)
stmt = (
    select(Speaker.id)
    .where(Speaker.full_name == full_name, or_(has_mandate_here, ~has_any_mandate))
    .order_by(Speaker.created_at, Speaker.id)
    .limit(1)
)
existing = await session.scalar(stmt)
```
  Rest (Anlegen) unverändert. Docstring: „get-or-create per ``full_name`` je Parlament: wieder-
  verwendet wird nur ein Sprecher mit Mandat in ``parliament`` oder ganz ohne Mandat (#143).“
  Imports: `exists`, `or_` aus `sqlalchemy`.
- `resolve_or_create_mandate`: `parliament: str` ersetzt durch `chamber: Chamber` (Signatur §3);
  Suche und Anlegen nutzen `chamber.parliament` und `chamber.role`.

### `src/wortlaut/pipeline/spans.py`
`_PARLIAMENT` samt Kommentar löschen. In `write_spans`:
`resolve_or_create_speaker(session, str(draft.speaker_hint["name"]), parliament=adapter.parliament)`
und in `resolve_or_create_mandate(...)` `chamber=chamber`, wobei vor der Schleife einmal
`chamber = Chamber(parliament=adapter.parliament, role=adapter.mandate_role)` gebildet wird.

### `src/wortlaut/ingest/conformance.py`
`PARLIAMENT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")` neben `_MIME_RE` (öffentlich, ohne
Unterstrich). `_MEMBERS` nach `"trust_level"` um `"parliament", "mandate_role"` erweitern. Am Ende
von `_check_identity` (vor der `rights_basis`-Prüfung):

```python
parliament = adapter.parliament
if not isinstance(parliament, str) or not PARLIAMENT_RE.match(parliament):
    findings.add("parliament", f"parliament ist kein Kurzname: {_short(parliament)}")
role = adapter.mandate_role
if not isinstance(role, str) or not role:
    findings.add("parliament", f"mandate_role ist keine nicht-leere Zeichenkette: {_short(role)}")
```

### Test-Fakes (alle Dateien der zweiten Aufzählung in §10)
In **jeder Klasse**, die eine Zeile `    trust_level = "..."` als Klassenattribut hat, direkt darunter
genau diese zwei Zeilen einfügen (gleiche Einrückung):

```python
    parliament = "bundestag"
    mandate_role = "MdB"
```
Ausnahme: `_BadTrustLevel` in `tests/unit/test_conformance.py` (erbt). Sonst **nichts** an diesen
Dateien ändern — außer den neuen Tests unten.

### `tests/unit/test_adapter_contract.py` — anhängen
Klasse `_WithoutParliament(_WithAclose)` ist nicht möglich (Attribut erbt); stattdessen eine
eigenständige Klasse `_WithoutParliament` wie `_WithoutRightsBasis`, aber **mit** `rights_basis =
"lizenz"`, `mandate_role = "MdB"` und `aclose`, **ohne** `parliament`. Test
`test_protocol_declares_parliament`: `isinstance(_WithoutParliament(), IngestAdapter) is False`;
`DipPlenarprotokollAdapter.parliament == "bundestag"`;
`DipPlenarprotokollAdapter.mandate_role == "MdB"` (jede Prüfung ein eigenes `assert`).

### `tests/unit/test_conformance.py` — ergänzen
Drei Unterklassen von `_GoodAdapter`: `_BadParliamentSpaces` (`parliament = "Landtag
Brandenburg"`), `_EmptyParliament` (`parliament = ""`), `_EmptyMandateRole` (`mandate_role = ""`).
In `_BROKEN` je ein Eintrag mit Prüf-ID `"parliament"`. Eine eigenständige Klasse
`_WithoutParliamentAttr` analog `_WithoutAclose`, aber mit `aclose = _GoodAdapter.aclose` und ohne
`parliament` → `_BROKEN`-Eintrag `"protocol"`.

### `tests/integration/test_span_ingest.py`
Die zwei Aufrufe in `test_speaker_get_or_create_idempotent` bekommen `parliament="bundestag"`.

### `tests/integration/test_parliament_scoping.py` — neu
Marker `pytestmark = pytest.mark.integration`. Fixtures wie in `test_span_ingest.py`
(`fresh_sessions`, aus `tests/integration/conftest.py`). Inhalt:

- `test_same_name_other_parliament_gets_new_speaker` (AC3): Sprecher „Dr. Gleichname“ +
  Mandat (`parliament="bundestag"`, `role="MdB"`, `party=None`, `active_from=date(2024, 1, 1)`)
  anlegen; dann `resolve_or_create_speaker(..., parliament="landtag-brandenburg")` → id ≠ erste.
- `test_same_parliament_reuses_speaker` (AC3): wie oben, aber nach dem Brandenburg-Sprecher ein
  Mandat `landtag-brandenburg`/`MdL` anlegen; zweiter Aufruf mit `landtag-brandenburg` → gleiche id.
- `test_speaker_without_mandate_is_reused` (AC4): zweimal `parliament="landtag-brandenburg"`
  ohne Mandat → gleiche id; `SELECT count(*) FROM speaker WHERE full_name = ...` == 1.
- `test_bundestag_speaker_reused` (AC5): Sprecher + Mandat `bundestag`; Aufruf mit `bundestag` →
  gleiche id.
- Mandate in den Tests über `chamber=Chamber(parliament=..., role=...)` anlegen.
- `test_write_spans_does_not_merge_with_bundestag_speaker` (AC3 über `write_spans`): vorher Sprecher
  „Dr. Gleichname“ mit Mandat `bundestag`/`MdB` anlegen, dann denselben Ablauf wie im AC2-Test;
  `SELECT s.speaker_id FROM span s WHERE s.source_id = ...` ist **nicht** die id des
  Bundestag-Sprechers.
- `test_write_spans_uses_adapter_parliament` (AC2): Fixture `seed_attestation` (conftest) — nach
  `insert_source` `await seed_attestation(session, source_id)` und commit, denn Spans sind nur auf
  attestierten Quellen erlaubt (DB-Trigger, ADR-0009).
- `test_write_spans_uses_adapter_parliament` (AC2): Ein Fake-Adapter (eigenständige Klasse) mit
  `name = "landtag-probe"`, `parliament = "landtag-brandenburg"`, `mandate_role = "MdL"`,
  `trust_level = "secondary"`, `rights_basis = "amtliches_werk_p5"`; `normalize` dekodiert UTF-8,
  `parse` liefert einen `SpanDraft` über den ganzen Text („Das ist ein Satz.“),
  `speaker_hint={"name": "Dr. Gleichname", "party": "SPD"}`, `spoken_at="2026-06-18"`,
  `locator={"sitzung": "35"}`, `permalink="https://example.org/35.docx"`. Vorher eine `source`-Zeile
  anlegen, Muster `_seed_source` in `tests/integration/test_timestamp_store.py`, aber **ohne**
  WORM: (1) per SQL `INSERT INTO ingest_adapter (name, version, trust_level) VALUES ('landtag-probe',
  '1.0.0', CAST('secondary' AS trust_level))`, commit; (2) `insert_source(session, NewSource(...))`
  aus `wortlaut.store.sources` mit `content_hash=content_hash(raw_bytes)` (aus
  `wortlaut.evidence.hashing`), `raw_bytes_ref="probe-ref"`, `archive_wayback=None`,
  `archive_today=None`, `origin_url="https://example.org/35.docx"`, `source_type="plenarprotokoll"`,
  `rights_basis="amtliches_werk_p5"`, `adapter_name="landtag-probe"`, `adapter_version="1.0.0"`,
  `byte_size=len(raw_bytes)`, `mime_type="text/plain"`, `retrieved_at=datetime.now(UTC)`,
  `normalized_text="Das ist ein Satz."`; (3) `await write_spans(session, adapter=adapter, raw=raw,
  normalized="Das ist ein Satz.", source_id=source_id)` aus `wortlaut.pipeline.spans` mit dem
  passenden `RawSource`; Rückgabe == 1. Danach per SQL `SELECT m.parliament, m.role FROM span s
  JOIN mandate m ON m.id = s.mandate_id WHERE s.source_id = ...` prüfen: `parliament ==
  "landtag-brandenburg"`, `role == "MdL"`, und je ein `assert` auf `!= "bundestag"` / `!= "MdB"`.

Jede Prüfung ein eigenes `assert` (kein `assert a and b`). In `pytest.raises`-Blöcken genau ein
Aufruf. Keine ausgeschriebenen DSNs/Passwörter.

### `docs/adapter-konformitaet.md`
In der `protocol`-Zeile nach `trust_level` die Mitglieder `parliament`, `mandate_role` ergänzen.
Neue Zeile nach `identity`: ``| `parliament` | `parliament` ist ein Kurzname (`^[a-z0-9]+(?:-[a-z0-9]+)*$`, z. B. `landtag-brandenburg`); `mandate_role` ist ein nicht-leerer `str` (z. B. `MdL`) |``

## 12. Do-NOT (hart)

- **Keine** Migration, **keine** Änderung an `models.py`, `registry.py`, `cli.py`, `serving/`.
- **Keine** bestehende Datei komplett neu schreiben — nur gezielte Edits.
- **Keine** Änderung an bestehenden Tests außer den in §11 genannten.
- **Keine** neuen Abhängigkeiten, **keine** Live-Netz-Aufrufe in Tests.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate fährt der Reviewer selbst.
