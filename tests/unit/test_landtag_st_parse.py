"""Unit: Landtag-Sachsen-Anhalt-Parser (Spec 0145, AC7–AC9).

Rein: gegen die committete Fixture ``tests/fixtures/landtag_st/protokoll.pdf``
(Generator: ``_make_protokoll.py``), KEIN Live-Call (R-TEST-03).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wortlaut.ingest.landtag_st_parse import classify, parse_header_st, segment_speeches_st
from wortlaut.ingest.protokoll_parse import extract_text

_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "landtag_st" / "protokoll.pdf"


def _normalized() -> str:
    return extract_text(_FIXTURE.read_bytes())


# ── AC7: Kopf ─────────────────────────────────────────────────────────────


def test_header() -> None:  # AC7
    spoken_at, locator = parse_header_st(_normalized())
    assert spoken_at == "2026-06-26"
    assert locator["protokoll"] == "8/118"
    assert locator["sitzung"] == "118"


# ── AC8: umbrochene Rollenangabe ──────────────────────────────────────────


def test_wrapped_role_is_own_speaker() -> None:  # AC8
    speeches = segment_speeches_st(_normalized())
    minister = next(s for s in speeches if s.name == "Dr. Anna Beispielhaft")
    assert minister.role == "Ministerin für Inneres und Sport"
    assert minister.party is None
    assert minister.verbatim_text == "Die Landesregierung sieht das anders."
    erika = next(s for s in speeches if s.name == "Erika Musterfrau")
    assert "Die Landesregierung sieht das anders." not in erika.verbatim_text


# ── AC9: Berichterstatter und Fraktion ────────────────────────────────────


def test_rapporteur_and_party() -> None:  # AC9
    speeches = segment_speeches_st(_normalized())
    rapporteur = next(s for s in speeches if s.name == "Max Mustermann")
    assert rapporteur.party is None
    assert rapporteur.role is None
    erika = next(s for s in speeches if s.name == "Erika Musterfrau")
    assert erika.party == "AfD"
    assert erika.role is None


# ── AC9: Präsidium und Zwischenruf sind keine Segmente ────────────────────


def test_presidium_and_heckle_not_speakers() -> None:  # AC9
    normalized = _normalized()
    speeches = segment_speeches_st(normalized)
    assert len(speeches) == 4
    assert all("Präsident" not in speech.name for speech in speeches)
    assert all("Beifall" not in speech.name for speech in speeches)
    erika = next(s for s in speeches if s.name == "Erika Musterfrau")
    assert "(Beifall bei der AfD)" in erika.verbatim_text


# ── AC9: Offset-Invariante ────────────────────────────────────────────────


def test_offsets() -> None:  # AC9 — Kern-Invariante
    normalized = _normalized()
    speeches = segment_speeches_st(normalized)
    assert speeches
    for speech in speeches:
        assert normalized[speech.text_start : speech.text_end] == speech.verbatim_text


# ── classify ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("paren", "expected_party", "expected_role"),
    [
        pytest.param("AfD", "AfD", None, id="fraktion"),
        pytest.param("Berichterstatterin", None, None, id="rapporteur"),
        pytest.param(
            "Staats- und Kulturminister",
            None,
            "Staats- und Kulturminister",
            id="minister",
        ),
        pytest.param("Ministerpräsident", None, "Ministerpräsident", id="minister-praesident"),
        pytest.param("Staatssekretärin", None, "Staatssekretärin", id="staatssekretaerin"),
        pytest.param("Ministerin für\nJustiz", None, "Ministerin für Justiz", id="wrapped"),
    ],
)
def test_classify(paren: str, expected_party: str | None, expected_role: str | None) -> None:
    party, role = classify(paren)
    assert party == expected_party
    assert role == expected_role


# ── ungültiges Datum ──────────────────────────────────────────────────────


def test_invalid_date_is_empty() -> None:
    spoken_at, _ = parse_header_st("118. Sitzung, Freitag, 31.02.2026")
    assert spoken_at == ""
