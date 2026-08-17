"""RFC-3161-Zeitstempel: source_timestamp (append-only) + Trigger

Spec 0076 §3.3/§7: Das RFC-3161-Token einer unabhängigen TSA über den
content_hash — anbieterunabhängiger Nachweis, dass die Bytes zu created_at
existierten (Eigenschaft B). Append-only je (source_id, tsa_name) über den
eigenen DB-Trigger (R-DATA-01); kein timestamp_pending-Flag (abgeleitet: keine
Zeile = pending).

Rohes SQL, weil die Immutabilitäts-Invariante DB-Wahrheit ist, nicht
ORM-Konvention (ADR-0003 rev.). forbid_mutation() existiert aus 0002 —
NICHT neu anlegen.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-17
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- source_timestamp, immutabel/append-only ---
    op.execute(
        """
        CREATE TABLE source_timestamp (
          id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          source_id  uuid NOT NULL REFERENCES source(id),
          tsa_name   text NOT NULL,
          token_ref  text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_source_timestamp_tsa UNIQUE (source_id, tsa_name)
        )
        """
    )
    op.execute("CREATE INDEX ix_source_timestamp_source ON source_timestamp(source_id)")

    # --- Immutability-Trigger (R-DATA-01); forbid_mutation() existiert aus 0002 ---
    op.execute(
        "CREATE TRIGGER trg_source_timestamp_immutable BEFORE UPDATE OR DELETE "
        "ON source_timestamp FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
    )


def downgrade() -> None:
    # Trigger und Index droppt die Funktion (forbid_mutation) NICHT — die gehört
    # zu 0002 und wird von source/span weiterhin gebraucht.
    op.execute("DROP TRIGGER IF EXISTS trg_source_timestamp_immutable ON source_timestamp")
    op.execute("DROP INDEX IF EXISTS ix_source_timestamp_source")
    op.execute("DROP TABLE IF EXISTS source_timestamp")
