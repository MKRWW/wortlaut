"""Unit (Spec 0098): Konformitäts-Testkit."""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from wortlaut.ingest.adapter import AdapterError, RawSource, SourceRef, SpanDraft
from wortlaut.ingest.conformance import (
    TRUST_LEVELS,
    ConformanceSamples,
    assert_conformant,
    check_adapter,
)
from wortlaut.store.models import _TRUST_LEVEL

_TEXT = "Präsidentin: Die Sitzung ist eröffnet. Dr. Max Mustermann (AfD): Ich beginne."
_SPAN = "Ich beginne."
_URL = "https://example.org/q1.txt"
_FAILING = SourceRef("https://fremd.example/x", "rede", {})
_SINCE = datetime(2024, 1, 1)


class _GoodAdapter:
    name = "gut"
    version = "1.0.0"
    trust_level = "secondary"
    parliament = "bundestag"
    mandate_role = "MdB"
    rights_basis: str | None = "lizenz"

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        return [SourceRef(_URL, "rede", {})]

    async def fetch(self, ref: SourceRef) -> RawSource:
        if "fremd.example" in ref.origin_url:
            raise AdapterError("fremd")
        return RawSource(ref.origin_url, "rede", _TEXT.encode("utf-8"), "text/plain", _SINCE)

    def normalize(self, raw: RawSource) -> str:
        return raw.raw_bytes.decode("utf-8")

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        start = normalized.index(_SPAN)
        return [
            SpanDraft(
                verbatim_text=_SPAN,
                text_start=start,
                text_end=start + len(_SPAN),
                speaker_hint={"name": "Dr. Max Mustermann", "party": "AfD"},
                spoken_at="2024-07-05",
                locator={"sitzung": "88"},
                permalink=raw.origin_url,
            )
        ]

    async def aclose(self) -> None:
        return None


def _samples(
    raw: RawSource | None = None,
    failing_ref: SourceRef | None = None,
    min_spans: int = 1,
) -> ConformanceSamples:
    return ConformanceSamples(
        since=_SINCE, failing_ref=failing_ref or _FAILING, raw=raw, min_spans=min_spans
    )


async def test_good_adapter_passes() -> None:
    assert await check_adapter(_GoodAdapter(), _samples()) == []


async def test_assert_conformant_good_does_not_raise() -> None:
    await assert_conformant(_GoodAdapter(), _samples())


def test_trust_levels_match_db_enum() -> None:
    assert TRUST_LEVELS == tuple(_TRUST_LEVEL.enums)


def test_only_contract_imports() -> None:
    path = Path(__file__).resolve().parents[2] / "src" / "wortlaut" / "ingest" / "conformance.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed = {"wortlaut.ingest.adapter", "wortlaut.ingest.rights"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("wortlaut"):
            assert node.module in allowed


class _BadTrustLevel(_GoodAdapter):
    """trust_level ist kein zulässiger Enum-Wert."""

    trust_level = "hoch"


class _BadRightsBasis(_GoodAdapter):
    """rights_basis ist kein zulässiger Wert."""

    rights_basis = "gemeinfrei"


class _NoRightsAnywhere(_GoodAdapter):
    """rights_basis ist nirgends gesetzt (None)."""

    rights_basis = None


class _DiscoverRaises(_GoodAdapter):
    """discover wirft stattdessen eine RuntimeError."""

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        raise RuntimeError("x")


class _DiscoverReturnsString(_GoodAdapter):
    """discover gibt einen String statt eine Sequence zurück."""

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        return cast(Sequence[SourceRef], "keine-liste")


class _FetchRaises(_GoodAdapter):
    """fetch wirft für jede Ref."""

    async def fetch(self, ref: SourceRef) -> RawSource:
        raise RuntimeError("x")


class _EmptyBytes(_GoodAdapter):
    """raw_bytes ist leer."""

    async def fetch(self, ref: SourceRef) -> RawSource:
        if "fremd.example" in ref.origin_url:
            raise AdapterError("fremd")
        return RawSource(ref.origin_url, "rede", b"", "text/plain", _SINCE)


class _BadMime(_GoodAdapter):
    """mime_type ist kein zulässiger MIME-Typ."""

    async def fetch(self, ref: SourceRef) -> RawSource:
        if "fremd.example" in ref.origin_url:
            raise AdapterError("fremd")
        return RawSource(ref.origin_url, "rede", _TEXT.encode("utf-8"), "pdf", _SINCE)


class _PdfMimeWithoutMagic(_GoodAdapter):
    """mime_type ist application/pdf, aber die Bytes sind weiter reiner Text."""

    async def fetch(self, ref: SourceRef) -> RawSource:
        if "fremd.example" in ref.origin_url:
            raise AdapterError("fremd")
        return RawSource(ref.origin_url, "rede", _TEXT.encode("utf-8"), "application/pdf", _SINCE)


