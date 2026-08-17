"""Strukturierter TSA-Fehler (Spec 0076 §3.2).

Nur stdlib. Trägt TSA, Grund und optionalen Statuscode bis in Log und Summary —
statt eines opaken String. Kein ``transient``-Flag: eine TSA wird in einem Lauf
genau EINMAL angesprochen (Spec 0076 §4.7 — der Pass ist der Retry), es gibt
also kein Transienz-/Retry-Konzept.
"""

from __future__ import annotations


class TimestampError(Exception):
    """Strukturierter TSA-Fehler — trägt den Grund bis in Log und Summary."""

    def __init__(
        self,
        tsa_name: str,
        reason: str,  # 'http_status'|'timeout'|'transport'|'content_type'
        # |'malformed'|'not_granted'|'mismatch'|'untrusted'|'oversize'
        *,
        status_code: int | None = None,
    ) -> None:
        self.tsa_name = tsa_name
        self.reason = reason
        self.status_code = status_code
        super().__init__(f"{tsa_name}: {reason}")

    def label(self) -> str:
        """Kompakter Aggregations-Schlüssel, z.B. 'freetsa:http_status_503'."""
        if self.status_code is None:
            return f"{self.tsa_name}:{self.reason}"
        return f"{self.tsa_name}:{self.reason}_{self.status_code}"

    def __str__(self) -> str:
        text = f"{self.tsa_name}: {self.reason}"
        if self.status_code is not None:
            text += f" {self.status_code}"
        return text
