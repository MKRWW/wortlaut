"""Unit (#8): verify_source — WORM-Rohbytes neu hashen, Statusmatrix.

Rein: Fake-WORM + gepatchtes get_source_by_id, keine DB, kein Netz.

Die Zeitstempel-Abfrage (Spec 0076) wird hier per Autouse-Fixture auf „keine Zeile"
gestubbt: diese Tests messen die HASH-Statusmatrix, nicht den Zeitstempel. Bewusst
ein Stub und kein ``try/except`` in der Produktion — ein DB-Fehler soll in
``verify_source`` durchschlagen wie jeder andere DB-Fehler dort auch, statt still
zu „kein Zeitstempel" zu degradieren.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from wortlaut.pipeline.verify import verify_source


@pytest.fixture(autouse=True)
def _no_timestamps() -> Iterator[None]:
    """Standard: die Quelle hat keine Zeitstempel-Zeile (→ timestamp_status 'missing')."""
    with patch(
        "wortlaut.pipeline.verify.get_timestamps_for_source", new=AsyncMock(return_value=[])
    ):
        yield


def _source(
    content_hash_hex: str,
    *,
    wayback: str | None = "https://web.archive.org/x",
    today: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        content_hash=content_hash_hex,
        raw_bytes_ref="s3://bucket/key?versionId=1",
        archive_wayback=wayback,
        archive_today=today,
    )


class _FakeWorm:
    """WormStore-Fake: get liefert feste Bytes oder wirft (fehlendes Objekt)."""

    def __init__(self, data: bytes | None = None, *, missing: bool = False) -> None:
        self._data = data
        self._missing = missing
        self.get_calls = 0

    async def ensure_bucket(self) -> None:
        raise AssertionError("not used")

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        raise AssertionError("not used")

    async def get(self, ref: str) -> bytes:
        self.get_calls += 1
        if self._missing:
            raise FileNotFoundError(f"worm object missing: {ref}")
        assert self._data is not None
        return self._data


@pytest.mark.asyncio
async def test_verify_ok_intact() -> None:  # AC1 + AC6
    raw = b"amtliches protokoll bytes"
    expected = hashlib.sha256(raw).hexdigest()
    src = _source(expected)
    with patch("wortlaut.pipeline.verify.get_source_by_id", new=AsyncMock(return_value=src)):
        report = await verify_source(uuid4(), session=AsyncMock(), worm=_FakeWorm(raw))
    assert report.ok is True
    assert report.status == "ok"
    assert report.content_hash_actual == report.content_hash_expected == expected
    assert report.archive_wayback == "https://web.archive.org/x"  # AC6
    assert report.archive_today is None
    # Spec 0076 AC14: kein Zeitstempel degradiert das Hash-Urteil NICHT.
    assert report.timestamp_status == "missing"
    assert report.timestamp_gen_time is None


@pytest.mark.asyncio
async def test_verify_hash_mismatch() -> None:  # AC2
    raw = b"original"
    expected = hashlib.sha256(raw).hexdigest()
    src = _source(expected)
    with patch("wortlaut.pipeline.verify.get_source_by_id", new=AsyncMock(return_value=src)):
        report = await verify_source(uuid4(), session=AsyncMock(), worm=_FakeWorm(b"manipuliert"))
    assert report.ok is False
    assert report.status == "hash_mismatch"
    assert report.content_hash_actual != report.content_hash_expected
    assert report.content_hash_expected == expected


@pytest.mark.asyncio
async def test_verify_source_not_found() -> None:  # AC3
    worm = _FakeWorm(b"x")
    with patch("wortlaut.pipeline.verify.get_source_by_id", new=AsyncMock(return_value=None)):
        report = await verify_source(uuid4(), session=AsyncMock(), worm=worm)
    assert report.ok is False
    assert report.status == "source_not_found"
    assert report.content_hash_expected is None
    assert worm.get_calls == 0  # kein WORM-Zugriff bei fehlender Quelle


@pytest.mark.asyncio
async def test_verify_worm_missing() -> None:  # AC4 — kein falsches ok
    src = _source("a" * 64)
    with patch("wortlaut.pipeline.verify.get_source_by_id", new=AsyncMock(return_value=src)):
        report = await verify_source(uuid4(), session=AsyncMock(), worm=_FakeWorm(missing=True))
    assert report.ok is False
    assert report.status == "worm_missing"
    assert report.content_hash_expected == "a" * 64
    assert report.content_hash_actual is None