class _ValueErrorOnFailingRef(_GoodAdapter):
    """fetch wirft für fremd.example ein ValueError statt AdapterError."""

    async def fetch(self, ref: SourceRef) -> RawSource:
        if "fremd.example" in ref.origin_url:
            raise ValueError("x")
        return RawSource(ref.origin_url, "rede", _TEXT.encode("utf-8"), "text/plain", _SINCE)


class _NoErrorOnFailingRef(_GoodAdapter):
    """fetch wirft für fremd.example keinen Fehler, sondern gibt normal RawSource zurück."""

    async def fetch(self, ref: SourceRef) -> RawSource:
        return RawSource(ref.origin_url, "rede", _TEXT.encode("utf-8"), "text/plain", _SINCE)


class _RandomNormalize(_GoodAdapter):
    """normalize ist nicht deterministisch: zählt Aufrufe und hängt die Zahl an den Text an."""

    def __init__(self) -> None:
        self._calls = 0

    def normalize(self, raw: RawSource) -> str:
        self._calls += 1
        return raw.raw_bytes.decode("utf-8") + str(self._calls)


class _NormalizeRaises(_GoodAdapter):
    """normalize wirft eine RuntimeError."""

    def normalize(self, raw: RawSource) -> str:
        raise RuntimeError("x")


class _ParseRaises(_GoodAdapter):
    """parse wirft eine RuntimeError."""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        raise RuntimeError("x")


class _NoSpans(_GoodAdapter):
    """parse gibt eine leere Liste zurück."""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return []


class _ShiftedOffsets(_GoodAdapter):
    """text_start und text_end um 1 nach links: im Bereich, aber der Slice stimmt nicht."""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return [
            dataclasses.replace(s, text_start=s.text_start - 1, text_end=s.text_end - 1)
            for s in super().parse(raw, normalized)
        ]


class _WrongVerbatim(_GoodAdapter):
    """Offsets korrekt, aber verbatim_text weicht vom Slice ab (R-DATA-06)."""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return [
            dataclasses.replace(s, verbatim_text="Ich beginnE.")
            for s in super().parse(raw, normalized)
        ]


class _NoSpeakerName(_GoodAdapter):
    """Der speaker_hint enthält kein name-Feld."""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return [
            dataclasses.replace(s, speaker_hint={"party": "AfD"})
            for s in super().parse(raw, normalized)
        ]


class _EmptyRole(_GoodAdapter):
    """Der speaker_hint enthält "role" als leeren String (AC12)."""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return [
            dataclasses.replace(
                s,
                speaker_hint={"name": "Dr. Max Mustermann", "party": "AfD", "role": ""},
            )
            for s in super().parse(raw, normalized)
        ]


class _GermanDate(_GoodAdapter):
    """spoken_at ist ein Datum im deutschen Format."""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return [
            dataclasses.replace(s, spoken_at="05.07.2024") for s in super().parse(raw, normalized)
        ]


class _SetLocator(_GoodAdapter):
    """locator enthält ein Set, das nicht JSON-fähig ist."""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return [
            dataclasses.replace(s, locator=cast(dict[str, object], {"x": {1, 2}}))
            for s in super().parse(raw, normalized)
        ]


class _EmptyPermalink(_GoodAdapter):
    """permalink ist leer."""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return [dataclasses.replace(s, permalink="") for s in super().parse(raw, normalized)]


class _AcloseRaisesSecondTime(_GoodAdapter):
    """Der zweite aclose-Aufruf wirft eine RuntimeError."""

    def __init__(self) -> None:
        self._calls = 0

    async def aclose(self) -> None:
        self._calls += 1
        if self._calls > 1:
            raise RuntimeError("x")


class _WithoutAclose:
    """Adapter ohne aclose-Methode."""

    name = "gut"
    version = "1.0.0"
    trust_level = "secondary"
    parliament = "bundestag"
    mandate_role = "MdB"
    rights_basis: str | None = "lizenz"
    discover = _GoodAdapter.discover
    fetch = _GoodAdapter.fetch
    normalize = _GoodAdapter.normalize
    parse = _GoodAdapter.parse


class _BadParliamentSpaces(_GoodAdapter):
    """parliament enthält Leerzeichen."""

    parliament = "Landtag Brandenburg"


class _EmptyParliament(_GoodAdapter):
    """parliament ist leer."""

    parliament = ""


class _EmptyMandateRole(_GoodAdapter):
    """mandate_role ist leer."""

    mandate_role = ""


