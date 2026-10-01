# Increment-Spec: Spans nur für attestierte Quellen (#126)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #126 · **Epic:** #123 (Increment 2) · **Status:** Draft
- **Phase/Layer:** phase/1-mvp · Migration, `pipeline`, `store`, `cli`, Tests
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Entscheidung: [ADR-0009](../docs/adr/0009-pflicht-anker-und-zitierfaehigkeit.md) §1, Konsequenzen
- Baut auf **#124** (`source_archive`, `attest`, live seit 2026-10-01, 9/9 attestiert), **#118** (`reparse`).

## 0. Ausgangslage

ADR-0009 erlaubt Spans und jede Ausgabe nur für Quellen mit Eigenschaft A. Seit #124 gibt es die
Tabelle `source_archive`, in die nur bei nachgewiesener Byte-Gleichheit geschrieben wird. Noch
**hängt aber nichts daran**: `ingest` und `reparse` schreiben Spans ohne Blick auf die
Attestierung, und der Read-Pfad liefert sie aus.

Die ADR verlangt, dass die Zusicherung **in der Datenbank** entsteht und **jeden** Weg zu Spans
erfasst — nicht nur die heute bekannten.

### 0a. Vorklärung: der Trigger zwingt `ingest` zur Änderung

`ingest` schreibt die Spans heute direkt nach dem Insert der Quelle (`pipeline/ingest.py`,
Schritt 9). In diesem Moment kann es noch keine Attestierung geben: Ein frischer Capture ist erst
Stunden bis Tage später abrufbar (#124, §0d). Mit dem Trigger würde jeder Ingest einer neuen Quelle
beim ersten Span scheitern.

**Entscheidung (Stakeholder, 2026-10-01):** `ingest` schreibt keine Spans mehr. Er erfasst nur
noch: Hash, WORM, Zeitstempel-fähig, eingefrorener `normalized_text`. Spans entstehen per
`reparse`, sobald `attest` die Quelle bestätigt hat. Ablauf im Betrieb:

```
ingest → timestamp → attest → reparse
```

Neue Zitate erscheinen damit erst nach der Attestierung. Das ist die Absicht der ADR, kein Nebeneffekt.

### 0b. Vorklärung: Bestand vor dem Umschalten

Der Trigger greift nur für **neue** Inserts. Spans, die schon existieren, prüft er nicht. Liefe die
Migration auf einer Datenbank, in der Spans zu unattestierten Quellen liegen, blieben genau diese
Spans ohne Bezeugung bestehen. Deshalb **verweigert die Migration selbst das Upgrade**, solange
solche Spans existieren — mit einer Meldung, die sagt, was vorher zu tun ist (`attest` fahren).
Auf dem Dedicated sind seit 2026-10-01 alle 9 Quellen attestiert; das Upgrade läuft dort durch.

## 1. Ziel

1. Ein `BEFORE INSERT`-Trigger auf `span` verweigert jeden Insert für eine Quelle ohne
   `source_archive`-Zeile.
2. Die Migration bricht ab, wenn es bereits Spans zu unattestierten Quellen gibt.
3. `ingest` schreibt keine Spans mehr.
4. `reparse` wählt nur attestierte Quellen ohne Spans.
5. Der Read-Pfad liefert nichts für unattestierte Quellen — auch den Quellen-Beleg nicht.

## 2. Nicht-Ziele (Scope-Grenze)

- **Keine** Anzeige der Attestierung in `/verify` (eigenes Increment, Stakeholder-Entscheidung).
- **Kein** Entfernen von `chk_archive`, `source.archive_wayback` oder des Wayback-Insert-Gates in
  `ingest` (Increment 3).
- **Keine** Änderung an `attest`, `timestamp`, `source_archive`.
- **Kein** Umbenennen von `reparse`.

## 3. Betroffene Interfaces / Öffentliche Signaturen

Keine neuen öffentlichen Signaturen. Geändert:

```python
# src/wortlaut/pipeline/ingest.py — ingest_source: Signatur unverändert.
#   IngestOutcome.span_count bleibt als Feld bestehen und ist für `ingest` immer 0.

# src/wortlaut/store/reparse.py — Signaturen unverändert; Auswahl zusätzlich: nur attestiert.
#   list_sources_without_spans(session, *, adapter_name, limit)
#   lock_source_if_spanless(session, source_id) -> bool   (zusätzlich: False, wenn unattestiert)

# src/wortlaut/store/read.py — Signaturen unverändert; zusätzlicher Pflichtfilter.
```

Neu nur für Tests: die Fixture `seed_attestation` in `tests/integration/conftest.py`.

## 4. Design — die vier Entscheidungen

### 4.1 Trigger statt Check-Constraint

Ein Check-Constraint kann keine andere Tabelle lesen. Deshalb Migration `0006` mit einer
Trigger-Funktion `require_source_attestation()`, die `BEFORE INSERT ON span` prüft, ob eine
`source_archive`-Zeile für `NEW.source_id` existiert, und sonst mit einer eindeutigen Meldung
abbricht. Weil `source_archive` append-only ist, kann eine einmal bestehende Attestierung nicht
nachträglich verschwinden; die Prüfung beim Insert genügt.

### 4.2 Die Migration prüft den Bestand vor dem Umschalten

Vor dem Anlegen des Triggers zählt die Migration Spans, deren Quelle keine `source_archive`-Zeile
hat. Ist die Zahl größer 0, bricht sie mit `RAISE EXCEPTION` ab und nennt die Zahl und den nötigen
Schritt (`attest` fahren). Damit kann Increment 2 nicht ausgerollt werden, bevor der Bestand
bezeugt ist. Umgesetzt als `DO $$ … $$`-Block in derselben Migration.

### 4.3 `ingest` endet beim Erfassen

In `ingest_source` entfällt Schritt 9 (Spans). `normalize` läuft weiter **vor** dem Insert und
friert `normalized_text` ein (Option A, #42) — `reparse` braucht genau diesen Text. `parse` wird in
`ingest` **nicht mehr aufgerufen**. `IngestOutcome.span_count` bleibt als Feld bestehen (ist immer
0), damit die Summary-Zeile von `ingest` ihr Format behält.

### 4.4 Doppelte Absicherung im Read-Pfad

Der Trigger verhindert neue Spans unattestierter Quellen. Der Read-Pfad filtert **zusätzlich**:
Ein Span wird nur ausgeliefert, wenn seine Quelle eine `source_archive`-Zeile hat; der Quellen-
Beleg (`/v1/sources/{id}`) ebenso. Grund: Ab Increment 3 entstehen Quellen ohne Attestierung als
Normalfall, und der Beleg-Endpunkt darf dann kein Schlupfloch sein (ADR-0009, Konsequenzen).
Gleiches Muster wie der bestehende fail-safe-Filter `_PUBLIC_FILTER`.

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)

- **AC1 — Trigger.** *Given* eine Quelle ohne `source_archive`-Zeile. *When* ein Insert in `span`.
  *Then* die DB verweigert ihn. *Given* dieselbe Quelle nach einem Insert in `source_archive`.
  *Then* der Span-Insert gelingt.
- **AC2 — Migration verweigert ungesicherten Bestand.** *Given* eine DB auf Revision `0005` mit
  einer Quelle, die einen Span, aber keine `source_archive`-Zeile hat. *When* Upgrade auf `head`.
  *Then* das Upgrade schlägt fehl, die Meldung nennt `attest`, und der Trigger existiert nicht.
  *Given* derselbe Bestand mit Attestierung. *Then* das Upgrade gelingt.
- **AC3 — Downgrade.** `0006` lässt sich auf `0005` zurückfahren; danach ist ein Span-Insert für
  eine unattestierte Quelle wieder möglich.
- **AC4 — `ingest` ohne Spans.** *Given* die Protokoll-Fixture aus #41. *When* `ingest_source`.
  *Then* Status `inserted`, `span_count == 0`, **0** Zeilen in `span` für diese Quelle,
  `normalized_text` ist gesetzt, `parse` wurde nicht aufgerufen.
- **AC5 — Der neue Weg zu Spans.** *Given* dieselbe Quelle nach `ingest`. *When* Attestierung
  eingetragen, dann `reparse_source`. *Then* Status `reparsed` mit genau so vielen Spans, wie der
  Parser für die Fixture liefert.
- **AC6 — `reparse` nur attestiert.** *Given* zwei spanlose Quellen, eine attestiert, eine nicht.
  *Then* `list_sources_without_spans` liefert nur die attestierte; `lock_source_if_spanless`
  liefert für die unattestierte `False`.
- **AC7 — Read-Pfad als zweite Absicherung.** *Given* eine attestierte Quelle mit öffentlichem
  Span und eine zweite Quelle mit öffentlichem Span **ohne** Attestierung. Die zweite lässt sich
  nur anlegen, wenn der Trigger umgangen wird — genau das simuliert der Test (Altbestand oder
  Fehlkonfiguration): In **einer** Testsitzung `SET LOCAL session_replication_role = replica`, dann
  die Inserts, dann Commit. *Then* Suche, Span-Detail und Kontext liefern nur Spans der
  attestierten Quelle; `/v1/sources/{id}` liefert für die unattestierte Quelle 404.
- **AC8 — Bestehendes Verhalten erhalten.** Alle Read-Pfad-, Schema- und Reparse-Tests sind
  weiterhin grün, **nachdem** ihre Testdaten um die Attestierung ergänzt wurden. Keine Assertion
  wird abgeschwächt, entfernt oder auf einen schwächeren Vergleich umgestellt; die einzigen
  geänderten Erwartungen sind die aus AC4 (`ingest` erzeugt 0 Spans, `parse` wird nicht gerufen).
- **AC9 — Doku.** `docs/deploy.md` nennt die Reihenfolge `ingest` → `timestamp` → `attest` →
  `reparse` und dass neue Zitate erst nach `attest` erscheinen.

## 6. Testplan (Test-zu-AC-Mapping)

| AC | Test | Datei | Art |
|---|---|---|---|
| AC1 | `test_span_requires_attestation` | `tests/integration/test_span_schema.py` | Integration |
| AC2, AC3 | `test_migration_refuses_unattested_spans` · `test_migration_downgrade_removes_guard` | `tests/integration/test_span_attestation.py` (neu) | Integration |
| AC4 | `test_ingest_creates_no_spans` | `tests/integration/test_span_ingest.py` | Integration |
| AC4 | `test_normalize_and_parse_called_phase1` (angepasst: `parse_calls == 0`) | `tests/unit/test_pipeline_order.py` | Unit |
| AC5 | `test_phase1_ingest_creates_spans` (umgebaut: ingest → Attestierung → reparse) | `tests/integration/test_span_ingest.py` | Integration |
| AC6 | `test_reparse_selects_only_attested` | `tests/integration/test_reparse.py` | Integration |
| AC7 | `test_unattested_source_never_served` | `tests/integration/test_serving_api.py` | Integration |
| AC8 | alle bestehenden Tests | — | — |

## 7. Recht / Security

- **Beweiskette:** Die Zitierfähigkeits-Grenze aus ADR-0009 wird DB-Wahrheit (R-DATA-01,
  ADR-0003-Grundsatz „Invarianten in der DB, nicht nur im Code"). Kein Pfad, auch kein künftiger,
  kann einen Span für eine unbezeugte Quelle anlegen.
- **Kein Schlupfloch über den Beleg-Endpunkt** (AC7).
- Keine neuen ENV-Variablen, keine Secrets, kein Netzzugriff.

## 8. Risiken & offene Fragen

- **Sichtbare Verzögerung neuer Zitate.** Gewollt (0a). Der Rückstand wird in Increment 3
  sichtbar gemacht.
- **Testdaten-Umbau:** Viele Integrationstests legen Spans direkt an. Sie brauchen jetzt vorher
  eine Attestierung. Das ist Datenvorbereitung, keine Verhaltensänderung — der Review prüft genau,
  dass keine Assertion dabei schwächer wird (AC8).
- **Ausrollen:** Erst `attest` (ist auf dem Dedicated erledigt), dann das Image mit `0006`. Die
  Migration erzwingt diese Reihenfolge (AC2).

## 9. Definition of Done (Verweis)

Siehe `docs/engineering.md`. **Abnahme im Betrieb:** Image ausrollen, Migration `0006` läuft durch
(alle 9 Quellen attestiert), `/v1/search` liefert unverändert Treffer, `reparse --dry-run` zeigt 0.

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu:**
- `migrations/versions/0006_span_requires_attestation.py`
- `tests/integration/test_span_attestation.py`

**Ändern (Produktivcode):**
- `src/wortlaut/pipeline/ingest.py`
- `src/wortlaut/store/reparse.py`
- `src/wortlaut/pipeline/reparse.py` — nur Modul-Docstring
- `src/wortlaut/store/read.py`
- `docs/deploy.md`

**Ändern (Tests):**
- `tests/integration/conftest.py` — Fixture `seed_attestation`
- `tests/integration/test_span_schema.py`
- `tests/integration/test_span_ingest.py`
- `tests/integration/test_reparse.py`
- `tests/integration/test_serving_api.py`
- `tests/integration/test_cli_ingest.py`
- `tests/unit/test_pipeline_order.py`

Weitere Testdateien nur, wenn sie ohne Attestierung rot werden — dann **ausschließlich** um
`seed_attestation`-Aufrufe ergänzen und das im Abschlussbericht nennen.

## 11. Umsetzungsdetails je Datei

### `migrations/versions/0006_span_requires_attestation.py` (neu)

Muster `0005_source_archive.py`; `revision = "0006"`, `down_revision = "0005"`. Modul-Docstring
mit Verweis auf ADR-0009 und Spec 0126. `upgrade()` in genau dieser Reihenfolge:

```sql
DO $$
DECLARE n bigint;
BEGIN
  SELECT count(*) INTO n FROM span sp
   WHERE NOT EXISTS (SELECT 1 FROM source_archive sa WHERE sa.source_id = sp.source_id);
  IF n > 0 THEN
    RAISE EXCEPTION
      '0006: % Span(s) gehoeren zu Quellen ohne Attestierung — vorher `python -m wortlaut attest` fahren (ADR-0009)', n;
  END IF;
END
$$;

CREATE FUNCTION require_source_attestation() RETURNS trigger AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM source_archive WHERE source_id = NEW.source_id) THEN
    RAISE EXCEPTION 'span: Quelle % ist nicht attestiert (ADR-0009)', NEW.source_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_span_requires_attestation BEFORE INSERT ON span
  FOR EACH ROW EXECUTE FUNCTION require_source_attestation();
```

Jede Anweisung in einem eigenen `op.execute(...)`. `downgrade()`: Trigger, dann Funktion droppen.

### `src/wortlaut/pipeline/ingest.py` (ändern)

- Schritt 9 (der `if normalized is not None:`-Block mit `write_spans`) entfällt; `span_count`
  wird nicht mehr berechnet, die Rückgabe übergibt `0`.
- Den Import `write_spans` und alle dadurch unbenutzten Importe entfernen.
- Modul-Docstring: „… → insert source" statt „… → spans"; ein Satz, dass Spans seit #126 erst per
  `reparse` nach `attest` entstehen (ADR-0009).
- `normalize` und `_safe_normalize` bleiben unverändert und vor dem Insert.

### `src/wortlaut/store/reparse.py` (ändern)

- `list_sources_without_spans`: zusätzlich `exists().where(SourceArchive.source_id == Source.id)`
  als Bedingung (nur attestierte Quellen).
- `lock_source_if_spanless`: nach dem `FOR UPDATE` zusätzlich prüfen, ob eine `source_archive`-
  Zeile existiert; ohne → `return False`. Docstrings entsprechend.

### `src/wortlaut/pipeline/reparse.py` (ändern)

Nur der Modul-Docstring: Ein Satz, dass `reparse` seit #126 der reguläre Weg zu Spans ist und nur
attestierte Quellen wählt. **Keine** Code-Änderung.

### `src/wortlaut/store/read.py` (ändern)

- `_PUBLIC_FILTER` um eine Bedingung ergänzen:
  `"AND EXISTS (SELECT 1 FROM source_archive sa WHERE sa.source_id = src.id)"`.
- `_SOURCE_SQL` um dieselbe Bedingung für die Quelle ergänzen (dort heißt die Tabelle `source`
  ohne Alias: `... WHERE sa.source_id = source.id`).
- Kommentar über `_PUBLIC_FILTER` um „… UND die Quelle ist fremdbezeugt (ADR-0009)" ergänzen.

### `docs/deploy.md` (ändern)

Im Abschnitt „Erfassungs-Läufe": Reihenfolge `ingest` → `timestamp` → `attest` → `reparse`;
`ingest` erzeugt keine Spans mehr; neue Zitate erscheinen erst nach `attest` und `reparse`;
`spans_total` in der `ingest`-Zeile ist deshalb immer 0. Vor dem Ausrollen dieser Version muss
`attest` gelaufen sein, sonst verweigert die Migration das Upgrade.

### `tests/integration/conftest.py` (ändern)

Neue Fixture, ohne bestehende Fixtures anzufassen:

```python
_ATTEST_SQL = text(
    "INSERT INTO source_archive "
    "(source_id, archiver, snapshot_url, snapshot_at, verified_sha256) "
    "SELECT id, 'wayback', 'https://web.archive.org/web/20260101000000/' || origin_url, "
    "now(), content_hash FROM source WHERE id = CAST(:sid AS uuid)"
)


@pytest.fixture
def seed_attestation() -> Callable[[AsyncSession | AsyncConnection, UUID | str], Awaitable[None]]:
    """Testdaten: trägt für eine Quelle eine gültige Attestierung ein (Hash = content_hash)."""

    async def _seed(executor: AsyncSession | AsyncConnection, source_id: UUID | str) -> None:
        await executor.execute(_ATTEST_SQL, {"sid": str(source_id)})

    return _seed
```

Der Trigger aus #124 akzeptiert die Zeile, weil `verified_sha256` aus `content_hash` kommt. Der
Aufrufer committet selbst, wie bei seinen übrigen Testdaten.

### Bestehende Tests (ändern)

Grundregel: **Nur Testdaten ergänzen, keine Erwartung abschwächen.** Wo ein Test eine Quelle
anlegt und danach Spans einfügt oder erwartet, wird **zwischen** Quelle und Span
`await seed_attestation(<session oder conn>, <source_id>)` eingefügt.

- `test_span_schema.py`: in `_seed_source` (bzw. direkt danach) attestieren; neuer Test AC1.
- `test_serving_api.py`: in `_seed` jede Quelle attestieren; neuer Test AC7 legt seine
  unattestierte Quelle samt Span selbst an — in einer eigenen Session mit
  `await session.execute(text("SET LOCAL session_replication_role = replica"))` vor den Inserts
  (deaktiviert Trigger nur für diese Transaktion; der Test-Container läuft als Superuser). Diese
  Umgehung ist **ausschließlich** in diesem einen Test erlaubt.
- `test_reparse.py`: wo eine Quelle für `reparse` vorbereitet wird, attestieren; neuer Test AC6.
  Der AC2-Gleichheitstest aus #118 vergleicht künftig `reparse`-Spans in **beiden** DBs — die
  Gleichheitsaussage bleibt erhalten.
- `test_span_ingest.py`: `test_phase1_ingest_creates_spans` → nach `ingest` attestieren und
  `reparse_source` aufrufen; alle Assertions über die Spans bleiben. Übrige Tests der Datei
  sinngemäß. Neuer Test AC4.
- `test_cli_ingest.py`, `test_end_to_end_single_source`: die Erwartung `span_count >= 1` wird zu
  `span_count == 0` mit Kommentar „#126: ingest erzeugt keine Spans mehr (ADR-0009)".
- `test_pipeline_order.py`, `test_normalize_and_parse_called_phase1`: `parse_calls == 0` statt
  `== 1`, `normalize` weiterhin genau einmal; Docstring und Kommentar anpassen.

### `tests/integration/test_span_attestation.py` (neu)

AC2 und AC3 mit `fresh_pg_dsn`, `upgrade_head` und `downgrade_to` aus `wortlaut.store.migrations`:
`upgrade_head` → `downgrade_to(dsn, "0005")` → Quelle, Sprecher, Mandat und Span per rohem SQL
anlegen (ohne Attestierung) → `upgrade_head` muss fehlschlagen (`pytest.raises`, Meldung enthält
`attest`), und `pg_trigger` kennt `trg_span_requires_attestation` nicht. Dann attestieren →
`upgrade_head` gelingt, Trigger existiert. AC3: `downgrade_to(dsn, "0005")` → Span-Insert für eine
weitere unattestierte Quelle gelingt. SQL-Bausteine für Quelle/Sprecher/Mandat/Span nach dem
Vorbild von `test_span_schema.py`.

- **Sonar-Muster:** genau **ein** Aufruf je `pytest.raises`-Block; keine zusammengesetzten Asserts
  (`assert a and b`); kein Lambda in `patch()`; Literale, die dreimal vorkommen, als Konstante;
  `async def` nur, wo awaited wird.

## 12. Do-NOT (hart)

- **Keine** Assertion abschwächen, entfernen oder durch einen schwächeren Vergleich ersetzen —
  außer den beiden in §11 benannten Erwartungen aus AC4.
- **Kein** `skip`, `xfail` oder auskommentierter Test.
- **Keine** Änderung an bestehenden Migrationen (`0001`–`0005`).
- **Keine** Änderung an `attest`, `timestamp`, `serving/`, `pipeline/verify.py`.
- **Kein** UPDATE/DELETE auf `source`, `span`, `span_state`, `source_archive`.
- **Keine** Änderung an `write_spans`.
- **Keine** neuen Abhängigkeiten in `pyproject.toml`.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate fährt der Reviewer selbst. Falls du doch lokal testen willst: Marker-Ausdruck
`-m "not integration and not live"`.
