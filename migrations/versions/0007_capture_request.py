"""Capture-Anfragen: capture_request (append-only)

Spec 0130 §0b/§4.1: Gedächtnis gegen Doppel-Captures. Ein Capture ist erst
Stunden bis Tage später abrufbar (#124 §0d); ohne Gedächtnis würde jeder Lauf
erneut einen Capture auslösen. `source` ist append-only und kann den Versuch
nicht festhalten — deshalb diese eigene append-only Tabelle mit einer
Abkühlzeit je Quelle (lange nach `captured`, kürzer nach `failed`).

Das ist das Protokoll der Anfrage, **kein** Beweis (AC11): Eine `captured`-
Zeile besagt nur, dass ein Auftrag rausging; die Bezeugung ist Sache von
`attest` (`source_archive`). Ein `captured` ohne `snapshot_url` bzw. ein
`failed` ohne `reason` verletzt `chk_capture_outcome` — die Kombination ist
DB-Wahrheit. **Kein** UNIQUE: Eine Quelle kann über die Zeit mehrere
Anfragen haben; das Protokoll ist gerade die Historie.

Rohes SQL, weil die Immutabilitäts-Invariante DB-Wahrheit ist, nicht
ORM-Konvention (ADR-0003 rev.). forbid_mutation() existiert aus 0002 —
NICHT neu anlegen.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-02
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- capture_request, immutabel/append-only ---
    op.execute(
        """
        CREATE TABLE capture_request (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          source_id     uuid NOT NULL REFERENCES source(id),
          archiver      text NOT NULL,
          outcome       text NOT NULL CHECK (outcome IN ('captured', 'failed')),
          snapshot_url  text,
          reason        text,
          requested_at  timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT chk_capture_outcome CHECK (
            (outcome = 'captured' AND snapshot_url IS NOT NULL AND reason IS NULL) OR
            (outcome = 'failed'   AND snapshot_url IS NULL     AND reason IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_capture_request_source ON capture_request(source_id, requested_at)"
    )

    # --- Immutability-Trigger (R-DATA-01); forbid_mutation() existiert aus 0002 ---
    op.execute(
        "CREATE TRIGGER trg_capture_request_immutable BEFORE UPDATE OR DELETE "
        "ON capture_request FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
    )


def downgrade() -> None:
    # Umgekehrte Reihenfolge: Trigger, Index, Tabelle.
    # forbid_mutation() gehört zu 0002 und wird NICHT angefasst.
    op.execute("DROP TRIGGER IF EXISTS trg_capture_request_immutable ON capture_request")
    op.execute("DROP INDEX IF EXISTS ix_capture_request_source")
    op.execute("DROP TABLE IF EXISTS capture_request")