class _WithoutParliamentAttr:
    """Adapter ohne parliament-Attribut."""

    name = "gut"
    version = "1.0.0"
    trust_level = "secondary"
    mandate_role = "MdB"
    rights_basis: str | None = "lizenz"
    discover = _GoodAdapter.discover
    fetch = _GoodAdapter.fetch
    normalize = _GoodAdapter.normalize
    parse = _GoodAdapter.parse
    aclose = _GoodAdapter.aclose


_BROKEN: list[tuple[type[object], str]] = [
    (_BadTrustLevel, "identity"),
    (_BadRightsBasis, "rights_basis"),
    (_NoRightsAnywhere, "ref_rights"),
    (_DiscoverRaises, "discover"),
    (_DiscoverReturnsString, "discover"),
    (_FetchRaises, "fetch"),
    (_EmptyBytes, "fetch_raw"),
    (_BadMime, "fetch_mime"),
    (_PdfMimeWithoutMagic, "fetch_mime"),
    (_ValueErrorOnFailingRef, "fetch_error"),
    (_NoErrorOnFailingRef, "fetch_error"),
    (_RandomNormalize, "normalize_deterministic"),
    (_NormalizeRaises, "normalize"),
    (_ParseRaises, "parse"),
    (_NoSpans, "parse_min_spans"),
    (_ShiftedOffsets, "span_offsets"),
    (_WrongVerbatim, "span_offsets"),
    (_NoSpeakerName, "span_speaker"),
    (_EmptyRole, "span_speaker"),
    (_GermanDate, "span_date"),
    (_SetLocator, "span_locator"),
    (_EmptyPermalink, "span_permalink"),
    (_AcloseRaisesSecondTime, "aclose"),
    (_WithoutAclose, "protocol"),
    (_BadParliamentSpaces, "parliament"),
    (_EmptyParliament, "parliament"),
    (_EmptyMandateRole, "parliament"),
    (_WithoutParliamentAttr, "protocol"),
]


@pytest.mark.parametrize(("adapter_cls", "check"), _BROKEN)
async def test_broken_adapter_is_reported(adapter_cls: type[object], check: str) -> None:
    """AC2/AC3: jede Verletzung meldet ihre Prüf-ID."""
    violations = await check_adapter(adapter_cls(), _samples())
    assert check in {v.check for v in violations}


class _EmptyDiscover(_GoodAdapter):
    """discover gibt eine leere Liste zurück."""

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        return []


async def test_empty_discover_with_sample_passes() -> None:
    raw = RawSource(_URL, "rede", _TEXT.encode("utf-8"), "text/plain", _SINCE)
    assert await check_adapter(_EmptyDiscover(), _samples(raw=raw)) == []


async def test_empty_discover_without_sample_is_no_sample() -> None:
    violations = await check_adapter(_EmptyDiscover(), _samples())
    assert "no_sample" in {v.check for v in violations}


class _SubclassError(AdapterError):
    """Unterklasse von AdapterError."""


class _RaisesSubclass(_GoodAdapter):
    """fetch wirft für fremd.example die AdapterError-Unterklasse."""

    async def fetch(self, ref: SourceRef) -> RawSource:
        if "fremd.example" in ref.origin_url:
            raise _SubclassError("x")
        return RawSource(ref.origin_url, "rede", _TEXT.encode("utf-8"), "text/plain", _SINCE)


async def test_failing_ref_adapter_error_subclass_ok() -> None:
    violations = await check_adapter(_RaisesSubclass(), _samples())
    assert "fetch_error" not in {v.check for v in violations}


async def test_fetch_checked_even_with_sample_raw() -> None:
    raw = RawSource(_URL, "rede", _TEXT.encode("utf-8"), "text/plain", _SINCE)
    violations = await check_adapter(_EmptyBytes(), _samples(raw=raw))
    assert "fetch_raw" in {v.check for v in violations}


class _EmptyStringRights(_GoodAdapter):
    """rights_basis ist ein leerer String."""

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        return [SourceRef(_URL, "rede", {}, rights_basis="")]


async def test_empty_string_rights_basis_is_invalid() -> None:
    violations = await check_adapter(_EmptyStringRights(), _samples())
    assert "ref_rights" in {v.check for v in violations}


async def test_assert_conformant_lists_violation() -> None:
    adapter = _ShiftedOffsets()
    samples = _samples()
    with pytest.raises(AssertionError, match=r"\[span_offsets\]"):
        await assert_conformant(adapter, samples)


class _LongError(_GoodAdapter):
    """fetch wirft für jede Ref eine RuntimeError mit langem Text."""

    async def fetch(self, ref: SourceRef) -> RawSource:
        raise RuntimeError("y" * 10_000)


async def test_messages_are_short() -> None:
    violations = await check_adapter(_LongError(), _samples())
    assert all(len(v.message) <= 200 for v in violations)
