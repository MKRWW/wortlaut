"""Idempotenter Adapter-Registry-Seed (FK-Voraussetzung fur source).

Muster wie store/spans.py:resolve_or_create_speaker. Bei existierender
(name,version) NO-OP, kein IntegrityError. Nutzt rohes SQL gegen die
Tabelle ingest_adapter.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_ingest_adapter(
    session: AsyncSession, *, name: str, version: str, trust_level: str
) -> None:
    """Get-or-create der ingest_adapter-Registry-Zeile.

    Atomar idempotent via ON CONFLICT auf dem PK (name, version) — kein
    IntegrityError bei Wiederholung. ``description`` bleibt NULL, ``created_at``
    traegt DEFAULT now() (Migration 0002). ``trust_level`` ist ein PG-ENUM.
    """
    await session.execute(
        text(
            "INSERT INTO ingest_adapter (name, version, trust_level) "
            "VALUES (:n, :v, CAST(:t AS trust_level)) "
            "ON CONFLICT (name, version) DO NOTHING"
        ),
        {"n": name, "v": version, "t": trust_level},
    )
