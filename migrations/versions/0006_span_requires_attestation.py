"""Span nur für attestierte Quellen (Spec 0126, ADR-0009 §1)

ADR-0009 erlaubt Spans und jede Ausgabe nur für Quellen mit Eigenschaft A
(nachgewiesene Byte-Gleichheit in ``source_archive``, #124). Diese Migration
macht die Grenze DB-Wahrheit (ADR-0003: Invarianten in der DB, nicht im Code):

- Bestand prüfen (Spec 0126 §4.2): ein ``DO``-Block zählt Spans, deren Quelle
  keine ``source_archive``-Zeile hat. Ist die Zahl > 0, bricht das Upgrade ab
  und nennt die Zahl sowie den nötigen Schritt (``attest`` fahren). Damit kann
  Increment 2 nicht ausgerollt werden, bevor der Bestand bezeugt ist.
- Attestierungs-Guard (Spec 0126 §4.1): ``BEFORE INSERT ON span`` verweigert
  jeden Insert für eine Quelle ohne ``source_archive``-Zeile. Ein
  Check-Constraint kann keine andere Tabelle lesen; deshalb Trigger-Funktion.
  ``source_archive`` ist append-only (R-DATA-01) — eine einmal bestehende
  Attestierung kann nicht nachträglich verschwinden, die Prüfung beim Insert
  genügt.

Rohes SQL, weil die Immutabilitäts-Invariante DB-Wahrheit ist, nicht
ORM-Konvention (ADR-0003 rev.). ``forbid_mutation()`` existiert aus 0002 —
NICHT neu anlegen.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-01
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Bestand prüfen VOR dem Umschalten (Spec 0126 §4.2): unattestierter
    # Altbestand blockiert das Upgrade, bis `attest` gefahren wurde. ---
    op.execute(
        """
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
        """
    )

    # --- Attestierungs-Guard: kein Span für eine Quelle ohne Attestierung ---
    op.execute(
        """
        CREATE FUNCTION require_source_attestation() RETURNS trigger AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM source_archive WHERE source_id = NEW.source_id) THEN
            RAISE EXCEPTION 'span: Quelle % ist nicht attestiert (ADR-0009)', NEW.source_id;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_span_requires_attestation BEFORE INSERT ON span "
        "FOR EACH ROW EXECUTE FUNCTION require_source_attestation()"
    )


def downgrade() -> None:
    # Umgekehrte Reihenfolge (AC3): erst Trigger, dann Funktion.
    op.execute("DROP TRIGGER IF EXISTS trg_span_requires_attestation ON span")
    op.execute("DROP FUNCTION IF EXISTS require_source_attestation()")
