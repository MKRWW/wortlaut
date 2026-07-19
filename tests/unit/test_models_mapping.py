"""Unit (#2): Die ORM-Modelle bilden die erwarteten Tabellen/Spalten ab.

Reiner Mapping-Check ohne DB-Roundtrip (kein Container) — die DB-Invarianten prüfen
die Integrationstests.
"""

from wortlaut.store.models import IngestAdapter, Source


def test_ingest_adapter_mapping() -> None:
    assert IngestAdapter.__tablename__ == "ingest_adapter"
    assert set(IngestAdapter.__table__.columns.keys()) == {
        "name",
        "version",
        "trust_level",
        "description",
        "created_at",
    }
    assert {c.name for c in IngestAdapter.__table__.primary_key.columns} == {"name", "version"}


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
