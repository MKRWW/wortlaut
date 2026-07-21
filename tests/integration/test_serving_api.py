"""Integration (#43): Read-API gegen echtes Postgres + MinIO (ASGI, kein Netz).

Deckt den harten Server-Filter (AC2/AC6), den vollen Wortlaut (AC3), Kontext (AC4),
Verify inkl. Manipulation (AC5), Filter (AC8) und das Anti-Halluzinations-Gate (AC9).
Frische DB je Test (fresh_pg_dsn).
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from wortlaut.evidence.hashing import span_hash
from wortlaut.serving.app import create_app
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.sources import NewSource, insert_source
from wortlaut.store.spans import NewSpan, insert_span
from wortlaut.store.worm import WormStore

pytestmark = pytest.mark.integration

_LOCATOR_TOP3: dict[str, object] = {
    "protokoll": "20/88",
    "sitzung": "88",
    "tagesordnungspunkt": "3",
}
_LOCATOR_TOP4: dict[str, object] = dict(_LOCATOR_TOP3, tagesordnungspunkt="4")

# Öffentliche Beiträge (Quelle A) — normalized_A wird aus diesen Sätzen gebaut.
_V1 = "Die Digitalisierung ist ein wichtiges Thema fuer das Land."
_V2 = "Die Bildung verdient endlich mehr Aufmerksamkeit."
_V3 = "Zur Wirtschaft gibt es einen weiteren Punkt."
_MACHINE = "Dieser Beitrag ist maschinell zugeordnet."
_REDACTED = "Dieser Beitrag wurde redigiert."
_TAIL = "HIER STEHT ETWAS ANDERES ALS DER GEFAELSCHTE SPAN."
_NORMALIZED_A = " ".join([_V1, _V2, _V3, _MACHINE, _REDACTED, _TAIL])

_VB = "Ein Satz auf Quelle B zur Integritaetspruefung."
_NORMALIZED_B = _VB


@pytest.fixture
async def fresh_sessions(fresh_pg_dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    from wortlaut.store.db import create_async_engine_from, make_sessionmaker
    from wortlaut.store.settings import DbSettings

    await upgrade_head(fresh_pg_dsn)
    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _speaker(session: AsyncSession, name: str) -> UUID:
    sid = await session.scalar(
        text("INSERT INTO speaker (full_name) VALUES (:n) RETURNING id"), {"n": name}
    )
    return UUID(str(sid))


async def _mandate(session: AsyncSession, speaker_id: UUID, party: str) -> UUID:
    mid = await session.scalar(
        text(
            "INSERT INTO mandate (speaker_id, role, parliament, party, active_from) "
            "VALUES (CAST(:s AS uuid), 'MdB', 'bundestag', :p, CAST('2021-10-26' AS date)) "
            "RETURNING id"
        ),
        {"s": str(speaker_id), "p": party},
    )
    return UUID(str(mid))


async def _source(session: AsyncSession, content_hash: str, ref: str, normalized: str) -> UUID:
    await session.execute(
        text(
            "INSERT INTO ingest_adapter (name, version, trust_level) "
            "VALUES ('dip-api','1.0.0',CAST('verified_primary' AS trust_level)) "
            "ON CONFLICT (name, version) DO NOTHING"
        )
    )
    await session.commit()
    return await insert_source(
        session,
        NewSource(
            content_hash=content_hash,
            raw_bytes_ref=ref,
            archive_wayback="https://web.archive.org/snap",
            archive_today=None,
            origin_url="https://dserver.bundestag.de/btp/20/2008800/2008800.pdf",
            source_type="plenarprotokoll",
            rights_basis="amtliches_werk_p5",
            adapter_name="dip-api",
            adapter_version="1.0.0",
            byte_size=100,
            mime_type="application/pdf",
            retrieved_at=datetime.now(UTC),
            normalized_text=normalized,
        ),
    )


@dataclass(frozen=True)
class _SpanSpec:
    """Ein zu seedender Span (gebündelt → schlanke Helfer-Signatur)."""

    source_id: UUID
    speaker_id: UUID
    mandate_id: UUID
    verbatim: str
    normalized: str
    slice_key: str  # Offsets zeigen hierauf; == verbatim (ehrlich) oder != (manipuliert, AC9)
    spoken_at: date
    locator: dict[str, object]
    verification: str = "official"
    visibility: str = "public"
    redacted: bool = False


async def _span(session: AsyncSession, spec: _SpanSpec) -> UUID:
    """Fügt Span + span_state ein; Offsets zeigen auf ``slice_key`` in ``normalized``."""
    start = spec.normalized.index(spec.slice_key)
    end = start + len(spec.slice_key)
    span_id = await insert_span(
        session,
        NewSpan(
            source_id=spec.source_id,
            speaker_id=spec.speaker_id,
            mandate_id=spec.mandate_id,
            verbatim_text=spec.verbatim,
            text_start=start,
            text_end=end,
            spoken_at=spec.spoken_at,
            locator=spec.locator,  # direkt beim Insert (span ist append-only, #40)
            permalink="https://dserver.bundestag.de/btp/20/2008800/2008800.pdf#p1",
            span_hash=span_hash(spec.verbatim),
        ),
    )
    await session.execute(
        text(
            "INSERT INTO span_state (span_id, verification, visibility, redacted) "
            "VALUES (CAST(:s AS uuid), CAST(:v AS verification), "
            "CAST(:vis AS visibility_class), :r)"
        ),
        {"s": str(span_id), "v": spec.verification, "vis": spec.visibility, "r": spec.redacted},
    )
    await session.commit()
    return span_id


async def _seed(session: AsyncSession, worm: WormStore) -> dict[str, UUID]:
    # Quelle A: echte Rohbytes im WORM, content_hash passt (verify → ok)
    raw_a = b"quelle A rohbytes fuer verify"
    hash_a = hashlib.sha256(raw_a).hexdigest()
    ref_a = await worm.put(hash_a, raw_a, content_type="application/pdf")
    src_a = await _source(session, hash_a, ref_a, _NORMALIZED_A)

    mm = await _speaker(session, "Dr. Max Mustermann")
    mm_m = await _mandate(session, mm, "AfD")
    mf = await _speaker(session, "Erika Musterfrau")
    mf_m = await _mandate(session, mf, "SPD")

    day5 = date(2024, 7, 5)
    ids: dict[str, UUID] = {"src_a": src_a}
    ids["v1"] = await _span(
        session,
        _SpanSpec(src_a, mm, mm_m, _V1, _NORMALIZED_A, _V1, day5, _LOCATOR_TOP3),
    )
    ids["v2"] = await _span(
        session,
        _SpanSpec(src_a, mf, mf_m, _V2, _NORMALIZED_A, _V2, day5, _LOCATOR_TOP3),
    )
    ids["v3"] = await _span(
        session,
        _SpanSpec(src_a, mm, mm_m, _V3, _NORMALIZED_A, _V3, date(2024, 7, 6), _LOCATOR_TOP4),
    )
    ids["machine"] = await _span(
        session,
        _SpanSpec(
            src_a,
            mm,
            mm_m,
            _MACHINE,
            _NORMALIZED_A,
            _MACHINE,
            day5,
            _LOCATOR_TOP3,
            verification="machine",
        ),
    )
    ids["redacted"] = await _span(
        session,
        _SpanSpec(
            src_a, mm, mm_m, _REDACTED, _NORMALIZED_A, _REDACTED, day5, _LOCATOR_TOP3, redacted=True
        ),
    )
    # Manipulierter Span (AC9): verbatim != normalized[slice_key] → Anti-Halluzination schlägt an
    ids["tampered"] = await _span(
        session,
        _SpanSpec(src_a, mm, mm_m, "GEFAELSCHTER TEXT", _NORMALIZED_A, _TAIL, day5, _LOCATOR_TOP3),
    )

    # Quelle B: WORM enthält MANIPULIERTE Bytes, content_hash = Hash der ORIGINALbytes → mismatch
    hash_b = hashlib.sha256(b"ORIGINAL B").hexdigest()
    ref_b = await worm.put(hash_b, b"MANIPULIERT B", content_type="application/pdf")
    src_b = await _source(session, hash_b, ref_b, _NORMALIZED_B)
    ids["vb"] = await _span(
        session,
        _SpanSpec(src_b, mm, mm_m, _VB, _NORMALIZED_B, _VB, day5, _LOCATOR_TOP3),
    )
    return ids


async def _client(
    fresh_sessions: async_sessionmaker[AsyncSession], worm: WormStore
) -> tuple[AsyncClient, dict[str, UUID]]:
    async with fresh_sessions() as session:
        ids = await _seed(session, worm)
    transport = ASGITransport(app=create_app(fresh_sessions, worm))
    return AsyncClient(transport=transport, base_url="http://test"), ids


async def test_search_returns_full_span_with_fields(
    fresh_sessions: async_sessionmaker[AsyncSession], worm_store: WormStore
) -> None:  # AC1 + AC3
    client, ids = await _client(fresh_sessions, worm_store)
    async with client:
        resp = await client.get("/v1/search", params={"q": "Digitalisierung"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    hit = next(r for r in body["results"] if r["span_id"] == str(ids["v1"]))
    assert hit["verbatim_text"] == _V1  # AC3: voller Beitrag, nicht gecroppt
    assert hit["speaker"]["party"] == "AfD"
    assert hit["source"]["content_hash"]
    assert hit["locator"]["tagesordnungspunkt"] == "3"
    assert hit["match"]["start"] == _V1.index("Digitalisierung")


async def test_machine_redacted_tampered_never_served(
    fresh_sessions: async_sessionmaker[AsyncSession], worm_store: WormStore
) -> None:  # AC2 + AC9
    client, ids = await _client(fresh_sessions, worm_store)
    async with client:
        search = (await client.get("/v1/search", params={"q": "Beitrag"})).json()
        served = {r["span_id"] for r in search["results"]}
        assert str(ids["machine"]) not in served
        assert str(ids["redacted"]) not in served
        assert str(ids["tampered"]) not in served
        for key in ("machine", "redacted", "tampered"):
            assert (await client.get(f"/v1/spans/{ids[key]}")).status_code == 404


async def test_no_internal_fields_leak(
    fresh_sessions: async_sessionmaker[AsyncSession], worm_store: WormStore
) -> None:  # AC6
    client, ids = await _client(fresh_sessions, worm_store)
    async with client:
        raw = (await client.get(f"/v1/spans/{ids['v1']}")).text
    assert "raw_bytes_ref" not in raw
    assert "s3://" not in raw
    assert "machine" not in raw


async def test_span_detail_has_context_bundle(
    fresh_sessions: async_sessionmaker[AsyncSession], worm_store: WormStore
) -> None:  # AC4
    client, ids = await _client(fresh_sessions, worm_store)
    async with client:
        detail = (await client.get(f"/v1/spans/{ids['v1']}")).json()
    top3_texts = {c["verbatim_text"] for c in detail["context"]}
    # Nachbar-Beiträge desselben TOP (3): v1 + v2; NICHT v3 (TOP 4) oder ausgeschlossene.
    assert _V1 in top3_texts
    assert _V2 in top3_texts
    assert _V3 not in top3_texts
    assert "GEFAELSCHTER TEXT" not in top3_texts
    starts = [c["text_start"] for c in detail["context"]]
    assert starts == sorted(starts)  # nach Position geordnet


async def test_filters_party_and_date(
    fresh_sessions: async_sessionmaker[AsyncSession], worm_store: WormStore
) -> None:  # AC8
    client, _ = await _client(fresh_sessions, worm_store)
    async with client:
        afd = (await client.get("/v1/search", params={"q": "Punkt", "party": "AfD"})).json()
        assert all(r["speaker"]["party"] == "AfD" for r in afd["results"])
        dated = (await client.get("/v1/search", params={"q": "Punkt", "from": "2024-07-06"})).json()
        assert all(r["spoken_at"] >= "2024-07-06" for r in dated["results"])


async def test_verify_ok_and_hash_mismatch(
    fresh_sessions: async_sessionmaker[AsyncSession], worm_store: WormStore
) -> None:  # AC5
    client, ids = await _client(fresh_sessions, worm_store)
    async with client:
        ok = (await client.get(f"/v1/spans/{ids['v1']}/verify")).json()
        assert ok["ok"] is True
        assert ok["status"] == "ok"
        assert ok["span_in_source"] is True
        mismatch = (await client.get(f"/v1/spans/{ids['vb']}/verify")).json()
        assert mismatch["ok"] is False
        assert mismatch["status"] == "hash_mismatch"


async def test_source_evidence_no_internals(
    fresh_sessions: async_sessionmaker[AsyncSession], worm_store: WormStore
) -> None:  # Beleg-Endpoint, keine WORM-Interna
    client, ids = await _client(fresh_sessions, worm_store)
    async with client:
        resp = await client.get(f"/v1/sources/{ids['src_a']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["content_hash"]
    assert body["permalink"].startswith("https://")
    assert body["rights_basis"] == "amtliches_werk_p5"
    assert "raw_bytes_ref" not in resp.text
    assert "s3://" not in resp.text


async def test_unknown_ids_return_404(
    fresh_sessions: async_sessionmaker[AsyncSession], worm_store: WormStore
) -> None:
    client, _ = await _client(fresh_sessions, worm_store)
    missing = "00000000-0000-0000-0000-000000000000"
    async with client:
        assert (await client.get(f"/v1/spans/{missing}")).status_code == 404
        assert (await client.get(f"/v1/spans/{missing}/verify")).status_code == 404
        assert (await client.get(f"/v1/sources/{missing}")).status_code == 404
