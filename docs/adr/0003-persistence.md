# ADR-0003: PostgreSQL + pgvector, Zugriff via SQLAlchemy Core + Alembic

- **Status:** Accepted (2026-07-18)

## Kontext
Das Datenmodell (docs/datamodel.md) verlangt: relationales Schema **plus** Vektor-
Suche, **append-only Immutability über DB-Trigger**, CHECK-Constraints,
Volltext (FTS). Das ist SQL-nah und trigger-lastig — ORM-„Magie" ist hier ein Risiko.

## Entscheidung
**PostgreSQL** als einziger Store (relational + `pgvector` + FTS). Zugriff über
**SQLAlchemy Core** (nicht den ORM) — explizite Statements, volle Kontrolle über rohes
SQL/Trigger/pgvector. Migrationen über **Alembic**, inklusive der Immutability-Trigger
und `pgvector`-Extension als versionierte Migrations-Schritte.

## Konsequenzen
- (+) Ein Store statt Vektor-DB-Zoo; Provenienz + Vektor in derselben Transaktion.
- (+) Core (kein ORM) → keine versteckten Writes; append-only bleibt kontrollierbar.
- (+) Alembic → versionierte, reviewbare Schema-Änderungen inkl. Trigger.
- (−) Mehr Handarbeit als mit ORM-Komfort; Mapping explizit.
- Trigger und Constraints sind **Teil der Migration** und werden per Invarianten-Test
  (R-DATA-01) gegen eine echte DB geprüft (siehe ADR-0006).

## Alternativen
- **SQLModel/ORM:** bequem, aber Abstraktion über append-only/Trigger/pgvector →
  „Magie"-Risiko → verworfen.
- **asyncpg + rohes SQL + eigener Migration-Runner:** maximale Kontrolle, aber
  Migrations-Tooling müssten wir selbst bauen → verworfen (Alembic ist reif).
