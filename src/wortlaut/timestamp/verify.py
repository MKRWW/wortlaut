"""RFC-3161-Token-Prüfung: bindet ein gespeichertes Token an einen content_hash.

Statusmatrix (Spec 0076 §4.4) in fester Reihenfolge — die Ursache bleibt erhalten
statt in ein opakes „ungültig" zu kollabieren (Lehre aus #73):

  1. decode_timestamp_response        Exception/kein DER  → ``malformed``
  2. resp.status == GRANTED           z.B. rejection       → ``malformed``
  3. imprint.hash_algorithm == SHA-256 SHA-1/512-Token     → ``mismatch``
  4. imprint.message == content_hash  bindet an fremde Bytes→ ``mismatch``
  5. verifier.verify(...)             Leaf-Pin/Kette/EKU   → ``untrusted``
  alles bestanden                                                   → ``ok``

Schritt 3+4 laufen VOR Schritt 5, weil die Bibliothek beide Fälle in dieselbe
``VerificationError`` wirft — ohne die Vorab-Trennung wäre „Token gehört zu
einer anderen Quelle" von „TSA ist gefälscht" nicht unterscheidbar.

**Wirft nie** — jeder Fehlerpfad endet in einem :class:`TimestampVerdict`.
Unbekanntes ``tsa_name`` (kein Anker) ⇒ ``untrusted``, nie ``ok``.
Nur stdlib + cryptography + rfc3161_client; kein anderer wortlaut-Layer (AC19).
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import cryptography.x509 as x509
from rfc3161_client import (
    VerificationError,
    VerifierBuilder,
    decode_timestamp_response,
)
from rfc3161_client.tsp import PKIStatus

from wortlaut.timestamp.profiles import load_certificate, load_profile

SHA256_OID = x509.ObjectIdentifier("2.16.840.1.101.3.4.2.1")


@dataclass(frozen=True)
class TimestampVerdict:
    """Ergebnis der Token-Prüfung — trennt 'bindet nicht' von 'nicht vertrauenswürdig'."""

    status: Literal["ok", "mismatch", "untrusted", "malformed"]
    tsa_name: str
    gen_time: datetime | None  # NUR aus dem Token gelesen, nie aus der DB
    detail: str | None


def verify_token(token_der: bytes, *, content_hash: str, tsa_name: str) -> TimestampVerdict:
    """Prüft ein gespeichertes Token gegen den content_hash. Wirft NIE — meldet immer."""
    # Unbekannter Anker: kein Profil ⇒ kein Vertrauen, nie ``ok``.
    try:
        profile = load_profile(tsa_name)
    except ValueError as exc:
        return TimestampVerdict("untrusted", tsa_name, None, str(exc))

    # Schritt 1: DER-Parsing. Jede Exception (kein DER, abgeschnitten) ⇒ malformed.
    try:
        resp = decode_timestamp_response(token_der)
    except Exception as exc:
        return TimestampVerdict("malformed", tsa_name, None, str(exc))

    # Schritt 2: Status muss GRANTED sein (nicht rejection/waiting/…).
    if resp.status != PKIStatus.GRANTED:
        return TimestampVerdict("malformed", tsa_name, None, f"PKIStatus {resp.status} != GRANTED")

    # Schritt 3: der gestempelte Algorithmus muss SHA-256 sein — sonst bindet das
    # Token an eine andere Hash-Länge, nicht an unseren content_hash.
    if resp.tst_info.message_imprint.hash_algorithm != SHA256_OID:
        oid = resp.tst_info.message_imprint.hash_algorithm
        return TimestampVerdict(
            "mismatch", tsa_name, None, f"imprint algorithm {oid.dotted_string} != sha256"
        )

    # Schritt 4: der Imprint muss byte-gleich mit content_hash sein. Ein
    # content_hash, der kein 64-stelliges Hex ist, bindet an nichts ⇒ mismatch.
    try:
        expected_digest = bytes.fromhex(content_hash)
    except ValueError:
        return TimestampVerdict("mismatch", tsa_name, None, "content_hash ist kein gültiges Hex")
    if not hmac.compare_digest(resp.tst_info.message_imprint.message, expected_digest):
        return TimestampVerdict(
            "mismatch", tsa_name, None, "imprint bindet an andere Bytes als content_hash"
        )

    # Schritt 5: Kette + gepinntes Leaf + EKU + Signatur gegen die pinned Ankern.
    # DER WERTE des Verifiers: Root UND Leaf (§0a-🔴). common_name wird NICHT
    # gesetzt — es würde gegen den vollständigen RFC4514-DN vergleichen (fälschbar,
    # rotationsbrüchig) und ist ohne Leaf-Pin wertlos.
    # Auch ein FEHLENDER/kaputter Trust-Anker endet in einem Verdict, nie in einer
    # Exception: diese Funktion darf nie werfen (sonst liefert /verify 500 statt
    # eines Befundes). Kein ladbarer Anker ⇒ kein Vertrauen ⇒ ``untrusted``.
    try:
        verifier = (
            VerifierBuilder()
            .add_root_certificate(load_certificate(profile.root_file))
            .tsa_certificate(load_certificate(profile.leaf_file))
            .build()
        )
        verifier.verify(resp, expected_digest)
    except VerificationError as exc:
        return TimestampVerdict("untrusted", tsa_name, None, str(exc))
    except Exception as exc:  # Trust-Anker nicht ladbar (fehlende/kaputte PEM)
        return TimestampVerdict("untrusted", tsa_name, None, f"Trust-Anker nicht ladbar: {exc}")

    # Alles bestanden: ok. gen_time kommt NUR aus dem Token (nie aus der DB).
    return TimestampVerdict("ok", tsa_name, resp.tst_info.gen_time, None)
