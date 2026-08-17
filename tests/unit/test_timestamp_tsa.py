"""Unit (Spec 0076): Rfc3161Tsa + FallbackTimeStamper — Antwort-Härtung (AC6–AC9).

Keine Netz-Calls: TSA-Antworten werden über ``httpx.MockTransport`` gemockt; als
Body dient das **echte** Fixture-Token aus ``tests/fixtures/tsa/``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from wortlaut.timestamp.errors import TimestampError
from wortlaut.timestamp.profiles import load_profile
from wortlaut.timestamp.tsa import FallbackTimeStamper, Rfc3161Tsa

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "tsa"
_MESSAGE_HASH = "fca714d25fbd7eef88f5e936023610e6e115814a702797cb97b6f22a9a059a99"


def _token(name: str) -> bytes:
    return (_FIXTURES / f"{name}.tsr").read_bytes()


def _raw() -> bytes:
    return (_FIXTURES / "message.bin").read_bytes()


def _tsa_with(
    profile_name: str,
    *,
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[Rfc3161Tsa, list[httpx.Request]]:
    """Rfc3161Tsa mit injiziertem MockTransport; zählt die Anfragen."""
    tsa = Rfc3161Tsa(load_profile(profile_name), timeout_seconds=1.0)
    requests: list[httpx.Request] = []

    def _h(req: httpx.Request) -> httpx.Response:
        requests.append(req)
        return handler(req)

    tsa._client = httpx.AsyncClient(transport=httpx.MockTransport(_h), follow_redirects=False)
    return tsa, requests


def _ok_response(body: bytes) -> httpx.Response:
    return httpx.Response(
        200, content=body, headers={"content-type": "application/timestamp-reply"}
    )


async def test_fallback_uses_second_tsa() -> None:  # AC6
    """AC6: TSA A (freetsa) wirft, TSA B (sigstore) liefert gültig → Ergebnis von B,
    A genau 1×, failures enthält A's label."""
    a, a_requests = _tsa_with(
        "freetsa",
        handler=lambda _req: httpx.Response(503, content=b"unverfuegbar"),
    )
    b, b_requests = _tsa_with("sigstore", handler=lambda _req: _ok_response(_token("sigstore")))
    stamper = FallbackTimeStamper([a, b])

    result = await stamper.stamp(_raw(), content_hash=_MESSAGE_HASH)

    assert result.tsa_name == "sigstore"
    assert len(a_requests) == 1  # A genau 1× aufgerufen
    assert len(b_requests) == 1
    assert stamper.failures == ("freetsa:http_status_503",)
    await stamper.aclose()


async def test_all_tsa_fail_raises() -> None:  # AC7
    """AC7: beide TSAs werfen → TimestampError, failures enthält beide Labels."""
    a, a_requests = _tsa_with(
        "freetsa",
        handler=lambda _req: httpx.Response(503, content=b"unverfuegbar"),
    )
    b, b_requests = _tsa_with(
        "sigstore",
        handler=lambda _req: httpx.Response(504, content=b"timeout"),
    )
    stamper = FallbackTimeStamper([a, b])

    # raw ausserhalb des raises-Blocks: dort steht genau EIN werfender Aufruf (Sonar S5778).
    raw = _raw()
    with pytest.raises(TimestampError):
        await stamper.stamp(raw, content_hash=_MESSAGE_HASH)

    assert len(a_requests) == 1
    assert len(b_requests) == 1
    assert "freetsa:http_status_503" in stamper.failures
    assert "sigstore:http_status_504" in stamper.failures
    await stamper.aclose()


async def test_response_hardening_rejects() -> None:  # AC8
    """AC8: 200 mit falschem Content-Type / >64 KiB / 302-Redirect → TimestampError,
    kein Token wird zurückgegeben."""
    # raw ausserhalb aller raises-Bloecke: dort steht je genau EIN werfender
    # Aufruf (Sonar S5778).
    raw = _raw()

    # (a) 200 + gültig aussehender Body, aber Content-Type: text/html
    tsa, _ = _tsa_with(
        "freetsa",
        handler=lambda _req: httpx.Response(
            200, content=_token("freetsa"), headers={"content-type": "text/html"}
        ),
    )
    with pytest.raises(TimestampError) as exc:
        await tsa.stamp(raw, content_hash=_MESSAGE_HASH)
    assert exc.value.reason == "content_type"

    # (b) 200 + korrektem Content-Type, aber >64 KiB
    tsa2, _ = _tsa_with(
        "freetsa",
        handler=lambda _req: httpx.Response(
            200, content=b"x" * 70000, headers={"content-type": "application/timestamp-reply"}
        ),
    )
    with pytest.raises(TimestampError) as exc2:
        await tsa2.stamp(raw, content_hash=_MESSAGE_HASH)
    assert exc2.value.reason == "oversize"

    # (c) 302 mit Location (Redirects werden nicht gefolgt)
    tsa3, _ = _tsa_with(
        "freetsa",
        handler=lambda _req: httpx.Response(
            302, content=b"", headers={"location": "https://elsewhere.example/tsr"}
        ),
    )
    with pytest.raises(TimestampError) as exc3:
        await tsa3.stamp(raw, content_hash=_MESSAGE_HASH)
    assert exc3.value.reason == "http_status"
    assert exc3.value.status_code == 302
    await tsa.aclose()
    await tsa2.aclose()
    await tsa3.aclose()


async def test_token_for_foreign_imprint_rejected() -> None:  # AC9
    """AC9 (🔴 Kern): TSA liefert 200 mit dem echten Fixture-Token, obwohl ganz andere
    Rohbytes gestempelt werden sollten (fremder Imprint) → TimestampError('mismatch'),
    das Token wird NICHT zurückgegeben und damit nie persistiert."""
    other_hash = "0" * 64  # ein anderer, gültiges 64-stelliges Hex

    tsa, _ = _tsa_with("freetsa", handler=lambda _req: _ok_response(_token("freetsa")))
    raw = _raw()  # ausserhalb des raises-Blocks (Sonar S5778)
    with pytest.raises(TimestampError) as exc:
        await tsa.stamp(raw, content_hash=other_hash)
    assert exc.value.reason == "mismatch"
    await tsa.aclose()


def test_message_hash_matches_fixture() -> None:
    """Sanity: der erwartete Hash ist sha256(message.bin) (Grundlage für AC9)."""
    assert hashlib.sha256(_raw()).hexdigest() == _MESSAGE_HASH


def test_fallback_requires_at_least_one_tsa() -> None:
    """Review-Fix: leere Kette ist Fehlkonfiguration (WORTLAUT_TSA_PROFILES="") und
    scheitert schon im Konstruktor — nicht erst je Quelle mitten im Lauf.

    Der Composition-Root faengt genau dieses ValueError und meldet Exit 2.
    """
    with pytest.raises(ValueError, match="mindestens eine TSA"):
        FallbackTimeStamper([])
