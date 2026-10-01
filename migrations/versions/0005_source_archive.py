"""Attestierung: source_archive (append-only) + Hash-Gleichheit-Trigger

Spec 0124 §4.1, ADR-0009 §2: Eine Zeile entsteht nur, wenn die Bytes eines
Fremdsnapshots nachweislich `source.content_hash` entsprechen (SHA-256). Der
BEFORE-INSERT-Trigger vergleicht `verified_sha256` mit dem `content_hash` der
referenzierten Quelle und verweigert den Insert bei Ungleichheit — die
Gleichheit gilt damit in der Datenbank, selbst wenn Anwendungscode irrt.
SHA-1 dient nur als Vorfilter für die Kandidatensuche, nie als Beweis.
Append-only je `(source_id, archiver)` über die bekannte DB-Trigger-Logik
(R-DATA-01); „unattestiert“ ist abgeleitet (keine Zeile), kein Flag.

Die Tabelle referenziert Quelle und Snapshot, **keine** S3-Version im eigenen
Speicher (ADR-0009 §2, #122).

Rohes SQL, weil die Immutabilitäts-Invariante DB-Wahrheit ist, nicht
ORM-Konvention (ADR-0003 rev.). forbid_mutation() existiert aus 0002 —
NICHT neu anlegen.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- source_archive, immutabel/append-only ---
    op.execute(
        """
        CREATE TABLE source_archive (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          source_id       uuid NOT NULL REFERENCES source(id),
          archiver        text NOT NULL,
          snapshot_url    text NOT NULL,
          snapshot_at     timestamptz NOT NULL,
          verified_sha256 char(64) NOT NULL,
          created_at      timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_source_archive_archiver UNIQUE (source_id, archiver)
        )
        """
    )
    op.execute("CREATE INDEX ix_source_archive_source ON source_archive(source_id)")

    # --- Hash-Gleichheit: verweigert den Insert bei Abweichung (ADR-0009 §2) ---
    op.execute(
        """
        CREATE FUNCTION check_source_archive_hash() RETURNS trigger AS $$
        BEGIN
          IF NEW.verified_sha256 IS DISTINCT FROM
             (SELECT content_hash FROM source WHERE id = NEW.source_id) THEN
            RAISE EXCEPTION 'source_archive: verified_sha256 passt nicht zu source.content_hash';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_source_archive_hash BEFORE INSERT ON source_archive "
        "FOR EACH ROW EXECUTE FUNCTION check_source_archive_hash()"
    )

    # --- Immutability-Trigger (R-DATA-01); forbid_mutation() existiert aus 0002 ---
    op.execute(
        "CREATE TRIGGER trg_source_archive_immutable BEFORE UPDATE OR DELETE "
        "ON source_archive FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
    )


def downgrade() -> None:
    # Umgekehrte Reihenfolge: Trigger, Funktion, Index, Tabelle.
    # forbid_mutation() gehört zu 0002 und wird NICHT angefasst.
    op.execute("DROP TRIGGER IF EXISTS trg_source_archive_immutable ON source_archive")
    op.execute("DROP TRIGGER IF EXISTS trg_source_archive_hash ON source_archive")
    op.execute("DROP FUNCTION IF EXISTS check_source_archive_hash()")
    op.execute("DROP INDEX IF EXISTS ix_source_archive_source")
    op.execute("DROP TABLE IF EXISTS source_archive")
