"""ORM-Modelle für ``ingest_adapter`` und ``source`` (Phase 0, #2).

Nur Zugriffs-Ergonomie. Constraints, CHECKs und der Append-only-Trigger leben in der
Alembic-Migration 0002 (rohes SQL, ADR-0003 rev.), **nicht** im ORM. Die Enum-Typen
werden von der Migration erzeugt → hier ``create_type=False`` (kein zweites CREATE TYPE).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Computed,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from wortlaut.store.db import Base

_SOURCE_TYPE = PgEnum(
    "plenarprotokoll",
    "drucksache",
    "dip_vorgang",
    "rede",
    "interview",
    "podcast",
    "social_post",
    "video",
    name="source_type",
    create_type=False,
)
_RIGHTS_BASIS = PgEnum(
    "amtliches_werk_p5",
    "oeffentlich_gemacht_art9e",
    "zitat_p51",
    "lizenz",
    "ungeklaert",
    name="rights_basis",
    create_type=False,
)
_TRUST_LEVEL = PgEnum(
    "verified_primary",
    "secondary",
    "low",
    name="trust_level",
    create_type=False,
)


class IngestAdapter(Base):
    """Erweiterbarkeits-Naht; immutabel je ``(name, version)`` (Trigger)."""

    __tablename__ = "ingest_adapter"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, primary_key=True)
    trust_level: Mapped[str] = mapped_column(_TRUST_LEVEL, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class Source(Base):
    """Archivierte Rohquelle; Inhalt immutabel/append-only (Trigger, §4)."""

    __tablename__ = "source"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    source_type: Mapped[str] = mapped_column(_SOURCE_TYPE, nullable=False)
    rights_basis: Mapped[str] = mapped_column(_RIGHTS_BASIS, nullable=False)
    adapter_name: Mapped[str] = mapped_column(Text, nullable=False)
    adapter_version: Mapped[str] = mapped_column(Text, nullable=False)
    origin_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    raw_bytes_ref: Mapped[str] = mapped_column(Text, nullable=False)
    archive_wayback: Mapped[str | None] = mapped_column(Text)
    archive_today: Mapped[str | None] = mapped_column(Text)
    warc_ref: Mapped[str | None] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["adapter_name", "adapter_version"],
            ["ingest_adapter.name", "ingest_adapter.version"],
        ),
    )


# -- Enums für Span-Schema (datamodel §2, create_type=False — Migration erzeugt) --

_VERIFICATION = PgEnum(
    "official",
    "machine",
    "human_verified",
    "disputed",
    "superseded",
    name="verification",
    create_type=False,
)
_VISIBILITY = PgEnum(
    "public",
    "restricted",
    "sensitive",
    name="visibility_class",
    create_type=False,
)


class Speaker(Base):
    """Mandatsträger; mutabel (§4)."""

    __tablename__ = "speaker"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    external_ids: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class Mandate(Base):
    """Zeitlich begrenztes Mandat; mutabel (§4)."""

    __tablename__ = "mandate"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    speaker_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("speaker.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    parliament: Mapped[str] = mapped_column(Text, nullable=False)
    party: Mapped[str | None] = mapped_column(Text)
    active_from: Mapped[date] = mapped_column(Date, nullable=False)
    active_to: Mapped[date | None] = mapped_column(Date)


class Span(Base):
    """Zitierfähige Einheit; Inhalt immutabel/append-only (Trigger, §4)."""

    __tablename__ = "span"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    source_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("source.id"), nullable=False
    )
    speaker_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("speaker.id"), nullable=False
    )
    mandate_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), ForeignKey("mandate.id"))
    verbatim_text: Mapped[str] = mapped_column(Text, nullable=False)
    text_start: Mapped[int] = mapped_column(Integer, nullable=False)
    text_end: Mapped[int] = mapped_column(Integer, nullable=False)
    spoken_at: Mapped[date] = mapped_column(Date, nullable=False)
    locator: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    permalink: Mapped[str] = mapped_column(Text, nullable=False)
    span_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    fts: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('german', verbatim_text)", persisted=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class SpanState(Base):
    """Mutabler Status (1:1 zu span); auditiert (§4)."""

    __tablename__ = "span_state"

    span_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("span.id"), primary_key=True
    )
    verification: Mapped[str] = mapped_column(_VERIFICATION, nullable=False)
    visibility: Mapped[str] = mapped_column(_VISIBILITY, nullable=False)
    redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    redaction_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
