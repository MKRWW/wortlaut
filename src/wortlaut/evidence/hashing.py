"""Content-Hash: SHA-256 über Rohbytes (Beweisketten-Anker).

Rein: keine wortlaut-Imports, keine I/O.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


def content_hash(raw: bytes) -> str:
    """SHA-256 über die Rohbytes, 64-stelliger lowercase-Hex."""
    return hashlib.sha256(raw).hexdigest()


def content_hash_stream(chunks: Iterable[bytes]) -> str:
    """Wie content_hash, aber inkrementell über Chunk-Iterator (große Quellen)."""
    h = hashlib.sha256()
    for chunk in chunks:
        h.update(chunk)
    return h.hexdigest()


def span_hash(verbatim_text: str) -> str:
    """SHA-256 über den ``verbatim_text`` (UTF-8) — Beweisanker je Span (#42).

    Wiederverwendung von :func:`content_hash`; 64-stelliger lowercase-Hex.
    """
    return content_hash(verbatim_text.encode("utf-8"))
