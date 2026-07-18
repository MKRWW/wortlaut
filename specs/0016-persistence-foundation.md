# Increment-Spec: Persistenz-Fundament (#16)

- **Story/Issue:** #16 · **Status:** Reviewed · **Phase/Layer:** phase/0 · `store`
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)

## 1. Ziel
Ein reproduzierbares, async-fähiges Persistenz-Fundament, gegen das alle folgenden
Schema-/Beweis-Increments (#2 ff.) **spec-first + TDD gegen echte Postgres** gebaut
werden: `uv` als Build-Tool, eine async **SQLAlchemy-ORM**-Basis, Alembic-Migrationen
mit `pgvector`, und ein Testcontainers-Postgres-Harness.

## 2. Nicht-Ziele (Scope-Grenze)
- **Keine** Fachtabellen (`source`/`span`/…) — das ist #2.
- **Kein** MinIO/WORM — eigenes Fundament (#5).
- **Kein** FastAPI/API-Wiring, keine Adapter, keine echten Daten.

## 3. Betroffene Interfaces / Öffentliche Signaturen (SQLAlchemy ORM, async)
```python
# src/wortlaut/store/settings.py  (pydantic-settings; DSN aus ENV, nie im Code, R-SEC-01)
class DbSettings(BaseSettings):
    dsn: str  # postgresql+asyncpg://user:pw@host:5432/wortlaut

# src/wortlaut/store/db.py
class Base(DeclarativeBase): ...                      # ORM-Basis für alle Modelle
def create_async_engine_from(settings: DbSettings) -> AsyncEngine: ...
def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]: ...
@asynccontextmanager
async def session_scope(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]: ...
```
- **Alembic:** `migrations/` mit **async** `env.py`; Revision 0001 = `CREATE EXTENSION IF NOT EXISTS vector` (rohes SQL).
- **Test-Harness:** Fixtures `pg_container` (session-scoped), `db_engine`, `db_session`; Marker `integration`.
- **Layering (R-ARCH-02):** `store` importiert keinen anderen wortlaut-Layer.

## 4. Design (kurz, verweist auf ADRs)
- **uv** (ADR-0002): `uv.lock` eingecheckt; CI nutzt `uv sync --frozen` + `uv run`.
- **ORM async** (ADR-0003 rev.): SQLAlchemy 2.0 `DeclarativeBase` + `AsyncSession` über
  **asyncpg**. **Immutability wird NICHT übers ORM, sondern über DB-Trigger erzwungen**
  (rohes SQL in Alembic, ab #2) — das ORM ist nur Zugriffs-Ergonomie.
- **pgvector**: Typ via `pgvector.sqlalchemy.Vector` im ORM-Modell.
- **Testcontainers** (ADR-0006): Image **digest-gepinnt** (Supply-Chain, R-SEC):
  `pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb`.
  Fixture fährt `alembic upgrade head` im Setup. Unit-Tests starten **keinen** Container.

## 5. Testbare Akzeptanzkriterien
- [ ] **AC1** *Given* frischer Clone, *When* `uv sync --frozen`, *Then* Exit 0 und `uv.lock` konsistent (CI-Schritt).
- [ ] **AC2** *Given* die CI, *When* Gates laufen, *Then* über `uv run …` (kein `pip install` mehr).
- [ ] **AC3** *Given* ein Testcontainers-Postgres, *When* eine `AsyncSession` `select(text("1"))` ausführt, *Then* Scalar == `1`. `[integration]`
- [ ] **AC4** *Given* `alembic upgrade head`, *When* ein ORM-Modell mit `Vector(3)` `[1,2,3]` per Session schreibt und lädt, *Then* geladener Vektor == `[1,2,3]`. `[integration]`
- [ ] **AC5** *Given* `pytest -m "not integration"`, *When* es läuft, *Then* startet **kein** Container; `pytest -m integration` startet ihn.

## 6. Testplan (Test-zu-AC-Mapping)
- **Unit (schnell, rein):**
  - `test_dbsettings_parses_dsn_from_env` — `DbSettings` liest DSN aus ENV.
  - `test_engine_factory_builds_expected_url` — `create_async_engine_from` ohne Verbindung.
- **Integration (Testcontainers Postgres):**
  - `test_session_select_one` → **AC3**
  - `test_orm_vector_roundtrip` → **AC4** (Modell mit `Vector(3)`, write→read)
  - `test_alembic_upgrade_head_clean` — Migration fehlerfrei/idempotent.
- **AC1/AC2** → CI (zwei Jobs: `unit` ohne Docker, `integration` mit Testcontainers; beide via `uv run`).

## 7. Recht / Security
- Kein Datenpfad; legt Basis für Immutability-Trigger (#2, R-DATA-01).
- DSN/Credentials nur aus ENV/Secret (R-SEC-01); Container-DB ephemer.
- Reproduzierbarkeit via `uv.lock`, Image via Digest gepinnt (R-SEC / ADR-0002/0006).

## 8. Risiken & offene Fragen
- **CI-Umbau** (pip→uv, neuer `integration`-Job mit Docker) passiert **in** diesem Increment.
- ORM-Risiko (versteckte Writes) → adressiert durch Trigger-Absicherung (ab #2) +
  Review-Disziplin (R-DATA, ADR-0003 rev.).
- Alembic-async-`env.py`: Standardmuster.

## 9. Definition of Done (Verweis)
[../docs/rules.md](../docs/rules.md) DoD: AC grün, **alle Gates grün inkl. Integration-Job**,
Review, keine Gott-Klassen, kein Secret/Pickle/LLM-Freitext, Coverage ≥ 80 %.
