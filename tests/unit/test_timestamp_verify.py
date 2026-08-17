"""Unit (Spec 0076): verify_token — RFC-3161-Token gegen content_hash (AC1–AC5).

Rein: echte Fixture-Tokens aus ``tests/fixtures/tsa/``, gepinnte Trust-Anker aus
dem Paket; keine Netz-Calls, kein Token-Erzeugen. Der erwartete Hash ist
``sha256(message.bin)`` = ``fca714d2…9a99``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from wortlaut.timestamp.verify import verify_token

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "tsa"

# sha256(message.bin) — byte-identisch mit dem Imprint der Fixture-Tokens (Spec 0076 §0a).
_MESSAGE_HASH = "fca714d25fbd7eef88f5e936023610e6e115814a702797cb97b6f22a9a059a99"


def _token(name: str) -> bytes:
    return (_FIXTURES / f"{name}.tsr").read_bytes()


def test_fixture_token_verifies() -> None:  # AC1
    """AC1: freetsa.tsr gegen seinen eigenen Hash → ok, tsa_name + tz-aware gen_time."""
    verdict = verify_token(_token("freetsa"), content_hash=_MESSAGE_HASH, tsa_name="freetsa")
    assert verdict.status == "ok"
    assert verdict.tsa_name == "freetsa"
    assert isinstance(verdict.gen_time, datetime)
    assert verdict.gen_time.tzinfo is not None
    assert verdict.detail is None


def test_wrong_content_hash_is_mismatch() -> None:  # AC2
    """AC2: gültiges Token, aber ein Nibble des Hashes geflippt → mismatch (nicht ok/untrusted)."""
    flipped = _MESSAGE_HASH[:1] + ("0" if _MESSAGE_HASH[0] != "0" else "1") + _MESSAGE_HASH[2:]
    assert len(flipped) == 64
    assert flipped != _MESSAGE_HASH
    verdict = verify_token(_token("freetsa"), content_hash=flipped, tsa_name="freetsa")
    assert verdict.status == "mismatch"
    assert verdict.tsa_name == "freetsa"
    assert verdict.gen_time is None


def test_wrong_root_is_untrusted() -> None:  # AC3
    """AC3 (🔴 Kern): freeTSA-Token (bringt seine eigene Kette inkl. Root mit) gegen das
    FALSCHE gepinnte Profil (sigstore) → untrusted, nicht ok. Und umgekehrt.

    Beide Richtungen sind Pflicht: ohne Leaf-Pin würde nur die Richtung
    sigstore→freeTSA ohnehin scheitern (sigstore bettet keinen Root ein) — allein
    geprüft gäbe sie falsche Sicherheit. Die Richtung freeTSA→sigstore deckt den
    Bug aus §0a-🔴 auf.
    """
    # freeTSA-Token gegen sigstore-Profil
    verdict = verify_token(_token("freetsa"), content_hash=_MESSAGE_HASH, tsa_name="sigstore")
    assert verdict.status == "untrusted"
    assert verdict.tsa_name == "sigstore"
    assert verdict.gen_time is None

    # sigstore-Token gegen freeTSA-Profil
    verdict = verify_token(_token("sigstore"), content_hash=_MESSAGE_HASH, tsa_name="freetsa")
    assert verdict.status == "untrusted"
    assert verdict.tsa_name == "freetsa"


def test_garbage_and_truncated_are_malformed() -> None:  # AC4
    """AC4: nicht-DR bzw. abgeschnittenes Token → malformed, keine Exception entkommt."""
    garbage = verify_token(b"nicht-der", content_hash=_MESSAGE_HASH, tsa_name="freetsa")
    assert garbage.status == "malformed"
    assert garbage.tsa_name == "freetsa"
    assert garbage.gen_time is None

    full = _token("freetsa")
    for cut in (1, 10, len(full) // 2, len(full) - 1):
        truncated = verify_token(full[:cut], content_hash=_MESSAGE_HASH, tsa_name="freetsa")
        assert truncated.status == "malformed", f"abgeschnitten bei {cut} sollte malformed sein"


def test_unknown_tsa_is_untrusted() -> None:  # AC5
    """AC5: gültiges Token, aber unbekanntes tsa_name (kein Anker) → untrusted, nie ok."""
    verdict = verify_token(_token("freetsa"), content_hash=_MESSAGE_HASH, tsa_name="gibtsnicht")
    assert verdict.status == "untrusted"
    assert verdict.tsa_name == "gibtsnicht"
    assert verdict.gen_time is None


def test_sha256_imprint_matches_message_hash() -> None:
    """AC1 (Grundlage): der Fixture-Imprint ist byte-identisch mit sha256(message.bin)."""
    message = (_FIXTURES / "message.bin").read_bytes()
    assert hashlib.sha256(message).hexdigest() == _MESSAGE_HASH
