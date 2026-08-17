"""Gepinnte TSA-Profile: die einzigen Trust-Anker, gegen die ein Token verifiziert wird.

Profile sind **append-only**. Rotiert eine TSA ihr Signatur-Zertifikat, wird ein
**neues** Profil angelegt (z.B. ``freetsa-2027``) — ein bestehendes wird **nie**
umgebogen, sonst werden alle bereits gestempelten Tokens dieses Profils
unverifizierbar. ``source_timestamp.tsa_name`` pinnt, welches Profil ein Token
prüft (Spec 0076 §8).

Wichtig (Spec 0076 §0a-🔴): ein Profil trägt Root **UND** Signatur-Leaf. Reines
Root-Pinning wäre wirkungslos (die Bibliothek würfelt Token-Zertifikate und
Roots in denselben Trust-Bag); erst der Leaf-Pin bindet den **öffentlichen
Schlüssel** einer TSA. ``common_name(...)`` wird bewusst nicht benutzt.

Nur stdlib + cryptography; importiert keinen anderen wortlaut-Layer (R-ARCH-02,
AC19).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib.resources import files

import cryptography.x509 as x509

# ── Registry: exakt zwei Profile (Spec 0076 §11) ────────────────────────
# Einträge sind Konstanten, keine ENV-Fremdeingabe (§0c/§4.6): der Trust-Anker
# darf nicht zur Laufzeit injiziert werden. Ein neuer TSA kostet einen
# Code-Change + Review — das ist hier die gewünschte Eigenschaft.


@dataclass(frozen=True)
class TsaProfile:
    """Ein TSA-Anbieter samt seinen GEPINNTEN Trust-Ankern (Root UND Leaf, §0a-🔴)."""

    name: str  # 'freetsa' | 'sigstore' — landet als tsa_name in der DB
    url: str  # https-Endpunkt
    root_file: str  # Dateiname in wortlaut/timestamp/trust/
    leaf_file: str  # Signatur-Zertifikat der TSA — bindet den öffentlichen Schlüssel


TSA_PROFILES: dict[str, TsaProfile] = {
    "freetsa": TsaProfile(
        name="freetsa",
        url="https://freetsa.org/tsr",
        root_file="freetsa-root.pem",
        leaf_file="freetsa-leaf.pem",
    ),
    "sigstore": TsaProfile(
        name="sigstore",
        url="https://timestamp.sigstore.dev/api/v1/timestamp",
        root_file="sigstore-root.pem",
        leaf_file="sigstore-leaf.pem",
    ),
}


def load_profile(name: str) -> TsaProfile:
    """Profil aus der Registry; KeyError-frei — wirft ``ValueError`` bei unbekanntem Namen."""
    try:
        return TSA_PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unbekanntes TSA-Profil: {name!r}") from exc


@cache
def load_certificate(file_name: str) -> x509.Certificate:
    """Lädt ein gepinntes PEM aus dem Paket (importlib.resources), gecached.

    Cache-Schlüssel ist der Dateiname (``str``, hashbar) — nicht das
    Profil-Objekt, das ``lru_cache`` nicht als Schlüssel nehmen würde.
    """
    pem = files("wortlaut.timestamp").joinpath("trust").joinpath(file_name).read_bytes()
    return x509.load_pem_x509_certificate(pem)
