"""CI-Gate: Ein PR darf keine offenen SonarCloud-Issues haben (#37).

Das SonarCloud-Quality-Gate ist im Free-Plan nicht auf "0 neue Issues"
konfigurierbar — dieses Skript erzwingt die Merge-Latte deshalb im eigenen CI:
nach dem Scan wird die Analyse (CE-Task aus .scannerwork/report-task.txt)
abgewartet, dann die offene Issue-Liste des PRs abgefragt. Jedes offene Issue
macht den Job rot.

Aufruf: python scripts/check_sonar_pr_issues.py <pr-nummer>
Env:    SONAR_TOKEN — geht in den Authorization-Header, nie in die URL (R-SEC-01).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPORT_TASK_FILE = Path(".scannerwork/report-task.txt")
POLL_INTERVAL_SECONDS = 5.0
CE_TASK_TIMEOUT_SECONDS = 300.0
_TERMINAL_CE_STATUS = frozenset({"SUCCESS", "FAILED", "CANCELED"})


def _read_report_task(path: Path) -> dict[str, str]:
    """Parst die key=value-Zeilen, die der Sonar-Scanner nach dem Scan ablegt."""
    if not path.is_file():
        raise SystemExit(f"FEHLER: {path} fehlt — lief der SonarCloud-Scan? (fail closed)")
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            entries[key.strip()] = value.strip()
    return entries


def _api_get(url: str, token: str) -> dict[str, Any]:
    """GET gegen die Sonar-API; Token ausschließlich im Authorization-Header."""
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    return payload


def _wait_for_analysis(ce_task_url: str, token: str) -> None:
    """Pollt den CE-Task bis SUCCESS; FAILED/CANCELED/Timeout ⇒ Abbruch (fail closed)."""
    deadline = time.monotonic() + CE_TASK_TIMEOUT_SECONDS
    while True:
        status = str(_api_get(ce_task_url, token).get("task", {}).get("status", ""))
        if status == "SUCCESS":
            return
        if status in _TERMINAL_CE_STATUS:
            raise SystemExit(f"FEHLER: Sonar-Analyse endete mit Status {status}")
        if time.monotonic() > deadline:
            raise SystemExit("FEHLER: Timeout beim Warten auf die Sonar-Analyse (fail closed)")
        time.sleep(POLL_INTERVAL_SECONDS)


def _fetch_open_issues(
    server_url: str, project_key: str, pr_number: str, token: str
) -> tuple[int, list[dict[str, Any]]]:
    """Fragt offene Issues des PRs ab; liefert (total, erste Ergebnisseite)."""
    query = urllib.parse.urlencode(
        {
            "componentKeys": project_key,
            "pullRequest": pr_number,
            "resolved": "false",
            "ps": "100",
        }
    )
    payload = _api_get(f"{server_url}/api/issues/search?{query}", token)
    issues: list[dict[str, Any]] = list(payload.get("issues", []))
    return int(payload.get("total", 0)), issues


def _format_issue(issue: dict[str, Any]) -> str:
    component = str(issue.get("component", "")).partition(":")[2]
    line = issue.get("line", "?")
    return (
        f"  {issue.get('severity', '?'):8} {issue.get('rule', '?'):20} "
        f"{component}:{line} — {issue.get('message', '')}"
    )


def main(argv: list[str]) -> int:
    """Gate-Einstieg: 0 offene Issues ⇒ grün, sonst rot mit Issue-Liste."""
    if len(argv) != 2:
        print("Aufruf: check_sonar_pr_issues.py <pr-nummer>", file=sys.stderr)
        return 2
    pr_number = argv[1]
    token = os.environ.get("SONAR_TOKEN", "")

    report = _read_report_task(REPORT_TASK_FILE)
    server_url = report.get("serverUrl", "https://sonarcloud.io")
    project_key = report.get("projectKey", "")
    ce_task_url = report.get("ceTaskUrl", "")
    if not project_key or not ce_task_url:
        raise SystemExit("FEHLER: report-task.txt ohne projectKey/ceTaskUrl (fail closed)")

    _wait_for_analysis(ce_task_url, token)
    total, issues = _fetch_open_issues(server_url, project_key, pr_number, token)

    if total > 0:
        print(f"ROT: {total} offene(s) SonarCloud-Issue(s) in PR #{pr_number} — Merge-Latte")
        print("ist 'alles gruen + 0 neue Issues'. Issues:")
        for issue in issues:
            print(_format_issue(issue))
        return 1

    print(f"OK: 0 offene SonarCloud-Issues in PR #{pr_number}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
