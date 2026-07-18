# ADR-0003: PostgreSQL + pgvector, Zugriff via SQLAlchemy ORM + Alembic

- **Status:** Accepted (2026-07-18) · **Revised 2026-07-18** (ORM statt Core — Stakeholder-Entscheid)

## Kontext
Das Datenmodell (docs/datamodel.md) verlangt: relationales Schema **plus** Vektor-
Suche (`pgvector`), **append-only Immutability über DB-Trigger**, CHECK-Constraints,
Volltext (FTS). Das ist SQL-nah und trigger-lastig.

## Entscheidung
**PostgreSQL** als einziger Store (relational + `pgvector` + FTS). Zugriff über den
**SQLAlchemy ORM** (async, `DeclarativeBase` + `AsyncSession`) für Ergonomie und
Wartbarkeit. Migrationen über **Alembic**.

**Wichtige Abgrenzung (die Absicherung gegen ORM-„Magie"):**
- **Immutability wird über DB-Trigger erzwungen, NICHT über das ORM.** Trigger,
  `pgvector`-Extension und CHECK-Constraints sind **rohes SQL in Alembic-Migrationen**
  und damit unabhängig vom Zugriffslayer wirksam — auch ein versehentliches ORM-Write
  wird von der DB abgelehnt (R-DATA-01).
- **Keine versteckten Writes:** kein `autoflush`-Verlass für Beweis-Tabellen;
  Beweis-Inserts sind explizit. Invarianten werden gegen echte DB getestet (ADR-0006).

## Konsequenzen
- (+) Ein Store statt Vektor-DB-Zoo; Provenienz + Vektor in derselben Transaktion.
- (+) ORM: weniger Boilerplate, typisierte Modelle (`Mapped`/`mapped_column`), gute
  Entwickler-Ergonomie und Lesbarkeit.
- (+) Alembic → versionierte, reviewbare Schema-Änderungen inkl. Trigger.
- (−) **ORM-Risiko:** Abstraktion kann Writes/Loads verschleiern → wird durch die
  Trigger-Absicherung + Invarianten-Tests + Review-Disziplin (keine impliziten Writes)
  aufgefangen. Diese Absicherung ist **nicht optional**.
- pgvector-Typ über `pgvector.sqlalchemy.Vector` im ORM-Modell.

## Alternativen
- **SQLAlchemy Core (ursprünglich in v1 dieser ADR):** maximale Kontrolle, keine
  ORM-Abstraktion — verworfen zugunsten der ORM-Ergonomie; das append-only-Risiko wird
  stattdessen auf DB-Ebene (Trigger) statt durch Layer-Wahl adressiert.
- **asyncpg + rohes SQL + eigener Migration-Runner:** verworfen (Alembic ist reif).
