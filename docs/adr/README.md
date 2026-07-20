# Architecture Decision Records (ADR)

Jede tragende technische Entscheidung wird hier als **ADR** festgehalten — mit
Kontext, Entscheidung, Konsequenzen und verworfenen Alternativen. So ist „warum ist
das so?" nachvollziehbar und wird nicht zu implizitem Stammeswissen (die Lektion aus
Projekten, die ohne festgehaltene Entscheidungen entglitten sind).

**Format:** kurz. Status · Kontext · Entscheidung · Konsequenzen · Alternativen.
**Regel:** Eine ADR wird nicht editiert, wenn sich die Entscheidung ändert — sie wird
per neuer ADR **abgelöst** (Status: Superseded by ADR-XXXX). IDs werden nie wiederverwendet.

| ADR | Entscheidung | Status |
|-----|--------------|--------|
| [0001](0001-language-runtime.md) | Python 3.12 als Sprache/Runtime | Accepted |
| [0002](0002-build-tool-uv.md) | `uv` als Build-/Dependency-Tool | Accepted |
| [0003](0003-persistence.md) | PostgreSQL + pgvector, Zugriff via SQLAlchemy Core + Alembic | Accepted |
| [0004](0004-async-web.md) | Async-I/O (asyncpg/httpx) + FastAPI | Accepted |
| [0005](0005-object-storage-worm.md) | MinIO (S3 Object-Lock) als WORM-Speicher | Superseded by [0007](0007-worm-lock-mode.md) |
| [0006](0006-testing-tdd.md) | pytest + Testcontainers, TDD-Pflicht | Accepted |
| [0007](0007-worm-lock-mode.md) | WORM-Lock-Modus: Governance + unbegrenzter Legal-Hold | Accepted |
