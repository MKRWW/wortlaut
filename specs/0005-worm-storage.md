# Increment-Spec: WORM-Storage-Adapter (MinIO Object-Lock) (#5)

- **Story/Issue:** #5 · **Status:** Reviewed · **Phase/Layer:** phase/0 · `store`
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#16** (Testcontainers-Harness) · ADR: **[0007](../docs/adr/0007-worm-lock-mode.md)** (löst 0005-Modus ab).

## 1. Ziel
Ein schmaler, unveränderlicher Beweis-Speicher für Rohbytes: ein `store`-Adapter mit
**nur `put`/`get`**, der jede abgelegte Quelle über **MinIO S3 Object-Lock
(Governance-Mode + unbegrenzter Legal-Hold)** append-only sichert — damit die
Rohbytes selbst bei kompromittiertem App-Layer nicht still überschrieben/gelöscht
werden können (R-DATA-01, Security §3.6).

## 2. Nicht-Ziele (Scope-Grenze)
- **Kein** Delete-/Overwrite-/Legal-Hold-Release-Pfad im Adapter (auch nicht optional).
  Der DSGVO-Härtefall-Löschpfad ist **privilegiert & out-of-band** (ADR-0007), nicht hier.
- **Kein** Hashing, keine Dedup-Logik (das ist #3), **keine** Pipeline-Orchestrierung (#7).
- **Kein** `source`-Insert / keine DB-Kopplung — der Adapter kennt nur Objekt-Storage.
- **Kein** WARC-Handling, kein Fremdarchiv (#4), kein Verify (#8).
- Keine Content-Adressierung *im Adapter*: der Key ist ein Parameter; dass #7 den
  `content_hash` als Key nutzt, ist Konvention des Aufrufers, nicht des Adapters (SRP).

## 3. Betroffene Interfaces / Öffentliche Signaturen
```python
# src/wortlaut/store/worm.py   (R-ARCH-01: Protocol-Naht; R-ARCH-02: store importiert keinen Layer)
from typing import Protocol

class WormStore(Protocol):
    async def ensure_bucket(self) -> None: ...
    async def put(self, key: str, data: bytes, *, content_type: str) -> str: ...  # → raw_bytes_ref
    async def get(self, key: str) -> bytes: ...

class MinioWormStore:                      # implementiert WormStore
    def __init__(self, settings: "WormSettings") -> None: ...
    async def ensure_bucket(self) -> None:
        """Erstellt den Bucket mit aktiviertem Object-Lock (idempotent); Object-Lock ist
        nur bei Bucket-Creation setzbar."""
    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        """Legt das Objekt ab, setzt Legal-Hold=ON. Gibt stabile Ref zurück:
        f's3://{bucket}/{key}'."""
    async def get(self, key: str) -> bytes: ...

# src/wortlaut/store/settings.py  (erweitert #16 DbSettings-Muster; pydantic-settings, ENV)
class WormSettings(BaseSettings):
    endpoint: str; access_key: str; secret_key: str
    bucket: str = "wortlaut-worm"; secure: bool = True
```
- **Layering (R-ARCH-02):** `store.worm` ist reine Infrastruktur, importiert keinen anderen
  wortlaut-Layer. Der Sync-`minio`-SDK wird in `asyncio.to_thread` gekapselt (async Surface,
  passt zum async `store` aus #16).
- **Öffentliche API bewusst minimal:** genau `ensure_bucket`/`put`/`get`. **Kein** `delete`,
  `remove`, `release_hold` — die Abwesenheit ist testbare Invariante (AC6).

## 4. Design (kurz, verweist auf ADR-0007)
- **Governance + Legal-Hold statt Compliance (ADR-0007):** jedes Objekt wird mit
  `Legal-Hold=ON` (ohne Ablaufdatum) geschrieben → im Normalbetrieb effektiv unlöschbar,
  ohne die „Retention läuft aus"-Lücke. Löschung im DSGVO-Härtefall bleibt als privilegierte
  Out-of-band-Operation möglich (`s3:BypassGovernanceRetention` + `PutObjectLegalHold`) —
  **nicht** im Adapter.
- **Bucket mit Object-Lock:** `ensure_bucket` erstellt den Bucket mit `object_lock=True`
  (nur bei Creation setzbar) und ist idempotent (existiert → no-op).
- **Stabile Ref:** `put` gibt `s3://{bucket}/{key}` zurück → landet später in
  `source.raw_bytes_ref` (#2). Der Adapter parst/erzeugt keine Hashes.
- **Kein Delete/Overwrite by construction:** put auf existierenden, gehaltenen Key scheitert
  an Object-Lock/Legal-Hold; das ist die WORM-Garantie, nicht eine Code-Konvention.
- **Secrets nur aus ENV** (R-SEC-01): Endpoint/Keys via `WormSettings`, nie im Code.

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)
- [ ] **AC1** *Given* frischer WORM-Bucket, *When* `put("k1", b"bytes", content_type="application/octet-stream")`
      dann `get("k1")`, *Then* zurückgegebene Bytes == `b"bytes"` **und** die Ref == `s3://<bucket>/k1`. `[integration]`
- [ ] **AC2** *Given* abgelegtes Objekt `k1`, *When* `put("k1", b"other", …)` (Overwrite), *Then* Fehler
      (Object-Lock/Legal-Hold) **und** `get("k1")` liefert weiterhin die **ursprünglichen** Bytes. `[integration]`
- [ ] **AC3** *Given* abgelegtes Objekt `k1`, *When* Delete-Versuch über die reguläre S3-Rolle (ohne
      Bypass), *Then* Fehler **und** Objekt existiert weiter. `[integration]`
- [ ] **AC4** *Given* frisch abgelegtes `k1`, *When* Object-Legal-Hold-Status abgefragt, *Then* == `ON`. `[integration]`
- [ ] **AC5** *Given* nicht existierender Bucket, *When* `ensure_bucket`, *Then* Bucket existiert **mit
      aktiviertem Object-Lock**; erneuter `ensure_bucket`-Aufruf ist fehlerfrei (idempotent). `[integration]`
- [ ] **AC6** *Given* die öffentliche Adapter-API, *When* via `inspect`/`dir` geprüft, *Then* existiert
      **kein** öffentliches `delete`/`remove`/`release_hold`/`overwrite` (nur `ensure_bucket`/`put`/`get`). `[unit]`
- [ ] **AC7** *Given* frischer Bucket, *When* `get("unbekannt")`, *Then* definierter Fehler (NotFound),
      **kein** leerer/Null-Erfolg. `[integration]`
> Jedes AC ist von einem automatisierten Test mit Ja/Nein beantwortbar.

## 6. Testplan (Test-zu-AC-Mapping)
- **Integration (Testcontainers MinIO, neue Fixture `worm_store`; Bucket mit Object-Lock):**
  `tests/integration/test_worm_storage.py`
  - `test_put_get_roundtrip` → **AC1**
  - `test_overwrite_denied` → **AC2** · `test_delete_denied` → **AC3**
  - `test_legal_hold_active` → **AC4**
  - `test_ensure_bucket_object_lock_and_idempotent` → **AC5**
  - `test_get_unknown_key_raises` → **AC7**
  - Fixture: `MinioContainer` (digest-gepinnt, Supply-Chain R-SEC; mit `MINIO_ROOT_*`), Bucket
    per `ensure_bucket`. Analog zur `pg_dsn`-Fixture aus #16 (conftest).
- **Unit (rein, kein Container):** `tests/unit/test_worm_api_surface.py`
  - `test_no_delete_or_release_in_public_api` → **AC6**
  - `test_worm_settings_from_env` (Settings-Parsing) — Sanity.
- **Invariante (Pflicht, R-DATA-01):** AC2/AC3/AC4 sind die WORM-Invarianten und **immer**
  Integrationstests gegen echtes MinIO (engineering §4.5, keine Mock-Invarianten).

## 7. Recht / Security
- **WORM-Invariante (R-DATA-01, Security §3.6):** append-only Beweisspeicher, kein
  Update/Delete-Pfad im Code — Kern der Beweis-Integrität (Assets §2 „Beweis-Rohdaten").
- **Governance+Legal-Hold (ADR-0007):** DSGVO-Härtefall-Löschung bleibt privilegiert/out-of-band
  möglich (Legal §4.3), Regel-Löschbegehren laufen über Redaction (R-DATA-04), nicht über WORM.
- **Least Privilege:** die Ingest-/App-Rolle hat **kein** Bypass-/Delete-Recht (nur der
  eng bewachte Härtefall-Pfad). **Keine Secrets im Repo** (R-SEC-01) — ENV.

## 8. Risiken & offene Fragen / Entscheidungen
- **Entscheidung (async-Wrap):** Sync-`minio`-SDK in `asyncio.to_thread` gekapselt (statt
  `aioboto3`) — offizieller SDK mit klarer Object-Lock/Legal-Hold-API, minimale neue Deps.
- **Governance schwächer gegen Privileg-Kompromittierung** als Compliance — bewusst (ADR-0007),
  mitigiert durch getrennte Bypass-Credentials + Audit. Nicht Teil von #5-Code.
- **Bucket-Object-Lock nur bei Creation setzbar:** `ensure_bucket` muss den Bucket selbst
  anlegen; ein vorab ohne Object-Lock angelegter Bucket wäre ein Betriebsfehler (dokumentieren).
- **MinIO-Testcontainer:** Image digest-pinnen (ADR-0006-Muster); Object-Lock im Container
  unterstützt. `testcontainers[minio]` als Dev-Dep ergänzen, `minio>=7` als Runtime-Dep.
- **Sandbox-Container-Start** kann lokal von der Sandbox abgeschossen werden (bekannter
  Fallstrick) → **CI ist die maßgebliche Integration-Instanz**.

## 9. Definition of Done (Verweis)
[../docs/rules.md](../docs/rules.md) DoD: alle AC grün (inkl. Integration-Job gegen echtes MinIO),
alle Gates grün (Lint·Type·Test·Coverage ≥80, Security, Architektur, SonarCloud-Quality-Gate),
Review (Architekt + **Security**, da R-DATA/R-SEC berührt), WORM-Invariante gewahrt,
kein Delete/Release im Serving/Adapter, kein Secret. ADR-0007 committet. PR referenziert **#5**.
