"""Read-API (#43): FastAPI-Fläche über den öffentlichen, zitierfähigen Korpus.

KI-frei (R-SEC-04): kein LLM/Modell-Call im Ausgabepfad — Ausgabe ist ausschließlich
wörtlicher DB-Span + Beleg. Der harte Server-Filter und das Anti-Halluzinations-Gate
sitzen im Read-Layer (store.read); hier nur Mapping auf die Antwort-Schemas + Verify
(reuse verify_source, #8). Rate-Limit/CORS-Edge macht Cloudflare (Deploy, Nicht-Ziel).

Hinweis: KEIN ``from __future__ import annotations`` — FastAPI muss die Depends/Query
aus den echten Annotationsobjekten lesen (die die lokalen get_session/SessionDep zur
Definitionszeit einfangen); als Strings wären lokale Namen nicht auflösbar → 422.
"""

import re
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from wortlaut.pipeline.verify import verify_source
from wortlaut.serving.schemas import (
    ContextItem,
    LocatorInfo,
    MatchInfo,
    SearchParams,
    SearchResponse,
    SourceEvidence,
    SourceInfo,
    SpanDetail,
    SpanResult,
    SpeakerInfo,
    VerifyResult,
)
from wortlaut.store.read import (
    ContextRow,
    SearchCriteria,
    SourceRow,
    SpanRow,
    get_context,
    get_source,
    get_span,
    search_spans,
)
from wortlaut.store.worm import WormStore

_WORD = re.compile(r"\w+", re.UNICODE)


def _find_match(verbatim_text: str, q: str) -> MatchInfo | None:
    """Best-effort-Highlight: erste Fundstelle des ersten Query-Wortes (croppt nie)."""
    term = _WORD.search(q)
    if term is None:
        return None
    idx = verbatim_text.lower().find(term.group(0).lower())
    if idx < 0:
        return None
    return MatchInfo(start=idx, end=idx + len(term.group(0)))


def _locator(loc: dict[str, object]) -> LocatorInfo:
    def s(key: str) -> str | None:
        value = loc.get(key)
        return str(value) if value is not None else None

    return LocatorInfo(
        protokoll=s("protokoll"), sitzung=s("sitzung"), tagesordnungspunkt=s("tagesordnungspunkt")
    )


def _span_result(row: SpanRow, q: str | None = None) -> SpanResult:
    return SpanResult(
        span_id=row.span_id,
        verbatim_text=row.verbatim_text,
        speaker=SpeakerInfo(
            name=row.speaker_name, party=row.party, role=row.role, parliament=row.parliament
        ),
        spoken_at=row.spoken_at,
        source=SourceInfo(
            type=row.source_type,
            permalink=row.source_permalink,
            archive_wayback=row.archive_wayback,
            archive_today=row.archive_today,
            content_hash=row.content_hash,
            rights_basis=row.rights_basis,
        ),
        span_hash=row.span_hash,
        verification=row.verification,
        locator=_locator(row.locator),
        match=_find_match(row.verbatim_text, q) if q else None,
    )


def _context_item(row: ContextRow) -> ContextItem:
    return ContextItem(
        span_id=row.span_id,
        speaker_name=row.speaker_name,
        party=row.party,
        text_start=row.text_start,
        text_end=row.text_end,
        verbatim_text=row.verbatim_text,
    )


def _source_evidence(row: SourceRow) -> SourceEvidence:
    return SourceEvidence(
        source_id=row.source_id,
        type=row.source_type,
        permalink=row.origin_url,
        content_hash=row.content_hash,
        rights_basis=row.rights_basis,
        archive_wayback=row.archive_wayback,
        archive_today=row.archive_today,
        byte_size=row.byte_size,
        mime_type=row.mime_type,
        retrieved_at=row.retrieved_at,
    )


def create_app(sessionmaker: async_sessionmaker[AsyncSession], worm: WormStore) -> FastAPI:
    """Baut die read-only Read-API. ``worm`` nur für /verify (Hash gegen WORM, #8)."""
    app = FastAPI(title="wortlaut Read-API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://wortlaut.io"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    async def get_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    SessionDep = Annotated[AsyncSession, Depends(get_session)]

    @app.get("/v1/search", response_model=SearchResponse)
    async def search(
        params: Annotated[SearchParams, Query()], session: SessionDep
    ) -> SearchResponse:
        criteria = SearchCriteria(
            q=params.q,
            party=params.party,
            speaker=params.speaker,
            date_from=params.date_from,
            date_to=params.date_to,
            limit=params.limit,
            offset=params.offset,
        )
        rows, total = await search_spans(session, criteria)
        return SearchResponse(results=[_span_result(r, params.q) for r in rows], total=total)

    @app.get("/v1/spans/{span_id}", response_model=SpanDetail)
    async def span_detail(span_id: UUID, session: SessionDep) -> SpanDetail:
        row = await get_span(session, span_id)
        if row is None:
            raise HTTPException(status_code=404, detail="span not found")
        top_raw = row.locator.get("tagesordnungspunkt")
        top = str(top_raw) if isinstance(top_raw, str) else None
        context = await get_context(session, row.source_id, top)
        base = _span_result(row)
        return SpanDetail(**base.model_dump(), context=[_context_item(c) for c in context])

    @app.get("/v1/spans/{span_id}/verify", response_model=VerifyResult)
    async def verify(span_id: UUID, session: SessionDep) -> VerifyResult:
        row = await get_span(session, span_id)
        if row is None:
            raise HTTPException(status_code=404, detail="span not found")
        report = await verify_source(row.source_id, session=session, worm=worm)
        # Der Span hat den Anti-Halluzinations-Filter passiert (get_span) → im Text belegt.
        return VerifyResult(
            ok=report.ok,
            status=report.status,
            content_hash_expected=report.content_hash_expected,
            content_hash_actual=report.content_hash_actual,
            span_in_source=True,
            archive_wayback=report.archive_wayback,
            archive_today=report.archive_today,
        )

    @app.get("/v1/sources/{source_id}", response_model=SourceEvidence)
    async def source_evidence(source_id: UUID, session: SessionDep) -> SourceEvidence:
        row = await get_source(session, source_id)
        if row is None:
            raise HTTPException(status_code=404, detail="source not found")
        return _source_evidence(row)

    return app
