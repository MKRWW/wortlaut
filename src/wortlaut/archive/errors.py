"""Strukturierter Archiv-Fehler (Spec 0073 §11).

Nur stdlib. Trägt Dienst, Grund, optionalen Statuscode und Transienz-Flag
bis in Log, Summary und IngestOutcome — statt eines opaken String.
"""

from __future__ import annotations


class ArchiveError(Exception):
    """Strukturierter Archiv-Fehler — trägt den Grund bis in Log und Summary."""

    def __init__(
        self,
        service: str,  # 'wayback' | 'archive_today'
        reason: str,  # 'http_status'|'timeout'|'transport'|'no_snapshot_url'
        # |'invalid_snapshot_url'|'disabled'|'unexpected'
        *,
        status_code: int | None = None,
        transient: bool = False,
    ) -> None:
        self.service = service
        self.reason = reason
        self.status_code = status_code
        self.transient = transient
        super().__init__(f"{service}: {reason}")

    def label(self) -> str:
        """Kompakter Aggregations-Schlüssel, z.B. 'wayback:http_status_404'."""
        if self.status_code is None:
            return f"{self.service}:{self.reason}"
        return f"{self.service}:{self.reason}_{self.status_code}"

    def __str__(self) -> str:
        text = f"{self.service}: {self.reason}"
        if self.status_code is not None:
            text += f" {self.status_code}"
        if self.transient:
            text += " (transient)"
        return text
