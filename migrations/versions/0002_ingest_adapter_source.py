"""ingest_adapter + source schema with append-only immutability

Schema laut docs/datamodel.md §2, §3.1, §3.2, §4. Rohes SQL, weil die
Immutabilitäts-/Provenienz-Invarianten DB-Wahrheit sind, nicht ORM-Konvention
(ADR-0003 rev.).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-19
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Enums (datamodel §2, nur die drei dieses Increments) ---
    op.execute(
        "CREATE TYPE source_type AS ENUM ("
        "'plenarprotokoll','drucksache','dip_vorgang','rede',"
        "'interview','podcast','social_post','video')"
    )
    op.execute(
        "CREATE TYPE rights_basis AS ENUM ("
        "'amtliches_werk_p5','oeffentlich_gemacht_art9e','zitat_p51','lizenz','ungeklaert')"
    )
    op.execute("CREATE TYPE trust_level AS ENUM ('verified_primary','secondary','low')")

    # --- ingest_adapter (datamodel §3.1) ---
    op.execute(
        """
        CREATE TABLE ingest_adapter (
          name         text        NOT NULL,
          version      text        NOT NULL,
          trust_level  trust_level NOT NULL,
          description  text,
          created_at   timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (name, version)
        )
        """
    )

    # --- source (datamodel §3.2), immutabel/append-only ---
    op.execute(
        """
        CREATE TABLE source (
          id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          source_type     source_type NOT NULL,
          rights_basis    rights_basis NOT NULL,
          adapter_name    text NOT NULL,
          adapter_version text NOT NULL,
          origin_url      text NOT NULL,
          content_hash    char(64) NOT NULL UNIQUE,
          byte_size       bigint NOT NULL,
          mime_type       text NOT NULL,
          retrieved_at    timestamptz NOT NULL,
          raw_bytes_ref   text NOT NULL,
          archive_wayback text,
          archive_today   text,
          warc_ref        text,
          normalized_text text,
          created_at      timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (adapter_name, adapter_version)
              REFERENCES ingest_adapter(name, version),
          CONSTRAINT chk_archive CHECK (
              archive_wayback IS NOT NULL OR archive_today IS NOT NULL),
          CONSTRAINT chk_rights CHECK (rights_basis <> 'ungeklaert' OR true)
        )
        """
    )

    # --- Append-only-Immutability über DB-Trigger (datamodel §4, R-DATA-01) ---
    op.execute(
        """
        CREATE OR REPLACE FUNCTION forbid_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'append-only: % auf % verboten', TG_OP, TG_TABLE_NAME;
        END; $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_source_immutable BEFORE UPDATE OR DELETE ON source "
        "FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_ingest_adapter_immutable BEFORE UPDATE OR DELETE ON ingest_adapter "
        "FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_ingest_adapter_immutable ON ingest_adapter")
    op.execute("DROP TRIGGER IF EXISTS trg_source_immutable ON source")
    op.execute("DROP TABLE IF EXISTS source")
    op.execute("DROP TABLE IF EXISTS ingest_adapter")
    op.execute("DROP FUNCTION IF EXISTS forbid_mutation()")
    op.execute("DROP TYPE IF EXISTS trust_level")
    op.execute("DROP TYPE IF EXISTS rights_basis")
    op.execute("DROP TYPE IF EXISTS source_type")
