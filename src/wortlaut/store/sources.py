"""Source-Dedup: Vorab-Check, ob eine Quelle bereits existiert."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.store.models import Source


async def source_exists(session: AsyncSession, content_hash: str) -> bool:
    """True, wenn bereits eine source mit diesem content_hash existiert (Dedup-Vorabcheck)."""
    result = await session.scalar(select(Source.id).where(Source.content_hash == content_hash))
    return result is not None
