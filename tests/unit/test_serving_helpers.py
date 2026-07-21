"""Unit (#43): reine Serving-Helfer — Highlight-Match, Locator-Mapping, limit-Clamp."""

from __future__ import annotations

from wortlaut.serving.app import _find_match, _locator
from wortlaut.serving.schemas import MatchInfo
from wortlaut.store.read import clamp_limit


def test_find_match_first_word() -> None:
    match = _find_match("Die Digitalisierung ist wichtig", "Digitalisierung Bildung")
    assert match == MatchInfo(start=4, end=19)


def test_find_match_none_when_absent() -> None:
    assert _find_match("Hallo Welt", "Xylophon") is None


def test_find_match_none_on_empty_query() -> None:
    assert _find_match("Hallo Welt", "  ") is None


def test_clamp_limit() -> None:
    assert clamp_limit(0) == 20  # Default
    assert clamp_limit(-5) == 20
    assert clamp_limit(50) == 50
    assert clamp_limit(500) == 100  # Deckel, kein silent cap (total bleibt echt)


def test_locator_maps_present_keys() -> None:
    loc = _locator({"protokoll": "20/88", "sitzung": "88", "tagesordnungspunkt": "3", "x": 1})
    assert loc.protokoll == "20/88"
    assert loc.sitzung == "88"
    assert loc.tagesordnungspunkt == "3"


def test_locator_missing_keys_are_none() -> None:
    loc = _locator({})
    assert loc.protokoll is None
    assert loc.sitzung is None
    assert loc.tagesordnungspunkt is None
