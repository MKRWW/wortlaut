"""Span-Schema: speaker, mandate, span, span_state + Enums

Schema laut docs/datamodel.md §2 (Enums), §3.3–3.6, mit Immutability-Trigger
auf span (datamodel §4). Rohes SQL, weil die Beweis-Invarianten DB-Wahrheit
sind, nicht ORM-Konvention (ADR-0003 rev.).

Abweichung zur Spec: `span.fts` ist eine generierte Spalte
(to_tsvector('german', verbatim_text)) statt handgepflegt — eliminiert
Drift zwischen Text und FTS-Index.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-20
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Enums (datamodel §2) ---
    op.execute(
        "CREATE TYPE verification AS ENUM ("
        "'official','machine','human_verified','disputed','superseded')"
    )
    op.execute(
        "CREATE TYPE visibility_class AS ENUM ('public','restricted','sensitive')"
    )

    # --- speaker (datamodel §3.3) ---
    op.execute(
        """
        CREATE TABLE speaker (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          full_name    text NOT NULL,
          external_ids jsonb NOT NULL DEFAULT '{}',
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # --- mandate (datamodel §3.4) ---
    op.execute(
        """
        CREATE TABLE mandate (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          speaker_id  uuid NOT NULL REFERENCES speaker(id),
          role        text NOT NULL,
          parliament  text NOT NULL,
          party       text,
          active_from date NOT NULL,
          active_to   date
        )
        """
    )
    op.execute("CREATE INDEX idx_mandate_speaker ON mandate(speaker_id)")

    # --- span (datamodel §3.5, fts als generierte Spalte) ---
    op.execute(
        """
        CREATE TABLE span (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          source_id    uuid NOT NULL REFERENCES source(id),
          speaker_id   uuid NOT NULL REFERENCES speaker(id),
          mandate_id   uuid REFERENCES mandate(id),
          verbatim_text text NOT NULL,
          text_start   int NOT NULL,
          text_end     int NOT NULL,
          spoken_at    date NOT NULL,
          locator      jsonb NOT NULL DEFAULT '{}',
          permalink    text NOT NULL,
          span_hash    char(64) NOT NULL,
          fts          tsvector GENERATED ALWAYS AS
                       (to_tsvector('german', verbatim_text)) STORED,
          created_at   timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT chk_offsets CHECK (text_end > text_start)
        )
        """
    )
    op.execute("CREATE INDEX idx_span_source    ON span(source_id)")
    op.execute("CREATE INDEX idx_span_speaker   ON span(speaker_id)")
    op.execute("CREATE INDEX idx_span_spokenat  ON span(spoken_at)")
    op.execute("CREATE INDEX idx_span_fts       ON span USING gin (fts)")

    # --- span_state (datamodel §3.6) ---
    op.execute(
        """
        CREATE TABLE span_state (
          span_id          uuid PRIMARY KEY REFERENCES span(id),
          verification     verification     NOT NULL,
          visibility       visibility_class NOT NULL,
          redacted         boolean NOT NULL DEFAULT false,
          redaction_reason text,
          updated_at       timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # --- Immutability-Trigger auf span (datamodel §4, R-DATA-01) ---
    # forbid_mutation() existiert aus 0002 — NICHT neu anlegen.
    op.execute(
        "CREATE TRIGGER trg_span_immutable BEFORE UPDATE OR DELETE ON span "
        "FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
    )


def downgrade() -> None:
    # Trigger zuerst (sonst hängt alles am Trigger)
    op.execute("DROP TRIGGER IF EXISTS trg_span_immutable ON span")

    # Tabellen in umgekehrter Abhängigkeitsreihenfolge
    op.execute("DROP TABLE IF EXISTS span_state")
    op.execute("DROP TABLE IF EXISTS span")
    op.execute("DROP TABLE IF EXISTS mandate")
    op.execute("DROP TABLE IF EXISTS speaker")

    # Enums zuletzt
    op.execute("DROP TYPE IF EXISTS visibility_class")
    op.execute("DROP TYPE IF EXISTS verification")
