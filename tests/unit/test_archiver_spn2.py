"""Unit: WaybackArchiver auf dem SPN2-Pfad (Spec 0108, #108).

Gegen einen echten ``httpx.AsyncClient`` mit ``MockTransport`` (kein Netz,
R-TEST-03): Der Fluss besteht aus mehreren Requests mit unterschiedlichen
Antworten (POST, dann N Status-Abrufe), und AC3/AC4 behaupten etwas über die
tatsächlich gesetzten Header. Der Handler sammelt die echten
``httpx.Request``-Objekte in einer LOKALEN Liste (Closure, kein
Modulzustand — S8997).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from wortlaut.archive.archiver import WaybackArchiver, WaybackTuning
from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.spn2 import IaCredentials


def _access_key() -> str:
    """Test-Key, zusammengesetzt statt literal (S6698)."""
    return "k-" + "abc-1"


def _secret() -> str:
    """Test-Secret, zusammengesetzt statt literal (S6698)."""
    return "s-" + "xyz-2"


def _credentials() -> IaCredentials:
    return IaCredentials(access_key=_access_key(), secret=_secret())


class _SleepRecorder:
    """Injizierte Sleep (R-TEST-03): zählt, wartet nie real."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _archiver_with_transport(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    requests: list[httpx.Request],
    credentials: IaCredentials | None = None,
    tuning: WaybackTuning | None = None,
) -> WaybackArchiver:
    """WaybackArchiver mit Mock-Transport; der Handler füllt ``requests`` (Closure)."""

    def _record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    wayback = WaybackArchiver(credentials=credentials, tuning=tuning, sleep=_SleepRecorder())
    client = httpx.AsyncClient(transport=httpx.MockTransport(_record), follow_redirects=False)
    wayback._client = client
    return wayback


