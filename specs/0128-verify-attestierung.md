# Increment-Spec: `/verify` und Quellen-Beleg zeigen die Attestierung (#128)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #128 · **Epic:** #123 · **Status:** Draft
- **Phase/Layer:** phase/1-mvp · `store`, `pipeline`, `serving`
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#124** (`source_archive`), **#126** (Ausgabe nur für attestierte Quellen), **#76**
  (Muster: additive `timestamp_*`-Felder in `/verify`).

## 0. Ausgangslage

Seit #126 wird nur ausgespielt, was attestiert ist — sichtbar ist die Attestierung aber nirgends.
`/v1/spans/{id}/verify` und `/v1/sources/{id}` zeigen `archive_wayback`, also die URL, die Save
Page Now **beim Erfassen** gemeldet hat. Bei 6 von 9 Bestandsquellen ist das **nicht** der
Snapshot, der die Quelle bezeugt (Abnahme #124: die Attestierung kam über Crawler-Snapshots mit
denselben Bytes). Wer die Beweiskette nachprüfen will, findet den bezeugenden Snapshot nicht.

### 0a. Vorklärung: kein Live-Abruf

`/verify` ruft den Internet Archive **nicht** an. Ausgewiesen wird, was in `source_archive` steht.
Dass die Bytes dieses Snapshots gleich `content_hash` sind, ist dort **per Trigger** garantiert
(#124). Ein Abruf je Anfrage brächte Latenz, Last auf einem fremden Dienst und eine neue
Fehlerquelle im Serving-Layer — ohne Beweisgewinn, denn die Snapshot-URL kann jeder selbst prüfen.

### 0b. Vorklärung: additiv, kein Gate

Muster `timestamp_*` (#76): neue Felder **am Ende**, `ok` und `status` bleiben hash-only und
ändern sich nicht. Für ausgespielte Spans ist die Attestierung seit #126 immer vorhanden;
`verify_source` kann aber auch für eine unattestierte Quelle aufgerufen werden und meldet dann
`missing`.

## 1. Ziel

`VerifyReport`, `VerifyResult` und `SourceEvidence` tragen die Attestierung: Status, Archivar,
Snapshot-URL, Snapshot-Zeitpunkt und den geprüften Hash.

## 2. Nicht-Ziele

- **Kein** Netzzugriff im Serving- oder Verify-Pfad.
- **Keine** Änderung an `ok`/`status`, an bestehenden Feldern oder ihrer Reihenfolge.
- **Kein** Entfernen von `archive_wayback` (Increment 3 des Epics).
- **Keine** Änderung an Migrationen, `attest`, `ingest`, `reparse`.

## 3. Betroffene Interfaces / Öffentliche Signaturen

```python
# src/wortlaut/store/attestations.py — additiv
@dataclass(frozen=True)
class SourceArchiveRow:
    archiver: str
    snapshot_url: str
    snapshot_at: datetime
    verified_sha256: str

async def get_attestations_for_source(
    session: AsyncSession, source_id: UUID
) -> list[SourceArchiveRow]: ...      # sortiert nach created_at, id

# src/wortlaut/pipeline/verify.py — VerifyReport, additiv am Ende
    attestation_status: Literal["ok", "missing"] = "missing"
    attestation_archiver: str | None = None
    attestation_snapshot_url: str | None = None
    attestation_snapshot_at: datetime | None = None
    attestation_verified_sha256: str | None = None

# src/wortlaut/serving/schemas.py — VerifyResult und SourceEvidence, je additiv am Ende
    attestation_status: str
    attestation_archiver: str | None
    attestation_snapshot_url: str | None
    attestation_snapshot_at: datetime | None
    attestation_verified_sha256: str | None
```

## 4. Design

- **Welche Zeile:** die erste nach `created_at, id` (Muster `_timestamp_fields`). Heute gibt es je
  Quelle höchstens eine (`UNIQUE (source_id, archiver)`, einziger Archivar `wayback`).
- **`verify_source`:** liest die Attestierung **in jedem Zweig, in dem die Quelle existiert** — also
  auch bei `worm_missing` und `hash_mismatch`. Gerade bei einem Fehlbefund ist der bezeugende
  Snapshot die Information, mit der man ihn klärt. Bei `source_not_found` bleibt es bei `missing`.
- **`/v1/sources/{id}`:** Der Endpunkt holt die Attestierung über `get_attestations_for_source`
  und füllt die Felder. (Ohne Attestierung liefert `get_source` seit #126 ohnehin `None` → 404.)

## 5. Testbare Akzeptanzkriterien

- **AC1 — Store.** *Given* eine Quelle mit Attestierung. *Then* `get_attestations_for_source`
  liefert genau eine Zeile mit `archiver`, `snapshot_url`, `snapshot_at` und
  `verified_sha256 == content_hash`. Ohne Attestierung → `[]`.
- **AC2 — `verify_source`.** Attestierte Quelle, Hash passt → `attestation_status == "ok"` und alle
  vier Felder gesetzt; `ok`/`status` unverändert (`True`/`"ok"`).
- **AC3 — Fehlbefund behält die Attestierung.** Attestierte Quelle, WORM-Bytes passen nicht
  (`hash_mismatch`) → `ok is False`, `status == "hash_mismatch"` **und** `attestation_status == "ok"`
  mit gesetzter Snapshot-URL. Dasselbe bei `worm_missing`.
- **AC4 — Unattestiert.** Quelle ohne Attestierung → `attestation_status == "missing"`, alle
  vier Felder `None`; `ok`/`status` unverändert gegenüber heute.
- **AC5 — `/v1/spans/{id}/verify`.** Die Antwort enthält die fünf Felder mit den Werten aus AC2.
- **AC6 — `/v1/sources/{id}`.** Die Antwort enthält die fünf Felder; `attestation_snapshot_url`
  ist die URL aus `source_archive`, **nicht** `archive_wayback` (Test mit verschiedenen Werten).
- **AC7 — Kein Netz.** `pipeline/verify.py` und `serving/` importieren weder
  `wortlaut.archive.wayback_lookup` noch `wortlaut.archive.archiver` (import-linter-Contract).
- **AC8 — Bestand.** Alle bestehenden Tests bleiben ohne Änderung grün; bestehende Felder und
  Werte der Antworten sind unverändert.

## 6. Testplan

| AC | Test | Datei | Art |
|---|---|---|---|
| AC1 | `test_get_attestations_for_source` | `tests/integration/test_attest.py` | Integration |
| AC2–AC4 | `test_verify_reports_attestation` · `test_verify_mismatch_keeps_attestation` · `test_verify_unattested_source` | `tests/integration/test_verify_integration.py` | Integration |
| AC5, AC6 | `test_verify_endpoint_shows_attestation` · `test_source_evidence_shows_attesting_snapshot` | `tests/integration/test_serving_api.py` | Integration |
| AC7 | import-linter | `.importlinter` | Architektur |

## 7. Recht / Security

- Keine neuen Daten: ausgewiesen wird nur, was schon im Ledger steht. Kein Roh-/WORM-Interna.
- Kein Netzzugriff im Serving-Pfad (AC7).

## 8. Risiken

- **Feld-Zuwachs in einer öffentlichen API.** Additiv am Ende; Clients, die unbekannte Felder
  ignorieren, sind nicht betroffen. Die Demo-Seite (#44) liest diese Felder heute nicht.

## 9. Definition of Done

Siehe `docs/engineering.md`. Abnahme im Betrieb: `/verify` einer Stichprobe aus 21/97 zeigt den
Snapshot vom 28.09. (nicht den eigenen Capture aus `archive_wayback`).

## 10. Files (NUR diese anlegen bzw. ändern)

- `src/wortlaut/store/attestations.py`
- `src/wortlaut/pipeline/verify.py`
- `src/wortlaut/serving/schemas.py`
- `src/wortlaut/serving/app.py`
- `.importlinter`
- `tests/integration/test_attest.py`
- `tests/integration/test_verify_integration.py`
- `tests/integration/test_serving_api.py`

## 11. Umsetzungsdetails je Datei

### `src/wortlaut/store/attestations.py`

`SourceArchiveRow` und `get_attestations_for_source` nach dem Muster von
`store/timestamps.py::get_timestamps_for_source`; `order_by(SourceArchive.created_at,
SourceArchive.id)`. Nur Read.

### `src/wortlaut/pipeline/verify.py`

- Die fünf Felder **am Ende** von `VerifyReport` ergänzen (§3), mit Defaults.
- Neue private Funktion `_attestation_fields(source_id, session) -> tuple[...]` nach dem Muster
  `_timestamp_fields`: liest die erste Zeile; keine → `("missing", None, None, None, None)`.
- In `verify_source` die Attestierungsfelder in den Zweigen `worm_missing`, `hash_mismatch` und
  `ok` übergeben (AC3). **Per Keyword-Argument**, nicht positionell — die bestehenden
  Positionsargumente bleiben, wie sie sind.
- Modul-Docstring um einen Satz ergänzen: Attestierung additiv, aus der Datenbank, ohne Netz (#128).

### `src/wortlaut/serving/schemas.py`

`VerifyResult` und `SourceEvidence`: die fünf Felder **am Ende** (§3), mit einem Kommentar
„NEU (Spec 0128, additiv): Attestierung aus source_archive — ändert ok/status nicht".

### `src/wortlaut/serving/app.py`

- `/v1/spans/{span_id}/verify`: die fünf Felder aus dem `VerifyReport` übernehmen.
- `/v1/sources/{source_id}`: nach `get_source` die Attestierung über
  `get_attestations_for_source(session, source_id)` holen und an `_source_evidence` übergeben.
  `_source_evidence` bekommt dafür einen zweiten Parameter
  `attestation: SourceArchiveRow | None`; `None` → `attestation_status="missing"`, Felder `None`.

### `.importlinter`

Am Ende anfügen:

```
# Spec 0128 §0a: /verify und der Quellen-Beleg weisen die Attestierung aus der Datenbank aus —
# kein Abruf beim Internet Archive im Ausgabepfad.
[importlinter:contract:ausgabe-ohne-archivabruf]
name = Verify und Serving rufen keine Archivdienste ab
type = forbidden
source_modules =
    wortlaut.pipeline.verify
    wortlaut.serving
forbidden_modules =
    wortlaut.archive.wayback_lookup
    wortlaut.archive.archiver
    wortlaut.archive.spn2
ignore_imports =
    wortlaut.archive.throttle -> wortlaut.archive.archiver
```

### Tests

- Integration mit `fresh_pg_dsn`/`worm_store` und `seed_attestation` aus
  `tests/integration/conftest.py`.
- Für AC6 eine Attestierung mit einer **anderen** `snapshot_url` als `archive_wayback` anlegen:
  eigene `INSERT INTO source_archive (...) SELECT ... content_hash FROM source WHERE id = ...` mit
  frei gewählter URL — der Trigger aus #124 akzeptiert sie, weil der Hash aus `content_hash` kommt.
- **Bestehende Tests nicht ändern.** Neue Tests als neue Funktionen in den genannten Dateien.
- **Sonar-Muster:** ein Aufruf je `pytest.raises`-Block; keine zusammengesetzten Asserts
  (`assert a and b`); kein Lambda in `patch()`; Literale, die dreimal vorkommen, als Konstante;
  `async def` nur, wo awaited wird.

## 12. Do-NOT (hart)

- **Kein** Netzzugriff, kein Import aus `archive.wayback_lookup`, `archive.archiver`, `archive.spn2`
  in `pipeline/verify.py` oder `serving/`.
- **Keine** Änderung an `ok`/`status`-Logik, an bestehenden Feldern oder ihrer Reihenfolge.
- **Keine** Änderung an bestehenden Tests, Migrationen, `attest`, `ingest`, `reparse`.
- **Keine** neuen Abhängigkeiten.
- **Keine** Dateien außerhalb von §10.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate fährt der Reviewer selbst.
