"""Entfernen des chk_archive-Constraints auf source (Spec 0132 §4.3, ADR-0009 §1)

Seit #132 erfasst ``ingest`` ohne jeden Kontakt zum Internet Archive: Neue
Quellen tragen ``archive_wayback = NULL`` und ``archive_today = NULL``. Der
``chk_archive``-Constraint aus 0002 (``archive_wayback IS NOT NULL OR
archive_today IS NOT NULL``) würde jeden solchen Insert verhindern (Spec 0132
§0a). Die Garantie „nichts wird zitierbar ohne Fremdbezeugung“ besteht
gleichwertig weiter an der Zitierfähigkeits-Grenze: der Trigger
``trg_span_requires_attestation`` aus 0006 lässt keinen Span ohne
``source_archive``-Zeile zu, und der Read-Pfad filtert zusätzlich
(ADR-0009 §1). ``chk_archive`` war die schwächere, ältere Form derselben
Garantie — es genügte *irgendeine* gemeldete Archiv-URL, ohne Byte-Prüfung.

Bestandsdaten bleiben unverändert (append-only, R-DATA-01): Die 9
Bestandsquellen behalten ihre ``archive_wayback``-Werte, neue Quellen
tragen dort NULL.

``downgrade()`` legt den Constraint mit ``NOT VALID`` wieder an: Ein
Rückweg darf nicht an Quellen ohne Archiv-URL scheitern, die nach dem
Upgrade entstanden sind. ``NOT VALID`` validiert den Bestand nicht neu,
erzwingt die Bedingung aber für alle neuen Rows — genau das, was der
Rückweg braucht, um anschließend wieder upgraden zu können.

Rohes SQL, weil die Immutabilitäts-Invariante DB-Wahrheit ist, nicht
ORM-Konvention (ADR-0003 rev.).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-02
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE source DROP CONSTRAINT chk_archive")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE source ADD CONSTRAINT chk_archive "
        "CHECK (archive_wayback IS NOT NULL OR archive_today IS NOT NULL) NOT VALID"
    )
