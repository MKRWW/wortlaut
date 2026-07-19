"""Unit: SHA-256 Content-Hash (AC1–AC4, AC7).

Rein: kein Container, keine I/O. Testet wortlaut.evidence.hashing.
"""

from wortlaut.evidence.hashing import content_hash, content_hash_stream


def test_known_vector() -> None:
    # AC1: b"" -> bekannter SHA-256-Hash
    assert content_hash(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_deterministic() -> None:
    # AC2: gleiche Bytes -> gleicher Hash
    data = b"wortlaut ist deterministisch"
    assert content_hash(data) == content_hash(data)


def test_rawbytes_not_text() -> None:
    # AC3: Rohbytes vs. geparster Text -> verschiedene Hashes
    raw = b"Hallo"
    parsed = raw.decode("utf-8").encode("utf-16-le")
    assert content_hash(raw) != content_hash(parsed)


def test_hex64_lowercase() -> None:
    # AC4: Ausgabe ist genau 64 Zeichen lowercase hex
    cases: list[bytes] = [
        b"",
        b"test",
        bytes(range(256)),
    ]
    for raw in cases:
        h = content_hash(raw)
        assert len(h) == 64
        assert h == h.lower()
        assert all(c in "0123456789abcdef" for c in h)


def test_stream_equivalence() -> None:
    # AC7: content_hash(b) == content_hash_stream(chunks)
    data = b"Wortlaut-Projekt-Inhalt-mit-mehreren-Chunks-zum-Testen"

    # Einzelner Chunk
    assert content_hash(data) == content_hash_stream((data,))

    # Zwei Chunks
    mid = len(data) // 2
    assert content_hash(data) == content_hash_stream((data[:mid], data[mid:]))

    # Drei Chunks
    third = len(data) // 3
    assert content_hash(data) == content_hash_stream(
        (data[:third], data[third : third * 2], data[third * 2 :])
    )

    # Viele kleine Chunks (je 1 Byte)
    assert content_hash(data) == content_hash_stream(data[i : i + 1] for i in range(len(data)))
