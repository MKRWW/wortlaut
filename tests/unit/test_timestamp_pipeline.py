"""Unit (Spec 0076): timestamp_source — Hash-Gegenprüfung + Happy Path (AC10, AC11).

Rein: ``WormStore`` und ``TimeStamper`` sind schlanke Fakes (Protokoll erfüllen,
Aufrufe zählen) — kein MinIO im Unit-Test.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

from wortlaut.pipeline.timestamp import timestamp_source
from wortlaut.store.timestamps import PendingSource
from wortlaut.timestamp.tsa import StampResult


class _FakeWorm:
    """WormStore-Fake: get liefert feste Bytes, put zählt und gibt eine Ref zurück."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.get_calls = 0
        self.put_calls: list[tuple[str, bytes, str]] = []

    async def ensure_bucket(self) -> None:
        raise AssertionError("not used")

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        self.put_calls.append((key, data, content_type))
        return f"s3://bucket/{key}?versionId=1"

    async def get(self, ref: str) -> bytes:
        self.get_calls += 1
        return self._data


class _FakeStamper:
    """TimeStamper-Fake: stamp zählt Aufrufe und liefert ein gültiges StampResult."""

    def __init__(self, *, tsa_name: str = "freetsa") -> None:
        self.tsa_name = tsa_name
        self.stamp_calls = 0
        self.failures: tuple[str, ...] = ()

    async def stamp(self, raw: bytes, *, content_hash: str) -> StampResult:
        self.stamp_calls += 1
        return StampResult(self.tsa_name, b"token-der", datetime.now(UTC))


def _pending(content_hash: str) -> PendingSource:
    return PendingSource(
        source_id=UUID(int=1),
        content_hash=content_hash,
        raw_bytes_ref="s3://bucket/raw?versionId=1",
    )


async def test_hash_mismatch_never_stamps() -> None:  # AC10
    """AC10: WORM-Bytes passen NICHT zum content_hash → hash_mismatch, Stamper 0×,
    keine source_timestamp-Zeile."""
    raw = b"manipulierte rohbytes"
    wrong_hash = "0" * 64  # != sha256(raw)
    worm = _FakeWorm(raw)
    stamper = _FakeStamper()

    outcome = await timestamp_source(
        _pending(wrong_hash), session=AsyncMock(), worm=worm, stamper=stamper
    )

    assert outcome.status == "hash_mismatch"
    assert outcome.tsa_name is None
    assert stamper.stamp_calls == 0  # Stamper NICHT aufgerufen
    assert worm.put_calls == []  # kein Token in WORM, keine Zeile
    assert worm.get_calls == 1


async def test_happy_path_persists_token() -> None:  # AC11
    """AC11: passende WORM-Bytes + funktionierende TSA → stamped, Token liegt als
    WORM-Objekt unter {content_hash}.{tsa}.tsr, genau eine Zeile mit token_ref."""
    raw = b"amtliches protokoll rohbytes"
    expected_hash = hashlib.sha256(raw).hexdigest()
    worm = _FakeWorm(raw)
    stamper = _FakeStamper(tsa_name="freetsa")
    session = AsyncMock()

    outcome = await timestamp_source(
        _pending(expected_hash), session=session, worm=worm, stamper=stamper
    )

    assert outcome.status == "stamped"
    assert outcome.tsa_name == "freetsa"
    assert stamper.stamp_calls == 1
    # Genau ein WORM-put, unter dem content-adressierten Key {content_hash}.{tsa}.tsr
    assert len(worm.put_calls) == 1
    key, data, content_type = worm.put_calls[0]
    assert key == f"{expected_hash}.freetsa.tsr"
    assert content_type == "application/timestamp-reply"
    assert data == b"token-der"