def _capture_handler(
    job_id: str, success_payload: dict[str, object]
) -> Callable[[httpx.Request], httpx.Response]:
    """Handler: POST /save → job_id; Status-Abruf → ``success_payload``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/save":
            return httpx.Response(200, json={"url": "https://beispiel.test/x", "job_id": job_id})
        if request.url.path == f"/save/status/{job_id}":
            return httpx.Response(200, json=success_payload)
        return httpx.Response(404, json={"detail": "not found"})

    return handler


def _status_requests(requests: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in requests if r.url.path.startswith("/save/status/")]


def _post_requests(requests: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in requests if r.url.path == "/save"]


def _success_payload() -> dict[str, object]:
    return {
        "status": "success",
        "timestamp": "20260827142259",
        "original_url": "https://beispiel.test/x",
    }


async def test_post_auf_save_mit_auth_header() -> None:
    """AC3: Request 1 ist POST an /save — form-kodiert, mit Accept und
    Authorization ``LOW <key>:<secret>``. Der Body ist prozent-kodiert,
    deshalb ``parse_qs`` statt Substring-Vergleich."""
    requests: list[httpx.Request] = []
    wayback = _archiver_with_transport(
        _capture_handler("spn2-abc", _success_payload()),
        requests=requests,
        credentials=_credentials(),
    )

    result = await wayback.archive("https://beispiel.test/x")

    post = _post_requests(requests)
    assert len(post) == 1
    request = post[0]
    assert request.method == "POST"
    assert str(request.url) == "https://web.archive.org/save"
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Authorization"] == "LOW k-abc-1:s-xyz-2"
    assert parse_qs(request.content.decode())["url"] == ["https://beispiel.test/x"]
    assert result == "https://web.archive.org/web/20260827142259/https://beispiel.test/x"


async def test_ohne_zugangsdaten_kein_header() -> None:
    """AC4: ohne Zugangsdaten trägt der Request KEINEN Authorization-Header
    (der Archiver arbeitet ohne weiter — die Pflicht liegt am CLI)."""
    requests: list[httpx.Request] = []
    wayback = _archiver_with_transport(
        _capture_handler("spn2-abc", _success_payload()), requests=requests
    )

    await wayback.archive("https://beispiel.test/x")

    post = _post_requests(requests)
    assert len(post) == 1
    assert "Authorization" not in post[0].headers


async def test_polling_bis_success_baut_snapshot_url() -> None:
    """AC5: zweimal ``pending``, dann ``success`` → exakte Snapshot-URL aus
    ``timestamp`` + ``original_url``; genau DREI Status-Abrufe."""
    requests: list[httpx.Request] = []
    job_id = "spn2-abc"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/save":
            return httpx.Response(200, json={"url": "https://beispiel.test/x", "job_id": job_id})
        if request.url.path == f"/save/status/{job_id}":
            # Zweimal pending, dann success. ``_record`` hängt das aktuelle
            # Request bereits an, also zählt ``polls`` inkl. dieses Aufrufs:
            # 1. → pending, 2. → pending, 3. → success.
            polls = len(_status_requests(requests))
            payload: dict[str, object] = _success_payload() if polls >= 3 else {"status": "pending"}
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"detail": "not found"})

    # Die injizierte Sleep ist sofort — das Interval ist deshalb nur die
    # Versuchszahl-Basis (default 3.0), nie reale Wartezeit.
    wayback = _archiver_with_transport(handler, requests=requests, credentials=_credentials())

    result = await wayback.archive("https://beispiel.test/x")

    assert result == "https://web.archive.org/web/20260827142259/https://beispiel.test/x"
    assert len(_status_requests(requests)) == 3  # genau drei Status-Abrufe


async def test_polling_timeout() -> None:
    """AC9: dauernd ``pending`` → nach genau 3 Polls (9 s // 3 s)
    ``capture_timeout`` (permanent); der injizierte Sleep wurde nie mit einem
    realen ``asyncio.sleep`` bedient (keine reale Wartezeit)."""
    requests: list[httpx.Request] = []
    sleep = _SleepRecorder()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/save":
            return httpx.Response(
                200,
                json={
                    "url": "https://beispiel.test/x",
                    "job_id": "spn2-abc",
                },
            )
        return httpx.Response(200, json={"status": "pending"})

    wayback = WaybackArchiver(
        credentials=_credentials(),
        tuning=WaybackTuning(poll_interval_seconds=3.0, poll_timeout_seconds=9.0),
        sleep=sleep,
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    wayback._client = client

    with pytest.raises(ArchiveError) as excinfo:
        await wayback.archive("https://beispiel.test/x")

    err = excinfo.value
    assert err.service == "wayback"
    assert err.reason == "capture_timeout"
    assert err.transient is False
    assert len(_status_requests(requests)) == 3  # genau drei Status-Abrufe
    assert sleep.delays == [3.0, 3.0, 3.0]  # injizierte Sleep, nie real


async def test_401_ist_permanent_und_ohne_retry() -> None:
    """AC10: HTTP 401 auf den POST → ``unauthorized`` (permanent) und der
    Aufruf wurde GENAU EINMAL abgesetzt (kein Retry)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # ``requests`` wird von ``_archiver_with_transport`` (_record) gefüllt —
        # der Handler liefert nur die Antwort (keine Doppel-Append).
        return httpx.Response(401, json={"message": "You need to be logged in."})

    wayback = _archiver_with_transport(
        handler, requests=requests, credentials=_credentials(), tuning=WaybackTuning(attempts=3)
    )

    with pytest.raises(ArchiveError) as excinfo:
        await wayback.archive("https://beispiel.test/x")

    err = excinfo.value
    assert err.service == "wayback"
    assert err.reason == "unauthorized"
    assert err.status_code == 401
    assert err.transient is False
    assert len(_post_requests(requests)) == 1  # genau ein POST, kein Retry
    assert _status_requests(requests) == []  # kein Status-Abruf


async def test_secret_nicht_im_log(caplog: pytest.LogCaptureFixture) -> None:
    """AC13: auch auf ``logging.DEBUG`` landen weder Access-Key noch Secret
    noch die Zeichenkette ``"LOW "`` in einer Logzeile (R-SEC-01) — auch
    nicht im Fehlerpfad."""
    caplog.set_level(logging.DEBUG)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "intern"})

    wayback = _archiver_with_transport(
        handler, requests=requests, credentials=_credentials(), tuning=WaybackTuning(attempts=1)
    )

    with pytest.raises(ArchiveError):
        await wayback.archive("https://beispiel.test/x")

    assert _access_key() not in caplog.text
    assert _secret() not in caplog.text
    assert "LOW " not in caplog.text
