"""Konformitäts-Testkit (#98).

Prüft eine Adapter-Instanz gegen die Zusicherungen des Vertrags
(``wortlaut.ingest.adapter``), ohne Netz und Datenbank. Nutzung siehe
``docs/adapter-konformitaet.md``. Importiert nur stdlib + die beiden Vertragsmodule.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from wortlaut.ingest.adapter import (
    AdapterError,
    IngestAdapter,
    RawSource,
    SourceRef,
    SpanDraft,
)
from wortlaut.ingest.rights import RIGHTS_BASES

TRUST_LEVELS: tuple[str, ...] = ("verified_primary", "secondary", "low")

_MEMBERS: tuple[str, ...] = (
    "name",
    "version",
    "trust_level",
    "parliament",
    "mandate_role",
    "rights_basis",
    "discover",
    "fetch",
    "normalize",
    "parse",
    "aclose",
)

_MIME_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
PARLIAMENT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class ConformanceSamples:
    """Beispieldaten des Adapter-Autors.

    ``since`` geht an ``discover``; ``raw`` ersetzt die Beispielquelle für
    ``normalize``/``parse`` (sonst ``fetch`` der ersten Ref); an ``failing_ref``
    muss ``fetch`` mit ``AdapterError`` scheitern; ``parse`` muss mindestens
    ``min_spans`` Spans liefern (Schutz vor Leerlauf).
    """

    since: datetime
    raw: RawSource | None = None
    failing_ref: SourceRef | None = None
    min_spans: int = 1


@dataclass(frozen=True)
class Violation:
    """Ein Verstoß: stabile Prüf-ID (siehe docs/adapter-konformitaet.md) und Meldung."""

    check: str
    message: str


class _Findings:
    def __init__(self) -> None:
        self.items: list[Violation] = []
        self._once: set[str] = set()

    def add(self, check: str, message: str, once: bool = False) -> None:
        if once:
            if check in self._once:
                return
            self._once.add(check)
        self.items.append(Violation(check=check, message=message))


def _short(value: object) -> str:
    return repr(value)[:80]


def _check_identity(adapter: IngestAdapter, findings: _Findings) -> None:
    for field in ("name", "version"):
        value = getattr(adapter, field)
        if not isinstance(value, str) or not value:
            findings.add("identity", f"{field} ist keine nicht-leere Zeichenkette: {_short(value)}")
    if adapter.trust_level not in TRUST_LEVELS:
        findings.add(
            "identity", f"trust_level nicht in TRUST_LEVELS: {_short(adapter.trust_level)}"
        )
    parliament = adapter.parliament
    if not isinstance(parliament, str) or not PARLIAMENT_RE.match(parliament):
        findings.add("parliament", f"parliament ist kein Kurzname: {_short(parliament)}")
    role = adapter.mandate_role
    if not isinstance(role, str) or not role:
        findings.add(
            "parliament", f"mandate_role ist keine nicht-leere Zeichenkette: {_short(role)}"
        )
    basis = adapter.rights_basis
    if basis is not None and basis not in RIGHTS_BASES:
        findings.add("rights_basis", f"nicht in RIGHTS_BASES: {_short(basis)}")


async def _check_discover(
    adapter: IngestAdapter, samples: ConformanceSamples, findings: _Findings
) -> list[SourceRef]:
    try:
        result = await adapter.discover(samples.since)
    except Exception as exc:
        findings.add("discover", f"Wurf: {type(exc).__name__}")
        return []
    if isinstance(result, str) or not isinstance(result, Sequence):
        findings.add("discover", f"Ergebnis keine Sequence: {_short(result)}")
        return []
    refs = [item for item in result if isinstance(item, SourceRef)]
    if len(refs) != len(result):
        first = next(i for i, item in enumerate(result) if not isinstance(item, SourceRef))
        findings.add("discover", f"Element kein SourceRef, erster Index {first}")
    missing = 0
    invalid = 0
    for ref in refs:
        # Wie der Kern (resolve_rights_basis): nur None heisst „keine Angabe“.
        basis = ref.rights_basis if ref.rights_basis is not None else adapter.rights_basis
        if basis is None:
            missing += 1
        elif basis not in RIGHTS_BASES:
            invalid += 1
    if missing:
        findings.add("ref_rights", f"fehlt bei {missing} Ref(s)", once=True)
    if invalid:
        findings.add("ref_rights", f"nicht in RIGHTS_BASES bei {invalid} Ref(s)", once=True)
    return refs


async def _check_fetch(
    adapter: IngestAdapter, ref: SourceRef, findings: _Findings
) -> RawSource | None:
    try:
        raw = await adapter.fetch(ref)
    except Exception as exc:
        findings.add("fetch", f"Wurf: {type(exc).__name__}")
        return None
    if not isinstance(raw, RawSource):
        findings.add("fetch", f"Ergebnis kein RawSource: {_short(raw)}")
        return None
    if not raw.raw_bytes:
        findings.add("fetch_raw", "raw_bytes ist leer")
        return None
    mime = raw.mime_type
    if not isinstance(mime, str) or not _MIME_RE.fullmatch(mime):
        findings.add("fetch_mime", f"mime_type ungültig: {_short(mime)}")
    elif mime == "application/pdf" and not raw.raw_bytes.startswith(b"%PDF-"):
        findings.add("fetch_mime", "application/pdf ohne %PDF- am Bytes-Anfang")
    return raw


async def _check_failing_ref(
    adapter: IngestAdapter, samples: ConformanceSamples, findings: _Findings
) -> None:
    if samples.failing_ref is None:
        return
    try:
        await adapter.fetch(samples.failing_ref)
    except AdapterError:
        return
    except Exception as exc:
        findings.add("fetch_error", f"Wurf: {type(exc).__name__}")
        return
    findings.add("fetch_error", "kein Fehler")


def _check_normalize(adapter: IngestAdapter, raw: RawSource, findings: _Findings) -> str | None:
    try:
        first = adapter.normalize(raw)
        second = adapter.normalize(raw)
    except Exception as exc:
        findings.add("normalize", f"Wurf: {type(exc).__name__}")
        return None
    if not isinstance(first, str) or not isinstance(second, str):
        findings.add("normalize", f"Ergebnis kein str: {_short(first)}")
        return None
    if first != second:
        findings.add("normalize_deterministic", "zwei Aufrufe liefern unterschiedliche Ergebnisse")
    return first


def _check_parse(
    adapter: IngestAdapter,
    raw: RawSource,
    normalized: str,
    samples: ConformanceSamples,
    findings: _Findings,
) -> None:
    try:
        drafts = list(adapter.parse(raw, normalized))
    except Exception as exc:
        findings.add("parse", f"Wurf: {type(exc).__name__}")
        return
    if len(drafts) < samples.min_spans:
        findings.add(
            "parse_min_spans", f"{len(drafts)} Drafts, mindestens {samples.min_spans} erwartet"
        )
    _check_drafts(drafts, normalized, findings)


def _check_drafts(drafts: Sequence[object], normalized: str, findings: _Findings) -> None:
    affected = {
        "parse": [i for i, d in enumerate(drafts) if not isinstance(d, SpanDraft)],
        "span_offsets": [
            i
            for i, d in enumerate(drafts)
            if isinstance(d, SpanDraft) and not _offset_ok(d, normalized)
        ],
        "span_speaker": [
            i for i, d in enumerate(drafts) if isinstance(d, SpanDraft) and not _speaker_ok(d)
        ],
        "span_date": [
            i for i, d in enumerate(drafts) if isinstance(d, SpanDraft) and not _date_ok(d)
        ],
        "span_locator": [
            i for i, d in enumerate(drafts) if isinstance(d, SpanDraft) and not _locator_ok(d)
        ],
        "span_permalink": [
            i for i, d in enumerate(drafts) if isinstance(d, SpanDraft) and not _permalink_ok(d)
        ],
    }
    for check, hits in affected.items():
        if hits:
            findings.add(check, f"{len(hits)} Drafts betroffen, erster Index {hits[0]}", once=True)


def _offset_ok(draft: SpanDraft, normalized: str) -> bool:
    start, end = draft.text_start, draft.text_end
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    return 0 <= start < end <= len(normalized) and normalized[start:end] == draft.verbatim_text


def _speaker_ok(draft: SpanDraft) -> bool:
    hint = draft.speaker_hint
    if not isinstance(hint, dict):
        return False
    name = hint.get("name")
    return isinstance(name, str) and name != ""


def _date_ok(draft: SpanDraft) -> bool:
    if draft.spoken_at == "":
        return True
    try:
        date.fromisoformat(draft.spoken_at)
    except (TypeError, ValueError):
        return False
    return True


def _locator_ok(draft: SpanDraft) -> bool:
    if not isinstance(draft.locator, dict):
        return False
    try:
        json.dumps(draft.locator)
    except (TypeError, ValueError):
        return False
    return True


def _permalink_ok(draft: SpanDraft) -> bool:
    return isinstance(draft.permalink, str) and draft.permalink != ""


async def _check_aclose(adapter: IngestAdapter, findings: _Findings) -> None:
    for _ in range(2):
        try:
            await adapter.aclose()
        except Exception as exc:
            findings.add("aclose", f"Wurf: {type(exc).__name__}")
            return


async def check_adapter(adapter: object, samples: ConformanceSamples) -> list[Violation]:
    """Prüft ``adapter`` gegen den Vertrag und liefert alle Verstöße (leer = konform).

    Wirft nie wegen eines kaputten Adapters; dessen Ausnahmen werden zu Verstößen.
    """
    findings = _Findings()
    if not isinstance(adapter, IngestAdapter):
        missing = [member for member in _MEMBERS if not hasattr(adapter, member)]
        findings.add("protocol", "fehlt: " + ", ".join(missing))
        return findings.items
    _check_identity(adapter, findings)
    refs = await _check_discover(adapter, samples, findings)
    # fetch wird immer an der ersten Ref geprueft; samples.raw ersetzt nur die
    # Beispielquelle fuer normalize/parse (Spec 0098 §4.1, Schritte 4 und 6).
    fetched = await _check_fetch(adapter, refs[0], findings) if refs else None
    raw = samples.raw if samples.raw is not None else fetched
    await _check_failing_ref(adapter, samples, findings)
    if raw is None:
        findings.add("no_sample", "keine Beispielquelle")
    else:
        normalized = _check_normalize(adapter, raw, findings)
        if normalized is not None:
            _check_parse(adapter, raw, normalized, samples, findings)
    await _check_aclose(adapter, findings)
    return findings.items


async def assert_conformant(adapter: object, samples: ConformanceSamples) -> None:
    """Wie ``check_adapter``; bei Verstößen ``AssertionError`` mit einer Zeile je Verstoß."""
    violations = await check_adapter(adapter, samples)
    if violations:
        raise AssertionError("\n".join(f"[{v.check}] {v.message}" for v in violations))
