"""Unit (#2): Die ORM-Modelle bilden die erwarteten Tabellen/Spalten ab.

Reiner Mapping-Check ohne DB-Roundtrip (kein Container) — die DB-Invarianten prüfen
die Integrationstests.
"""

from wortlaut.store.models import (
    IngestAdapter,
    Mandate,
    Source,
    Span,
    SpanState,
    Speaker,
)


def test_ingest_adapter_mapping() -> None:
    assert IngestAdapter.__tablename__ == "ingest_adapter"
    assert set(IngestAdapter.__table__.columns.keys()) == {
        "name",
        "version",
        "trust_level",
        "description",
        "created_at",
    }
    assert {c.name for c in IngestAdapter.__table__.primary_key} == {"name", "version"}


def test_source_mapping() -> None:
    assert Source.__tablename__ == "source"
    assert set(Source.__table__.columns.keys()) == {
        "id",
        "source_type",
        "rights_basis",
        "adapter_name",
        "adapter_version",
        "origin_url",
        "content_hash",
        "byte_size",
        "mime_type",
        "retrieved_at",
        "raw_bytes_ref",
        "archive_wayback",
        "archive_today",
        "warc_ref",
        "normalized_text",
        "created_at",
    }


def test_source_content_hash_is_unique() -> None:
    assert Source.__table__.c.content_hash.unique is True


def test_span_models_map_expected_tables_and_columns() -> None:
    # AC9: ORM-Modelle für Speaker/Mandate/Span/SpanState bilden die erwarteten
    # Tabellen/Spalten exakt ab (Migration 0003).
    assert Speaker.__tablename__ == "speaker"
    assert set(Speaker.__table__.columns.keys()) == {
        "id",
        "full_name",
        "external_ids",
        "created_at",
        "updated_at",
    }

    assert Mandate.__tablename__ == "mandate"
    assert set(Mandate.__table__.columns.keys()) == {
        "id",
        "speaker_id",
        "role",
        "parliament",
        "party",
        "active_from",
        "active_to",
    }

    assert Span.__tablename__ == "span"
    assert set(Span.__table__.columns.keys()) == {
        "id",
        "source_id",
        "speaker_id",
        "mandate_id",
        "verbatim_text",
        "text_start",
        "text_end",
        "spoken_at",
        "locator",
        "permalink",
        "span_hash",
        "fts",
        "created_at",
    }

    assert SpanState.__tablename__ == "span_state"
    assert set(SpanState.__table__.columns.keys()) == {
        "span_id",
        "verification",
        "visibility",
        "redacted",
        "redaction_reason",
        "updated_at",
    }
