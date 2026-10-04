# Konformitäts-Testkit für Adapter

Das Testkit (`src/wortlaut/ingest/conformance.py`) prüft eine Adapter-Instanz gegen die
Zusicherungen des Ingest-Vertrags — ohne Netz, ohne Datenbank. Ein Adapter-Autor verdrahtet
seinen Adapter offline mit Beispieldaten und bekommt gesagt, welche Zusicherungen er verletzt;
in pytest wird daraus ein normaler roter Test.

## Minimalbeispiel

```python
from datetime import datetime

from wortlaut.ingest.adapter import AdapterError, RawSource, SourceRef, SpanDraft
from wortlaut.ingest.conformance import ConformanceSamples, assert_conformant


class MeinAdapter:
    name = "mein_adapter"
    version = "0.1.0"
    trust_level = "secondary"
    rights_basis = "zitat_p51"

    async def discover(self, since: datetime) -> list[SourceRef]:
        return [SourceRef("https://example.invalid/q1.txt", "rede", {})]

    async def fetch(self, ref: SourceRef) -> RawSource:
        if ref.origin_url.startswith("https://fremd.invalid"):
            raise AdapterError("Host nicht erlaubt")
        return RawSource(
            origin_url=ref.origin_url,
            source_type="rede",
            raw_bytes=b"Ich beginne.",
            mime_type="text/plain",
            retrieved_at=datetime(2024, 7, 5),
        )

    def normalize(self, raw: RawSource) -> str:
        return raw.raw_bytes.decode("utf-8")

    def parse(self, raw: RawSource, normalized: str) -> list[SpanDraft]:
        text = "Ich beginne."
        start = normalized.index(text)
        return [
            SpanDraft(
                verbatim_text=text,
                text_start=start,
                text_end=start + len(text),
                speaker_hint={"name": "Dr. Max Mustermann"},
                spoken_at="2024-07-05",
                locator={"sitzung": "88"},
                permalink="https://example.invalid/q1.txt",
            )
        ]

    async def aclose(self) -> None:
        pass


async def test_mein_adapter_ist_konform() -> None:
    adapter = MeinAdapter()
    samples = ConformanceSamples(
        since=datetime(2024, 1, 1),
        failing_ref=SourceRef("https://fremd.invalid/x", "rede", {}),
        min_spans=1,
    )
    await assert_conformant(adapter, samples)
```

`check_adapter` liefert die Verstöße als Liste von `Violation`; `assert_conformant` wirft bei
mindestens einem Verstoß eine `AssertionError` mit einer Zeile je Verstoß im Format
`[<Prüf-ID>] <Meldung>`. Der Test läuft offline: die `invalid`-Adressen sind Platzhalter, es
wird nichts von einem Server geholt.

## Prüf-IDs

| Prüf-ID | Was geprüft wird |
|---|---|
| `protocol` | alle Protocol-Mitglieder vorhanden: `name`, `version`, `trust_level`, `parliament`, `mandate_role`, `rights_basis`, `discover`, `fetch`, `normalize`, `parse`, `aclose` |
| `identity` | `name` und `version` sind nicht-leere `str`; `trust_level` ist ein Wert aus `TRUST_LEVELS` |
| `parliament` | `parliament` ist ein Kurzname (`^[a-z0-9]+(?:-[a-z0-9]+)*$`, z. B. `landtag-brandenburg`); `mandate_role` ist ein nicht-leerer `str` (z. B. `MdL`) |
| `rights_basis` | `rights_basis` ist `None` oder ein Wert aus `RIGHTS_BASES` |
| `discover` | wirft nicht; Ergebnis ist eine `Sequence` von `SourceRef` (leere Sequenz ist erlaubt) |
| `ref_rights` | je Ref eine auflösbare Rechtsgrundlage (Ref-Angabe, sonst Adapter-Default) in `RIGHTS_BASES` |
| `fetch` | wirft nicht; Ergebnis ist ein `RawSource` |
| `fetch_raw` | `raw_bytes` ist nicht leer |
| `fetch_mime` | `mime_type` hat ein gültiges Muster; bei `application/pdf` beginnen die Bytes mit `%PDF-` |
| `fetch_error` | an `failing_ref` muss `fetch` mit `AdapterError` (oder einer Unterklasse) scheitern |
| `no_sample` | keine Beispielquelle: weder `samples.raw` noch ein gültiges `fetch`-Ergebnis |
| `normalize` | wirft nicht; Ergebnis ist ein `str` |
| `normalize_deterministic` | zwei Aufrufe auf derselben Quelle liefern das gleiche Ergebnis |
| `parse` | wirft nicht; jedes Element ist ein `SpanDraft` |
| `parse_min_spans` | `parse` liefert mindestens `min_spans` Spans |
| `span_offsets` | `0 <= text_start < text_end <= len(normalized)` und `normalized[text_start:text_end] == verbatim_text` |
| `span_speaker` | `speaker_hint` ist ein `dict` mit nicht-leerem `str` unter `"name"` |
| `span_date` | `spoken_at` ist `""` oder ein ISO-Datum (`date.fromisoformat` gelingt) |
| `span_locator` | `locator` ist ein `dict` und `json.dumps(locator)` gelingt |
| `span_permalink` | `permalink` ist ein nicht-leerer `str` |
| `aclose` | wird zweimal aufgerufen, keiner der Aufrufe wirft (Idempotenz) |

## Grenzen

Das Testkit prüft die übergebenen Daten, nicht jede denkbare Quelle. Ein Adapter, der die
Beispieldaten gerade so durchlässt, ist damit nicht fachlich gut — das Testkit prüft den
Vertrag an den Daten, die man ihm gibt, nicht die Qualität des Parsers. Quelle der
Zusicherungen ist `src/wortlaut/pipeline/spans.py` (dort ist gemessen, was ein Verstoß im Kern
auslöst); neue Zusicherungen im Kern müssen hier nachgezogen werden.
