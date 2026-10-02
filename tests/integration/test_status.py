"""Integration (Spec 0132 §4.4, AC5): ``status`` — Rückstand je Pipeline-Stufe.

*Given* eine Quelle je Zustand: ungestempelt; unattestiert ohne Anfrage;
unattestiert mit letzter Anfrage ``failed``; attestiert ohne Span; attestiert
mit Span. *Then* die fünf Zählungen nennen genau die erwarteten Zahlen.
Isolation: frische DB je Test (``fresh_pg_dsn``).
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from wortlaut.store.db import create_async_engine_from, make_sessionmaker
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.settings import DbSettings
from wortlaut.store.status import BacklogCounts, backlog_counts

pytestmark = pytest.mark.integration

_ARCHIVER = "wayback"
_TSA = "freetsa"
_TOKEN_REF = "s3://tsa/token"
_VERBATIM = "Ein wörtlicher Satz."
_SPAN_HASH = hashlib.sha256(_VERBATIM.encode("utf-8")).hexdigest()

_ADAPTER_INSERT = text(
    "INSERT INTO ingest_adapter (name, version, trust_level) "
    "VALUES (:n, :v, CAST(:t AS trust_level)) ON CONFLICT (name, version) DO NOTHING"
)

_SOURCE_INSERT = text(
    "INSERT INTO source (source_type, rights_basis, adapter_name, adapter_version, "
    "origin_url, content_hash, byte_size, mime_type, retrieved_at, raw_bytes_ref) "
    "VALUES (CAST(:st AS source_type), CAST(:rb AS rights_basis), :an, :av, "
    ":ou, :ch, :bs, :mt, CAST(:ra AS timestamptz), :rbref) RETURNING id"
)

_TIMESTAMP_INSERT = text(
    "INSERT INTO source_timestamp (source_id, tsa_name, token_ref) "
    "VALUES (CAST(:sid AS uuid), :tsa, :ref)"
)

_ARCHIVE_INSERT = text(
    "INSERT INTO source_archive (source_id, archiver, snapshot_url, snapshot_at, "
    "verified_sha256) VALUES (CAST(:sid AS uuid), :arch, "
    "'https://web.archive.org/web/20260101000000/' || (SELECT origin_url FROM source "
    "WHERE id = CAST(:sid AS uuid)), now(), (SELECT content_hash FROM source "
    "WHERE id = CAST(:sid AS uuid)))"
)

_SPEAKER_INSERT = text("INSERT INTO speaker (full_name) VALUES (:name) RETURNING id")

_SPAN_INSERT = text(
    "INSERT INTO span (source_id, speaker_id, verbatim_text, text_start, text_end, "
    "spoken_at, permalink, span_hash) "
    "VALUES (CAST(:sid AS uuid), CAST(:spid AS uuid), :vt, 0, "
    "length(:vt), DATE '2026-10-02', 'https://example.test/protokoll', :sh)"
)

_CAPTURE_INSERT = text(
    "INSERT INTO capture_request (source_id, archiver, outcome, snapshot_url, reason, "
    "requested_at) VALUES (CAST(:sid AS uuid), :arch, :oc, :url, :reason, :at)"
)


def _source_params(i: int) -> dict[str, object]:
    return {
        "st": "plenarprotokoll",
        "rb": "amtliches_werk_p5",
        "an": "dip-api",
        "av": "1.0.0",
        "ou": f"https://example.test/0132-status-{i}",
        "ch": f"{i:02x}" * 32,
        "bs": 1,
        "mt": "application/pdf",
        "ra": datetime(2026, 10, 2, tzinfo=UTC),
        "rbref": "worm://test",
    }


@pytest.fixture
async def fresh_sessions(fresh_pg_dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Migrierte, frische DB je Test."""
    await upgrade_head(fresh_pg_dsn)
    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _seed_all_states(session: AsyncSession) -> None:
    """Eine Quelle je Zustand (AC5); alles in einer Transaktion, ein Commit.

    A: ungestempelt (aber attestiert + mit Span)
    B: unattestiert, keine Capture-Anfrage
    C: unattestiert, LETZTE Capture-Anfrage ``failed`` (eine ältere ``captured``
       davor — die Zählung darf auf der Neuesten stehen)
    D: attestiert, ohne Span
    E: attestiert, mit Span
    """
    await session.execute(_ADAPTER_INSERT, {"n": "dip-api", "v": "1.0.0", "t": "verified_primary"})

    ids: dict[str, object] = {}
    for letter, i in (("A", 1), ("B", 2), ("C", 3), ("D", 4), ("E", 5)):
        row = (await session.execute(_SOURCE_INSERT, _source_params(i))).first()
        assert row is not None
        ids[letter] = row[0]

    # Zeitstempel für alle außer A (ungestempelt).
    for letter in ("B", "C", "D", "E"):
        await session.execute(
            _TIMESTAMP_INSERT,
            {"sid": ids[letter], "tsa": _TSA, "ref": _TOKEN_REF},
        )

    # Attestierung für A, D, E (B und C bleiben unattestiert).
    for letter in ("A", "D", "E"):
        await session.execute(_ARCHIVE_INSERT, {"sid": ids[letter], "arch": _ARCHIVER})

    # Spans für A und E (nach der Attestierung — Trigger 0006).
    speaker_row = (await session.execute(_SPEAKER_INSERT, {"name": "Test Person"})).first()
    assert speaker_row is not None
    speaker_id = speaker_row[0]
    for letter in ("A", "E"):
        await session.execute(
            _SPAN_INSERT,
            {
                "sid": ids[letter],
                "spid": speaker_id,
                "vt": _VERBATIM,
                "sh": _SPAN_HASH,
            },
        )

    # C: ältere ``captured``-Anfrage, dann neuere ``failed``-Anfrage —
    # die letzte (neueste) zählt.
    now = datetime.now(UTC)
    await session.execute(
        _CAPTURE_INSERT,
        {
            "sid": ids["C"],
            "arch": _ARCHIVER,
            "oc": "captured",
            "url": "https://web.archive.org/snap-0132",
            "reason": None,
            "at": now - timedelta(days=2),
        },
    )
    await session.execute(
        _CAPTURE_INSERT,
        {
            "sid": ids["C"],
            "arch": _ARCHIVER,
            "oc": "failed",
            "url": None,
            "reason": "wayback:http_status_429",
            "at": now - timedelta(hours=1),
        },
    )
    await session.commit()


async def test_backlog_counts(fresh_sessions: async_sessionmaker[AsyncSession]) -> None:
    """AC5: genau eine Quelle je Zustand → die fünf Zählungen sind exakt."""
    async with fresh_sessions() as session:
        await _seed_all_states(session)
        counts = await backlog_counts(session)

    assert counts == BacklogCounts(
        sources=5,
        unstamped=1,
        unattested=2,
        unattested_capture_failed=1,
        attested_without_spans=1,
    )
