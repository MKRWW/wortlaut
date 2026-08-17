# Increment-Spec: RFC-3161-Zeitstempel auf content_hash (#76)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Lege die Dateien aus **§10** wirklich auf der Platte an und ändere die dort genannten
>   bestehenden Dateien. Am Ende müssen die neuen Dateien existieren.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.
>
> Der Spec-Review und die Stakeholder-Entscheidungen sind bereits erfolgt (§0b). Die
> Bibliotheks-Signaturen in §3 sind **am echten Paket verifiziert**, nicht geraten — halte dich
> exakt daran. Die Trust-Anker (`src/wortlaut/timestamp/trust/*.pem`) und die Test-Fixtures
> (`tests/fixtures/tsa/*`) **liegen bereits im Repo** (vom Architekten erzeugt, siehe §0a) —
> du erzeugst sie **nicht** und änderst sie **nicht**.

- **Story/Issue:** #76 · **Status:** Reviewed · **Phase/Layer:** phase/1-mvp · `timestamp` · `store` · `pipeline` · `serving` · CLI
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#3** (content_hash), **#5** (WORM), **#8** (verify), **#64** (Ingest-CLI), **#73** (Ausfall-Diagnose).
- Stack-Entscheidung dokumentiert in [../docs/adr/0008-rfc3161-timestamping.md](../docs/adr/0008-rfc3161-timestamping.md).

## 0. Ausgangslage

Der Backfill-Ausfall (#73) hat gezeigt: fällt das Internet Archive aus, hat eine frisch erfasste
Quelle **keinen anbieterunabhängigen Nachweis**, dass genau diese Bytes zu einem Zeitpunkt
existierten. WORM + `content_hash` beweisen das nur gegen uns selbst — wer uns nicht traut, hat
keinen Anker. Ein RFC-3161-Token einer unabhängigen TSA über den `content_hash` schließt genau
diese Lücke (Eigenschaft **B**: Existenz/Integrität zu Zeitpunkt T).

### 0a. Gemessene Vorklärung (Architekt, 2026-08-17) — keine Annahmen

Alle drei Kandidaten-TSAs wurden **real angefragt** (`openssl ts -query -sha256 -cert`,
Ziel-Digest `5b3b1427…a849`):

| TSA | HTTP | Content-Type | Imprint == unser SHA-256 | Nonce-Echo | Verify gegen gepinnten Root |
|---|---|---|---|---|---|
| `https://freetsa.org/tsr` | 200 | `application/timestamp-reply` | ✅ | ✅ | **OK** |
| `https://timestamp.sigstore.dev/api/v1/timestamp` | 200 | `application/timestamp-reply` | ✅ | ✅ | **OK** |
| `http://zeitstempel.dfn.de` | 200 | `application/timestamp-reply` | ✅ | ✅ | nur `http`, TSA-Feld `unspecified` |

**Gegenprobe (der adversariale Kern):** freeTSA-Token gegen den **sigstore**-Root verifiziert →
`Verification: FAILED`. Root-Pinning wirkt also nachweislich, eine fremde/gefälschte TSA fliegt auf.

**Eingebettete Zertifikate im Token** (entscheidet, was der Trust-Store liefern muss):

| TSA | Certs im Token | Leaf-Subject-DN | Root |
|---|---|---|---|
| freeTSA | **2 (Leaf + Root)** | `…,CN=www.freetsa.org,…,O=Free TSA` | `CN=www.freetsa.org, OU=Root CA`, gültig bis **2041-03-07** |
| sigstore | **1 (nur Leaf)** | `CN=sigstore-tsa,O=sigstore.dev` | `CN=sigstore-tsa-selfsigned`, gültig bis **2035-04-06** |

### 0a-🔴 Befund: reines Root-Pinning ist mit dieser Bibliothek WIRKUNGSLOS

Beim Nachbauen des Verifikations-Rezepts am echten Paket (Architekten-Probe gegen die Fixtures)
kam heraus:

> Ein `VerifierBuilder().add_root_certificate(<sigstore-Root>)` akzeptiert das **freeTSA**-Token
> mit `ok`.

Ursache: `Verifier._verify_tsr_with_chains` schüttet **Token-Zertifikate, konfigurierte Roots und
Intermediates in EINEN Trust-Bag** und übergibt den an `PKCS7_verify`. Weil freeTSA seinen eigenen
Root **mit ins Token legt**, ist die Kette selbsttragend — der konfigurierte Root wird faktisch
ignoriert. Praktische Folge: wer sich eine eigene CA + TSA-Leaf selbst signiert und **beide** ins
Token legt, käme durch. Genau die Angriffsklasse von CVE-2026-33753, nur über den Chain-Pfad statt
über die Leaf-Auswahl.

**Gegenmaßnahme (verifiziert):** zusätzlich das **Signatur-Zertifikat (Leaf) pinnen** —
`VerifierBuilder().add_root_certificate(root).tsa_certificate(leaf)`. Die Bibliothek identifiziert
den Leaf über `SignerInfo(issuer, serial)` (signatur-gedeckt, seit dem CVE-Fix) und vergleicht ihn
byte-gleich mit dem gepinnten. Damit ist der **öffentliche Schlüssel** gebunden, nicht nur ein Name.
Messergebnis der Kreuzprobe mit Leaf-Pin:

| Token | gepinntes Profil | Ergebnis |
|---|---|---|
| freeTSA | freeTSA | **ok** |
| freeTSA | sigstore | **untrusted** (`Embedded certificate does not match…`) |
| sigstore | sigstore | **ok** |
| sigstore | freeTSA | **untrusted** (`unable to get local issuer certificate`) |
| beide | eigenes Profil, **falscher Digest** | **abgelehnt** |

⚠️ `VerifierBuilder.common_name(...)` ist **nicht** die Lösung: der Parameter vergleicht trotz
seines Namens gegen den **vollständigen RFC4514-Subject-DN** (bei freeTSA ein 200-Zeichen-String
inkl. E-Mail-Adresse und Beschreibung) — fälschbar, weil ein DN kein Geheimnis ist, und zugleich
rotationsbrüchig. Wird **nicht** benutzt.

**Bereits im Repo (vom Architekten angelegt, NICHT vom Coder zu erzeugen):**
- `src/wortlaut/timestamp/trust/freetsa-root.pem` · `freetsa-leaf.pem`
- `src/wortlaut/timestamp/trust/sigstore-root.pem` · `sigstore-leaf.pem`
- `tests/fixtures/tsa/message.bin` (31 Bytes, SHA-256 `fca714d25fbd7eef88f5e936023610e6e115814a702797cb97b6f22a9a059a99`)
- `tests/fixtures/tsa/request.tsq` · `tests/fixtures/tsa/freetsa.tsr` · `tests/fixtures/tsa/sigstore.tsr`

Beide Fixture-Tokens verifizieren gegen ihr eigenes Profil (**ok**), ihr Imprint ist byte-identisch
mit `sha256(message.bin)`, und jede Kreuzprobe scheitert korrekt.

### 0b. Stakeholder-Entscheidungen (2026-08-17)

- **(a) TSA-Wahl: freeTSA primär, sigstore als Fallback.** Beide kostenlos, `https`, ohne
  Registrierung, Root öffentlich abrufbar und pinbar. *Verworfen:* DFN (nur `http`, Nutzungsrecht
  für ein privates Projekt ungeklärt); DigiCert/Sectigo (deren Timestamp-Dienste sind laut Terms
  an Code-Signing-Kunden adressiert — lizenzrechtlich graues Fundament für ein Beweis-Archiv);
  „nur eine TSA" (widerspricht der Lehre aus #73: kein Single Point of Failure).
  **Grenze, bewusst akzeptiert:** keine der beiden ist eine **eIDAS-qualifizierte** TSA. Für
  Eigenschaft B (Integrität/Existenz) genügt das; ein *qualifizierter* Zeitstempel mit
  Beweislastumkehr nach eIDAS Art. 41 bräuchte einen QTSP-Vertrag (kostenpflichtig) und ist
  **Nicht-Ziel**.
- **(b) Ausführung: ausschließlich eigener Pass**, kein Inline-Call im Ingest. Neuer Befehl
  `python -m wortlaut timestamp`. Begründung: (i) der R-CORE-02-Pfad
  `fetch→hash→dedup→archiv→WORM→insert` wird **nicht angefasst** — null Regressionsrisiko auf
  genau dem Pfad, der in #73 gebrochen war; (ii) ein Nachlauf-Pass wird **ohnehin** gebraucht,
  weil bereits ingestierte Sources sonst nie einen Stempel bekämen; (iii) ein TSA-Ausfall
  verzögert den Ingest um nichts. *Preis:* T(Stempel) = T(Ingest) + Lauf-Verzögerung; bei
  `ingest && timestamp` sind das Minuten. *Verworfen:* inline (+ Catch-up) — zwei Codepfade für
  dieselbe Sache; nur inline — Bestand bliebe für immer ungestempelt und ein TSA-Ausfall erzeugte
  eine Lücke, die nichts mehr schließen kann (`source` ist append-only).
- **(c) Speicherung: DER-Token als WORM-Objekt + append-only DB-Zeile mit Ref.** Konsistent mit
  `raw_bytes_ref`; Postgres-Backups bleiben blob-frei; Legal-Hold schützt stärker als ein
  DB-Trigger (den ein Superuser droppen kann). **Keine denormalisierten Token-Felder in der DB** —
  `gen_time`, Serial, Policy werden beim Verify **aus dem Token** gelesen, nie aus der Zeile.
  *Verworfen:* `bytea`-Spalte (bricht mit dem etablierten Muster); beides (zwei Wahrheiten für ein
  Artefakt — bei Divergenz ist unklar, welche gilt).

### 0c. Architekten-Abweichung vom Issue-Text (bewusst, security-begründet)

Das Issue sagt „TSA-URL(s) aus ENV". Umgesetzt wird: **ENV wählt aus einer im Code hinterlegten
Registry aus, ENV kann keine URL und keinen Root injizieren** (`WORTLAUT_TSA_PROFILES=freetsa,sigstore`).
Grund: Der Trust-Anker ist der **einzige** Grund, warum ein Token überhaupt etwas beweist. Wäre er
zur Laufzeit injizierbar, könnte wer immer den Container startet den Anker austauschen und
beliebige Tokens „gültig" machen — ein Downgrade-Pfad mitten im Beweis-Kern. Ein neuer TSA kostet
damit einen Code-Change + Review, und das ist hier die gewünschte Eigenschaft (analog zu den
konstanten Archiv-Hosts in `archive/archiver.py`). Die Konfigurierbarkeits-Absicht des Issues
(Auswahl/Reihenfolge/Abschalten ohne Redeploy-Änderung am Code) bleibt erhalten. Secrets gibt es
keine — TSA-URLs und Root-Zertifikate sind öffentlich (R-SEC-01 unberührt).

## 1. Ziel

Jede archivierte Quelle bekommt einen **anbieterunabhängigen, öffentlich nachrechenbaren Nachweis**,
dass ihr `content_hash` zu einem bestimmten Zeitpunkt existierte — und der Verify-Pfad sagt
unmissverständlich, ob dieser Nachweis **an genau diese Quelle bindet** (`ok`), an eine andere
(`mismatch`), von einer nicht vertrauenswürdigen Stelle stammt (`untrusted`) oder fehlt (`missing`).

## 2. Nicht-Ziele (Scope-Grenze)

- **Kein Gate.** Ein fehlender Zeitstempel blockiert **nichts**: nicht den Ingest, nicht das
  Serving, nicht die Zitierfähigkeit. R-CORE-02 bleibt **wörtlich unangetastet**.
- **Kein Inline-Call im Ingest** (§0b Entscheidung b) — `pipeline/ingest.py` wird in diesem
  Increment **nicht angefasst**.
- **Keine** qualifizierte/eIDAS-TSA, **kein** QTSP-Vertrag, **kein** Geld.
- **Keine** Fremdattestierung (Eigenschaft A, perma.cc) — das ist #78 (parked).
- **Keine** Token-Erneuerung/ERS-Kette (RFC 4998), **keine** Re-Stempelung bei
  Algorithmus-Alterung, **keine** Mehrfach-Stempelung derselben Quelle durch beide TSAs
  (das Schema lässt sie zu, der Pass macht sie nicht).
- **Keine** Änderung an Span-Parsing, `rights_basis`, Suche oder Ausgabe-Text.
- **Kein** `timestamp_pending`-Flag: „pending" ist ein **abgeleiteter** Zustand (keine Zeile in
  `source_timestamp`), kein mutabler Status. `source` bleibt append-only.

## 3. Betroffene Interfaces / Öffentliche Signaturen

### 3.1 Fremdbibliothek (verifiziert an `rfc3161-client` 1.0.8 — NICHT raten)

```python
from rfc3161_client import (
    TimestampRequestBuilder, VerifierBuilder, VerificationError,
    decode_timestamp_response,
)
from rfc3161_client.tsp import PKIStatus            # PKIStatus.GRANTED == 0

req  = TimestampRequestBuilder().data(raw).nonce(nonce=True).cert_request(cert_request=True).build()
req.as_bytes()                                      # -> bytes (DER, Body des POST)

resp = decode_timestamp_response(response_der)      # -> TimeStampResponse
resp.status                                         # -> int  (0 == GRANTED)
resp.as_bytes()                                     # -> bytes (volle TimeStampResp-DER)
resp.tst_info.gen_time                              # -> datetime.datetime (tz-aware, UTC)
resp.tst_info.message_imprint.message               # -> bytes  (der gestempelte Digest)
resp.tst_info.message_imprint.hash_algorithm        # -> cryptography.x509.ObjectIdentifier

verifier = (VerifierBuilder()
            .add_root_certificate(root)             # Kettenbildung
            .tsa_certificate(leaf)                  # PFLICHT — bindet den Schluessel (§0a-🔴)
            .build())
verifier.verify(resp, hashed_message)               # -> bool; wirft VerificationError
```

> **`verify(resp, hashed_message)` nimmt den fertigen Digest** — genau unseren `content_hash`
> als `bytes.fromhex(...)`. Es wird **nie** `verify_message` benutzt (das würde Rohbytes
> verlangen und den Verify-Pfad unnötig von WORM abhängig machen).

### 3.2 NEU: `src/wortlaut/timestamp/` — eigener Infrastruktur-Layer (Muster: `wortlaut.archive`)

```python
# ── src/wortlaut/timestamp/errors.py (nur stdlib) ───────────────────────
class TimestampError(Exception):
    """Strukturierter TSA-Fehler — trägt den Grund bis in Log und Summary."""

    def __init__(
        self,
        tsa_name: str,
        reason: str,      # 'http_status'|'timeout'|'transport'|'content_type'
                          # |'malformed'|'not_granted'|'mismatch'|'untrusted'|'oversize'
        *,
        status_code: int | None = None,
    ) -> None: ...

    tsa_name: str
    reason: str
    status_code: int | None

    def label(self) -> str:
        """Aggregations-Schlüssel, z.B. 'freetsa:http_status_503'."""

# ── src/wortlaut/timestamp/profiles.py (stdlib + cryptography) ──────────
@dataclass(frozen=True)
class TsaProfile:
    """Ein TSA-Anbieter samt seinen GEPINNTEN Trust-Ankern (Root UND Leaf, §0a-🔴)."""

    name: str            # 'freetsa' | 'sigstore' — landet als tsa_name in der DB
    url: str             # https-Endpunkt
    root_file: str       # Dateiname in wortlaut/timestamp/trust/
    leaf_file: str       # Signatur-Zertifikat der TSA — bindet den oeffentlichen Schluessel

TSA_PROFILES: dict[str, TsaProfile]   # exakt zwei Einträge, siehe §11

def load_profile(name: str) -> TsaProfile:
    """Profil aus der Registry; KeyError-frei — wirft ValueError bei unbekanntem Namen."""

def load_certificate(file_name: str) -> x509.Certificate:
    """Lädt ein gepinntes PEM aus dem Paket (importlib.resources), gecached."""

# ── src/wortlaut/timestamp/verify.py (stdlib + cryptography + rfc3161_client) ──
SHA256_OID = x509.ObjectIdentifier("2.16.840.1.101.3.4.2.1")

@dataclass(frozen=True)
class TimestampVerdict:
    """Ergebnis der Token-Prüfung — trennt 'bindet nicht' von 'nicht vertrauenswürdig'."""

    status: Literal["ok", "mismatch", "untrusted", "malformed"]
    tsa_name: str
    gen_time: datetime | None    # NUR aus dem Token gelesen, nie aus der DB
    detail: str | None

def verify_token(token_der: bytes, *, content_hash: str, tsa_name: str) -> TimestampVerdict:
    """Prüft ein gespeichertes Token gegen den content_hash. Wirft NIE — meldet immer."""

# ── src/wortlaut/timestamp/tsa.py (stdlib + httpx + gleiche Package-Module) ──
@dataclass(frozen=True)
class StampResult:
    tsa_name: str
    token_der: bytes     # volle TimeStampResp-DER (resp.as_bytes())
    gen_time: datetime

class TimeStamper(Protocol):
    """Öffentliche Naht (R-ARCH-01) — eine TSA oder eine Kette davon."""

    async def stamp(self, raw: bytes, *, content_hash: str) -> StampResult: ...

class Rfc3161Tsa:
    """Eine TSA: POST, Antwort-Härtung, SOFORTIGE Verifikation vor der Rückgabe."""

    def __init__(self, profile: TsaProfile, *, timeout_seconds: float = 10.0) -> None: ...
    async def stamp(self, raw: bytes, *, content_hash: str) -> StampResult: ...
    async def aclose(self) -> None: ...

class FallbackTimeStamper:
    """Probiert die TSAs der Reihe nach; erst wenn ALLE scheitern, fliegt der letzte Fehler."""

    def __init__(self, stampers: Sequence[Rfc3161Tsa]) -> None: ...
    async def stamp(self, raw: bytes, *, content_hash: str) -> StampResult: ...
    async def aclose(self) -> None: ...
    failures: tuple[str, ...]   # label()s des letzten stamp-Aufrufs, für die Summary

# ── src/wortlaut/timestamp/settings.py (ENV-Präfix WORTLAUT_TSA_) ───────
class TimestampSettings(BaseSettings):
    profiles: str = "freetsa,sigstore"     # Reihenfolge = Primär, Fallback
    timeout_seconds: float = 10.0
    consecutive_failure_limit: int = 5     # Circuit-Breaker, Muster aus #73
```

### 3.3 NEU: Persistenz

```python
# ── src/wortlaut/store/timestamps.py ────────────────────────────────────
@dataclass(frozen=True)
class NewSourceTimestamp:
    source_id: UUID
    tsa_name: str
    token_ref: str        # s3://bucket/key?versionId=...

@dataclass(frozen=True)
class PendingSource:
    source_id: UUID
    content_hash: str
    raw_bytes_ref: str

@dataclass(frozen=True)
class SourceTimestampRow:
    tsa_name: str
    token_ref: str
    created_at: datetime

async def insert_source_timestamp(session: AsyncSession, row: NewSourceTimestamp) -> UUID: ...
async def list_sources_without_timestamp(
    session: AsyncSession, *, limit: int | None = None
) -> list[PendingSource]: ...
async def get_timestamps_for_source(
    session: AsyncSession, source_id: UUID
) -> list[SourceTimestampRow]: ...

# ── src/wortlaut/store/models.py (ergänzt) ──────────────────────────────
class SourceTimestamp(Base):
    __tablename__ = "source_timestamp"
    id: UUID · source_id: UUID (FK source.id) · tsa_name: str · token_ref: str · created_at: datetime
```

### 3.4 NEU/GEÄNDERT: Orchestrierung + Ausgabe

```python
# ── src/wortlaut/pipeline/timestamp.py (neu) ────────────────────────────
@dataclass(frozen=True)
class TimestampOutcome:
    status: Literal["stamped", "hash_mismatch", "worm_missing", "tsa_failed"]
    source_id: UUID
    tsa_name: str | None = None
    failures: tuple[str, ...] = ()

async def timestamp_source(
    pending: PendingSource, *, session: AsyncSession, worm: WormStore, stamper: TimeStamper
) -> TimestampOutcome:
    """WORM lesen → Hash gegenprüfen → stempeln → Token in WORM → Zeile schreiben."""

# ── src/wortlaut/pipeline/verify.py (ERWEITERT, bestehende Felder unverändert) ──
@dataclass(frozen=True)
class VerifyReport:
    ok: bool
    source_id: UUID
    status: Literal["ok", "hash_mismatch", "source_not_found", "worm_missing"]
    content_hash_expected: str | None
    content_hash_actual: str | None
    archive_wayback: str | None
    archive_today: str | None
    # NEU (additiv, ans Ende):
    timestamp_status: Literal[
        "ok", "mismatch", "untrusted", "malformed", "missing", "unreadable"
    ] = "missing"
    timestamp_tsa: str | None = None
    timestamp_gen_time: datetime | None = None

async def verify_source(
    source_id: UUID, *, session: AsyncSession, worm: WormStore
) -> VerifyReport: ...
# Signatur unverändert — der Token wird über denselben worm gelesen.

# ── src/wortlaut/serving/schemas.py (VerifyResult ERWEITERT, additiv ans Ende) ──
timestamp_status: str
timestamp_tsa: str | None
timestamp_gen_time: datetime | None
```

- **Layering (R-ARCH-02):** `wortlaut.timestamp` ist ein **eigener Infrastruktur-Layer** wie
  `wortlaut.archive` und importiert **keinen** anderen wortlaut-Layer — neuer import-linter-Contract
  (§11). Import-Richtung: `cli → pipeline → timestamp|store|archive`; `serving → pipeline`.
  `tsa.py` bleibt **pydantic-frei**: `TimestampSettings` wird **nur** im Composition-Root (CLI)
  gelesen und als einfache Werte injiziert (DI, Muster aus #73).
- **`ok` bleibt hash-only.** `VerifyReport.ok` ist weiterhin **ausschließlich** die Aussage über
  den Hash. Ein fehlender/kaputter Zeitstempel setzt `ok` **nicht** auf `False` — sonst würde aus
  dem additiven Increment doch ein Gate (Nicht-Ziel §2) und jeder Bestandsdatensatz wäre über
  Nacht „nicht ok".

## 4. Design (kurz)

**4.1 Warum die Bindung an `content_hash` und nicht an die Rohbytes.**
Das RFC-3161-`messageImprint` **ist** ein Hash. Wir stempeln SHA-256 über die Rohbytes — also
exakt `evidence.content_hash`. Damit ist der Imprint im Token **byte-identisch** mit dem Anker im
Ledger, und die Beweiskette ist ohne Zwischenschritt lesbar:

```
WORM-Rohbytes --sha256--> content_hash (Ledger) == messageImprint (Token) --Signatur--> TSA @ gen_time
```

⚠️ **Der naheliegende Fehler wäre, den Hex-String des `content_hash` zu stempeln.** Dann bände
das Token an `sha256("5b3b…")` statt an die Bytes, und jeder Prüfer bräuchte einen
undokumentierten Extraschritt. Gestempelt werden **die Rohbytes** (`TimestampRequestBuilder().data(raw)`
hasht selbst), verifiziert wird gegen **`bytes.fromhex(content_hash)`**.

**4.2 Der Pass liest WORM — und das ist ein Feature.**
`TimestampRequestBuilder` nimmt nur Rohdaten, keinen fertigen Digest. Der Pass muss die Bytes also
ohnehin aus WORM holen — und **rechnet den Hash vorher nach**. Stimmt er nicht mit
`source.content_hash` überein, wird **nicht gestempelt** (Status `hash_mismatch`, laut). Wir
beglaubigen nie Bytes, deren Bindung an den Ledger wir nicht gerade selbst nachgerechnet haben.

**4.3 Verifikation SOFORT beim Holen — nicht erst beim Verify (🔴 adversarialer Kern).**
`Rfc3161Tsa.stamp` gibt ein Token **nur** zurück, wenn es vorher vollständig verifiziert wurde:
Status `GRANTED`, Imprint-Algorithmus **SHA-256**, Imprint == `content_hash`, Kette gegen den
**gepinnten Root**, EKU `id-kp-timeStamping`, erwarteter Leaf-CN. Scheitert irgendetwas davon,
wirft `stamp` einen `TimestampError` — und `FallbackTimeStamper` geht zur nächsten TSA.
**Konsequenz: eine gefälschte oder nicht bindende TSA-Antwort wird nie persistiert.** Der
Verify-Pfad prüft dieselbe Bindung später noch einmal gegen den dann gespeicherten Zustand — die
Prüfung ist also doppelt, an beiden Enden.

**4.4 Statusmatrix der Token-Prüfung (nie ein falsches `ok`, nie ein kollabierter Grund).**
Die Prüfschritte laufen in **dieser** Reihenfolge, damit die Ursache erhalten bleibt statt in ein
opakes „ungültig" zu kollabieren (die Lehre aus #73 §0, Defekt 3):

| Schritt | Fehlerfall | `status` |
|---|---|---|
| 1. `decode_timestamp_response` | Exception / kein DER | `malformed` |
| 2. `resp.status == PKIStatus.GRANTED` | z.B. `rejection` | `malformed` |
| 3. `message_imprint.hash_algorithm == SHA256_OID` | SHA-1/SHA-512-Token | `mismatch` |
| 4. `message_imprint.message == bytes.fromhex(content_hash)` | bindet an andere Bytes | `mismatch` |
| 5. `verifier.verify(resp, bytes.fromhex(content_hash))` wirft `VerificationError` | Leaf-Pin/Kette/EKU/Signatur | `untrusted` |
| — alles bestanden | — | `ok` |

Schritt 3+4 **vor** Schritt 5, weil die Bibliothek beide Fälle in dieselbe `VerificationError`
wirft. Ohne die Vorab-Trennung wäre „das Token gehört zu einer anderen Quelle" von „die TSA ist
gefälscht" nicht unterscheidbar — zwei völlig verschiedene Befunde.
Unbekannter `tsa_name` (Profil nicht in der Registry) ⇒ `untrusted` (kein Anker ⇒ kein Vertrauen),
**nie** `ok`.

**4.5 Antwort-Härtung am HTTP-Rand (CLAUDE.md §2.3: „was, wenn diese Bytes nicht sind, was sie
vorgeben?").** Vor dem Parsen: Statuscode 2xx, `Content-Type` beginnt mit
`application/timestamp-reply`, Body ≤ 64 KiB (ein legitimes Token liegt bei 1–7 KiB). Redirects
werden **nicht** gefolgt (`follow_redirects=False`). Alles andere ⇒ `TimestampError` ⇒ nächste TSA.

**4.6 SSRF + Transport (R-SEC-05).** Die TSA-URLs sind **Konstanten aus der Registry**, keine
Fremdeingabe — dieselbe Lage wie bei den Archiv-Hosts. Es wird über `httpx.AsyncClient` mit
`follow_redirects=False` und Timeout gepostet. Der Digest wandert im **POST-Body**, nie in der
Query (R-SEC-01). Es gibt keine Credentials.

**4.7 Kein Retry innerhalb einer TSA — der Pass IST der Retry.**
Eine TSA, ein Versuch; scheitert sie, übernimmt sofort die nächste. Scheitern beide, bleibt die
Quelle „pending" und der **nächste Lauf** holt sie nach (idempotent, weil pending abgeleitet ist).
Das spart eine zweite Retry-Maschinerie neben `archive/retry.py` (die wegen der `ArchiveError`-Bindung
nicht wiederverwendbar ist) und wäre ohnehin nur eine Verdopplung.

**4.8 Circuit-Breaker (Muster aus #73 §4.5).** Die CLI zählt **aufeinanderfolgende** `tsa_failed`;
bei `consecutive_failure_limit` bricht der Lauf mit Exit **3** ab und nennt den häufigsten Grund.
Limit ≤ 0 schaltet ihn ab.

**4.9 `hash_mismatch` ist ein Alarm, kein Statistikposten.** Findet der Pass eine Quelle, deren
WORM-Bytes nicht mehr zum Ledger-Hash passen, ist das der schwerwiegendste Befund, den dieses
Werkzeug produzieren kann. Der Lauf verarbeitet zu Ende (damit der Report vollständig ist) und
endet dann mit Exit **4** — abgegrenzt von 0/2/3.

**4.10 Idempotenz + Race.** `UNIQUE (source_id, tsa_name)` macht den Pass wiederholbar; ein
paralleler zweiter Lauf läuft in `IntegrityError` und zählt die Quelle als bereits gestempelt statt
zu crashen. Reihenfolge: WORM-`put` **vor** dem Insert; scheitert der Insert am UNIQUE, bleibt ein
verwaistes WORM-Objekt zurück — Speicherplatz, kein Korrektheitsproblem, bewusst akzeptiert (§8).

**4.11 WORM-Key.** `f"{content_hash}.{tsa_name}.tsr"` — content-adressiert nach dem, was das Token
beglaubigt, und je TSA kollisionsfrei. Der Namensraum der Rohbytes (blanker Hash) bleibt getrennt.
`content_type="application/timestamp-reply"`.

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)

- [ ] **AC1** *(Bindung, Kern)* — *Given* das Fixture-Token `freetsa.tsr` und
      `content_hash = sha256(message.bin) = fca714d2…9a99`, *When* `verify_token(...)`, *Then*
      `status == "ok"`, `tsa_name == "freetsa"` und `gen_time` ist ein tz-aware `datetime` aus dem
      Token. `[unit]`
- [ ] **AC2** *(🔴 bindet nicht)* — *Given* dasselbe gültige Token, aber ein **anderer**
      `content_hash` (ein Nibble geflippt), *When* `verify_token(...)`, *Then* `status == "mismatch"`
      — **nicht** `ok` und **nicht** `untrusted`. `[unit]`
- [ ] **AC3** *(🔴 gefälschte/fremde TSA — Kern von §0a-🔴)* — *Given* das `freetsa.tsr`-Token,
      **das seine eigene Kette inklusive Root mitbringt**, *When* `verify_token` mit
      `tsa_name="sigstore"` (also gegen das **falsche gepinnte Profil**), *Then*
      `status == "untrusted"` — **nicht** `ok`. Umgekehrt gilt dasselbe für `sigstore.tsr` gegen
      `freetsa`. **Beide Richtungen sind Pflicht:** ohne Leaf-Pin scheitert nur die Richtung
      „sigstore-Token gegen freeTSA-Profil" ohnehin (sigstore bettet keinen Root ein) — allein
      geprüft gäbe sie falsche Sicherheit. Die Richtung „freeTSA-Token gegen sigstore-Profil" ist
      die einzige, die den Bug aus §0a-🔴 aufdeckt. `[unit]`
- [ ] **AC4** *(malformed)* — *Given* `b"nicht-der"` bzw. ein an beliebiger Stelle abgeschnittenes
      Fixture-Token, *When* `verify_token`, *Then* `status == "malformed"` und **keine** Exception
      verlässt die Funktion. `[unit]`
- [ ] **AC5** *(unbekannter Anker)* — *Given* ein gültiges Token, *When* `verify_token` mit
      `tsa_name="gibtsnicht"`, *Then* `status == "untrusted"` (kein Anker ⇒ kein Vertrauen), nie `ok`. `[unit]`
- [ ] **AC6** *(Fallback)* — *Given* TSA A wirft `TimestampError`, TSA B liefert ein gültiges Token,
      *When* `FallbackTimeStamper.stamp`, *Then* Ergebnis stammt von B (`tsa_name == "B"`),
      A wurde **genau 1×** aufgerufen, und `failures` enthält A's `label()`. `[unit]`
- [ ] **AC7** *(alle TSAs tot)* — *Given* beide TSAs werfen, *When* `stamp`, *Then* fliegt ein
      `TimestampError`, und `failures` enthält **beide** Labels. `[unit]`
- [ ] **AC8** *(🔴 Antwort-Härtung)* — *Given* eine TSA antwortet **200 mit einem gültig
      aussehenden Body, aber `Content-Type: text/html`** (bzw. 200 mit >64 KiB Body, bzw. 302 mit
      `Location`), *When* `Rfc3161Tsa.stamp`, *Then* `TimestampError` mit
      `reason ∈ {content_type, oversize, http_status}` — **kein** Token wird zurückgegeben. `[unit]`
- [ ] **AC9** *(🔴 TSA liefert Token für fremden Imprint)* — *Given* eine TSA antwortet 200 mit
      dem **echten Fixture-Token**, obwohl ganz andere Rohbytes gestempelt werden sollten, *When*
      `Rfc3161Tsa.stamp`, *Then* `TimestampError(reason="mismatch")` — das Token wird **nicht**
      zurückgegeben und damit nie persistiert. `[unit]`
- [ ] **AC10** *(Hash-Gegenprüfung vor dem Stempeln)* — *Given* eine `source`, deren WORM-Bytes
      **nicht** zu `content_hash` passen, *When* `timestamp_source`, *Then* Status `hash_mismatch`,
      der Stamper wurde **0×** aufgerufen und **keine** `source_timestamp`-Zeile entsteht. `[unit]`
- [ ] **AC11** *(Happy Path persistiert)* — *Given* eine `source` mit passenden WORM-Bytes und
      einer funktionierenden TSA, *When* `timestamp_source`, *Then* Status `stamped`, das Token
      liegt als WORM-Objekt unter `{content_hash}.{tsa}.tsr`, und genau **eine**
      `source_timestamp`-Zeile mit `token_ref` == der WORM-Ref existiert. `[unit]` + `[integration]`
- [ ] **AC12** *(Idempotenz)* — *Given* eine bereits gestempelte `source`, *When* der Pass erneut
      läuft, *Then* liefert `list_sources_without_timestamp` sie **nicht** mehr, es entsteht **keine**
      zweite Zeile, und es wird **kein** TSA-Call abgesetzt. `[integration]`
- [ ] **AC13** *(Verify meldet ok/mismatch/missing/unreadable)* — *Given* (a) eine gestempelte
      Quelle, (b) dieselbe Quelle mit einem Token, das an einen anderen Hash bindet, (c) eine
      ungestempelte Quelle, (d) eine Quelle mit Zeile, deren WORM-Token **nicht lesbar** ist,
      *When* `verify_source`, *Then* `timestamp_status` ist `ok` / `mismatch` / `missing` /
      `unreadable`, und `timestamp_gen_time` ist **nur** im Fall (a) gesetzt. Fall (d) darf
      **nicht** als `missing` erscheinen (§11-Nachtrag). `[integration]`
- [ ] **AC14** *(kein Gate)* — *Given* eine Quelle **ohne** Zeitstempel, *When* `verify_source` und
      `GET /v1/spans/{id}/verify`, *Then* `ok is True` und `status == "ok"` (Hash stimmt),
      `timestamp_status == "missing"` — der fehlende Stempel degradiert **nichts**. `[integration]`
- [ ] **AC15** *(Immutabilität, R-DATA-01)* — *Given* eine `source_timestamp`-Zeile, *When* UPDATE
      oder DELETE darauf, *Then* wirft die DB (`append-only`-Trigger). `[integration]`
- [ ] **AC16** *(CLI-Summary + Exit 0)* — *Given* ein Lauf über N pending Quellen, die alle
      gestempelt werden, *When* `main(["timestamp"])`, *Then* Exit **0** und die Summary-Zeile
      nennt `pending=`, `stamped=`, `hash_mismatch=`, `worm_missing=`, `tsa_failed=` und `reasons=`. `[unit]`
- [ ] **AC17** *(Circuit-Breaker)* — *Given* ein Lauf, in dem jede TSA-Anfrage scheitert, *When*
      `consecutive_failure_limit` erreicht ist, *Then* bricht der Lauf ab, Exit ist **3**, es werden
      **nicht mehr** als `limit` Quellen verarbeitet, und die Ausgabe nennt den häufigsten Grund. `[unit]`
- [ ] **AC18** *(hash_mismatch ist laut)* — *Given* ein Lauf mit ≥1 `hash_mismatch`, *When* der Lauf
      endet, *Then* ist der Exit-Code **4** und die Ausgabe nennt die betroffene `source_id`. `[unit]`
- [ ] **AC19** *(Layering)* — *Given* der neue Layer, *When* `lint-imports`, *Then* ist der Contract
      „Timestamp-Layer importiert keinen anderen wortlaut-Layer" **KEPT**. `[ci]`
- [ ] **AC20** — CI vollständig grün (ruff · format · mypy strict · pytest Unit+Integration ·
      import-linter · Coverage ≥ 80) **und 0 neue Sonar-Issues** im PR. `[ci]`

> Jedes AC ist von einem automatisierten Test mit Ja/Nein beantwortbar.

## 6. Testplan (Test-zu-AC-Mapping)

**Unit (rein, httpx gemockt, keine Netz-Calls — R-TEST-03):**
- `tests/unit/test_timestamp_verify.py` — `test_fixture_token_verifies` → AC1 ·
  `test_wrong_content_hash_is_mismatch` → **AC2** · `test_wrong_root_is_untrusted` → **AC3** ·
  `test_garbage_and_truncated_are_malformed` → AC4 · `test_unknown_tsa_is_untrusted` → AC5
- `tests/unit/test_timestamp_tsa.py` — `test_fallback_uses_second_tsa` → AC6 ·
  `test_all_tsa_fail_raises` → AC7 · `test_response_hardening_rejects` → **AC8** ·
  `test_token_for_foreign_imprint_rejected` → **AC9**
- `tests/unit/test_timestamp_pipeline.py` — `test_hash_mismatch_never_stamps` → **AC10** ·
  `test_happy_path_persists_token` → AC11
- `tests/unit/test_cli_timestamp.py` — `test_summary_and_exit_zero` → AC16 ·
  `test_circuit_breaker_aborts_run` → AC17 · `test_hash_mismatch_exit_four` → AC18

**Integration (Testcontainers, echte Postgres/MinIO) — legt der Architekt an:**
- `tests/integration/test_timestamp_store.py` — Persistenz + Ref-Roundtrip → AC11 ·
  Idempotenz über zwei Läufe → AC12 · ok/mismatch/missing/unreadable → AC13 ·
  `ok is True` trotz fehlendem Stempel → **AC14** · UPDATE/DELETE scheitert → **AC15**

> Der Kniff, der diese Tests aussagekräftig macht: die Test-Quelle bekommt als Rohbytes **genau**
> den Inhalt von `tests/fixtures/tsa/message.bin`. Damit ist ihr `content_hash` byte-identisch mit
> dem `messageImprint` der **echten** Fixture-Tokens — die Bindung Token↔Quelle wird gegen eine
> reale TSA-Antwort geprüft, ohne je das Netz anzufassen (R-TEST-03).

**Invarianten (Pflicht, R-DATA):** `source` und `span` bekommen **keinen** neuen UPDATE-/DELETE-Pfad;
die bestehenden Append-only-Trigger-Tests bleiben unverändert grün. Die neue Tabelle bringt ihren
**eigenen** Trigger mit (AC15).

**Fixtures:** ausschließlich die vom Architekten erzeugten, **echten** TSA-Tokens unter
`tests/fixtures/tsa/` (§0a). Es werden **keine** Tokens im Test erzeugt und **kein** TSA-Call
abgesetzt.

## 7. Recht / Security

- **Beweis-Integrität (R-CORE-02, R-DATA-02):** Der Stempel liegt auf SHA-256 **über die Rohbytes**
  — derselbe Anker wie `content_hash`, kein zweiter, konkurrierender Hash (§4.1). Gestempelt wird
  nur, was unmittelbar vorher gegen den Ledger nachgerechnet wurde (§4.2).
- **Trust-Anker gepinnt (🔴 Kern):** Verifiziert wird gegen **im Repo eingecheckte, im Review
  sichtbare** Zertifikate — Root **und** Signatur-Leaf (§0a-🔴). Reines Root-Pinning wäre
  wirkungslos, weil die Bibliothek Token-Zertifikate und konfigurierte Roots in einen Trust-Bag
  wirft; erst der Leaf-Pin bindet den **öffentlichen Schlüssel**. Gegenprobe in §0a-🔴 belegt beide
  Richtungen.
  **CVE-2026-33753** (`rfc3161-client` ≤ 1.0.5, Authorization Bypass durch naive Leaf-Auswahl aus
  dem PKCS#7-Bag) ist genau diese Angriffsklasse ⇒ Dependency **`>=1.0.8`** gepinnt (ADR-0008).
- **Immutabilität (R-DATA-01):** `source_timestamp` ist append-only per Trigger (AC15). Es
  entsteht **kein** UPDATE-/DELETE-Pfad auf `source`/`span`. Das WORM-Objekt trägt Legal-Hold wie
  jede andere Beweis-Ablage.
- **Kein Gate, keine Degradierung (R-CORE-02 unberührt):** Ein fehlender Zeitstempel ändert weder
  `ok` noch Sichtbarkeit noch Zitierfähigkeit (AC14). Der Increment ist rein additiv.
- **Secrets (R-SEC-01):** keine. TSA-URLs und Root-Zertifikate sind öffentlich; der Digest geht im
  POST-Body, nie in einer Query; Logs enthalten `tsa_name`, Grund, Statuscode, `source_id`.
- **SSRF (R-SEC-05):** TSA-URLs sind Code-Konstanten, keine Fremdeingabe; `follow_redirects=False`;
  ENV kann keinen Endpunkt injizieren (§0c).
- **Fremd-Content bleibt Daten (R-SEC-07):** Aus der TSA-Antwort werden ausschließlich Statuscode,
  Content-Type und ASN.1-Felder gelesen — nie Anweisungen, nie Freitext in den Ausgabepfad.
- **Recht:** `rights_basis` unberührt. Der Zeitstempel ist **nicht** eIDAS-qualifiziert (§0b) —
  er beweist Integrität/Existenz, nicht eine gesetzlich vermutete Beweiskraft. Diese Grenze gehört
  in die Doku, sobald der Verify-Output öffentlich erklärt wird.

## 8. Risiken & offene Fragen

- **🟠 freeTSA ist ein Ein-Personen-Dienst ohne SLA.** Genau deshalb der Fallback. Fallen **beide**
  aus, bleiben Quellen „pending" — sichtbar in der Summary, nachholbar per Re-Run, ohne dass
  irgendetwas anderes stehenbleibt (das ist der ganze Sinn von Entscheidung (b)).
- **🟠 Zertifikats-Rotation ist die Kehrseite des Leaf-Pins.** Weil das Signatur-Zertifikat gepinnt
  ist (§0a-🔴, ohne den ist die Prüfung wertlos), bricht eine Rotation der TSA das **Stempeln** —
  aber **laut und sofort**: `stamp` liefert `untrusted`, die Quelle bleibt pending, die Summary
  zeigt es, der Fallback übernimmt. Das ist die richtige Fehlerrichtung (nie stumm akzeptieren).
  Behoben wird es durch ein **neues Profil** (`freetsa-2027`), nie durch Umbiegen des alten —
  sonst werden alle Alt-Tokens dieses Profils unverifizierbar. `tsa_name` in der DB macht das
  tragfähig. freeTSA hat sein Leaf zuletzt im **März 2026** rotiert; mit zwei Profilen im Fallback
  steht der Betrieb dabei nicht.
- **🟠 Verwaiste WORM-Objekte** bei Insert-Race (§4.10) — Speicherplatz, kein Korrektheitsproblem.
- **🟡 Kein qualifizierter Zeitstempel** (§0b) — bewusst; Upgrade-Pfad ist ein QTSP-Vertrag, kein Code.
- **🟡 Zeitversatz Ingest→Stempel** (§0b) — dokumentiert; wer ihn minimieren will, ruft
  `ingest && timestamp` hintereinander auf.
- **🟡 Neue Rust-Dependency** (`rfc3161-client`, PyO3-Wheels) im prod-Image. Muss im Docker-Build
  grün sein — der CI-Job `docker` deckt das ab.
- **Fixture-Alterung:** die Bibliothek validiert die Kette laut Doku **zum `gen_time` des Tokens**;
  die Fixtures sollten daher nicht mit den Zertifikaten „ablaufen". Sollte sich das im Test anders
  verhalten, ist das ein Bibliotheks-Befund und gehört als Risiko in den PR — **nicht** durch
  Aufweichen der Verifikation „gelöst".

## 9. Definition of Done (Verweis)

[../docs/rules.md](../docs/rules.md) DoD: alle AC grün (Unit + Integration), alle Gates grün
(ruff · ruff format · mypy strict inkl. Tests · pytest Unit+Integration · import-linter ·
Coverage ≥ 80 · Security-Gate · SonarCloud 0 neue Issues), Review durch Architekt, Invarianten
gewahrt, keine Gott-Klassen, kein Secret, keine Live-Calls im CI-Gate. PR referenziert **#76**
(`Closes #76`) gegen `develop`.

---

## 10. Files (NUR diese anlegen bzw. ändern)

**Neu anlegen:**
- `src/wortlaut/timestamp/__init__.py`        — leer (Paket-Marker)
- `src/wortlaut/timestamp/errors.py`          — `TimestampError`
- `src/wortlaut/timestamp/profiles.py`        — `TsaProfile`, `TSA_PROFILES`, `load_profile`, `load_root_certificate`
- `src/wortlaut/timestamp/verify.py`          — `SHA256_OID`, `TimestampVerdict`, `verify_token`
- `src/wortlaut/timestamp/tsa.py`             — `StampResult`, `TimeStamper`, `Rfc3161Tsa`, `FallbackTimeStamper`
- `src/wortlaut/timestamp/settings.py`        — `TimestampSettings`
- `src/wortlaut/store/timestamps.py`          — Persistenz-Funktionen
- `src/wortlaut/pipeline/timestamp.py`        — `TimestampOutcome`, `timestamp_source`
- `migrations/versions/0004_source_timestamp.py` — Tabelle + Trigger
- `tests/unit/test_timestamp_verify.py`       — AC1–AC5
- `tests/unit/test_timestamp_tsa.py`          — AC6–AC9
- `tests/unit/test_timestamp_pipeline.py`     — AC10, AC11
- `tests/unit/test_cli_timestamp.py`          — AC16, AC17, AC18

**Ändern (chirurgisch, nichts Umliegendes umbauen):**
- `src/wortlaut/store/models.py`      — Klasse `SourceTimestamp` **anhängen**, nichts Bestehendes ändern
- `src/wortlaut/pipeline/verify.py`   — drei Felder an `VerifyReport` **anhängen** + Token-Prüfung
- `src/wortlaut/serving/schemas.py`   — drei Felder an `VerifyResult` **anhängen**
- `src/wortlaut/serving/app.py`       — die drei Felder in `VerifyResult(...)` durchreichen
- `src/wortlaut/cli.py`               — Subcommand `timestamp` + `_run_timestamp`
- `.importlinter`                     — neuer Contract (Wortlaut in §11)

> **NICHT anfassen:** `pyproject.toml`, `uv.lock` (Dependency + Lock hat der Architekt bereits
> gesetzt), `src/wortlaut/pipeline/ingest.py`, `src/wortlaut/archive/**`, `src/wortlaut/ingest/**`,
> `src/wortlaut/store/worm.py`, `src/wortlaut/store/sources.py`, `src/wortlaut/evidence/**`,
> `src/wortlaut/timestamp/trust/*.pem`, `tests/fixtures/tsa/**`, `migrations/versions/0001*`,
> `0002*`, `0003*`, alle übrigen Tests.
> Die **Integrationstests (AC11–AC15)** zieht der Architekt separat nach — lege sie **nicht** an.

## 11. Umsetzungsdetails je Datei

### `src/wortlaut/timestamp/errors.py` (neu)
Muster **exakt wie** `src/wortlaut/archive/errors.py` (vorher lesen!): `TimestampError(Exception)`
mit `__init__(self, tsa_name, reason, *, status_code=None)`, setzt die drei gleichnamigen
Attribute, `__str__` als `f"{tsa_name}: {reason}"` plus `f" {status_code}"` wenn gesetzt.
`label()` liefert `f"{tsa_name}:{reason}"`, bei gesetztem `status_code`
`f"{tsa_name}:{reason}_{status_code}"`. Kein `transient`-Flag (§4.7: kein Retry).

### `src/wortlaut/timestamp/profiles.py` (neu)
```python
TSA_PROFILES = {
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
```
`load_profile(name)` → `TSA_PROFILES[name]`, bei unbekanntem Namen `ValueError` mit dem Namen in
der Meldung. `load_certificate(file_name)` liest die PEM-Datei über
`importlib.resources.files("wortlaut.timestamp") / "trust" / file_name` (als Bytes) und gibt
`cryptography.x509.load_pem_x509_certificate(...)` zurück; **`functools.lru_cache` über den
Dateinamen** (ein `str`, hashbar — nicht über das Profil-Objekt).
**Kommentar in die Datei (wörtlich sinngemäß):** Profile sind **append-only**. Rotiert eine TSA ihr
Signatur-Zertifikat, wird ein **neues Profil** angelegt (z.B. `freetsa-2027`) — ein bestehendes wird
**nie** umgebogen, sonst werden alle bereits gestempelten Tokens dieses Profils unverifizierbar.
`source_timestamp.tsa_name` pinnt, welches Profil ein Token prüft.

### `src/wortlaut/timestamp/verify.py` (neu)
`verify_token` implementiert **exakt** die Statusmatrix aus §4.4, in dieser Reihenfolge, und
**wirft nie** — jeder Fehlerpfad endet in einem `TimestampVerdict`.
- `tsa_name` unbekannt (`load_profile` wirft `ValueError`) ⇒ `TimestampVerdict("untrusted", tsa_name, None, <grund>)`.
- Schritt 1/2 ⇒ `malformed`. Schritt 3/4 ⇒ `mismatch`. `VerificationError` in Schritt 5 ⇒ `untrusted`.
- Der Verifier in Schritt 5 wird **immer** mit **beiden** Ankern gebaut:
  `VerifierBuilder().add_root_certificate(load_certificate(profile.root_file))
  .tsa_certificate(load_certificate(profile.leaf_file)).build()`.
  **`common_name(...)` wird NICHT gesetzt** (§0a-🔴). Ohne `tsa_certificate` ist die ganze Prüfung
  wertlos — das ist kein Stil, sondern der Kern des Increments.
- Der Vergleich in Schritt 4 ist `hmac.compare_digest(imprint.message, bytes.fromhex(content_hash))`.
  Ein `content_hash`, der nicht aus 64 Hex-Zeichen besteht (`ValueError` in `fromhex`), ⇒ `mismatch`.
- Erst ganz am Ende `gen_time` aus `resp.tst_info.gen_time` lesen und im `ok`-Verdict mitgeben;
  in allen anderen Verdicts ist `gen_time` `None`.
- `detail` trägt eine **kurze** Begründung (z.B. `"imprint algorithm 1.3.14.3.2.26 != sha256"`,
  `str(exc)` bei `VerificationError`) — nie den Token-Inhalt, nie Rohbytes.

### `src/wortlaut/timestamp/tsa.py` (neu)
- `Rfc3161Tsa.__init__` speichert Profil + Timeout und legt den `httpx.AsyncClient` **lazy** an
  (Muster `_client_or_create` aus `archive/archiver.py`), mit
  `httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)`.
- `stamp(raw, *, content_hash)`:
  1. Request bauen:
     `TimestampRequestBuilder().data(raw).nonce(nonce=True).cert_request(cert_request=True).build()`,
     Body = `req.as_bytes()`.
  2. `await client.post(profile.url, content=body, headers={"Content-Type": "application/timestamp-query"})`;
     `httpx.TimeoutException` ⇒ `TimestampError(name, "timeout")`, `httpx.TransportError` ⇒
     `TimestampError(name, "transport")`.
  3. Härtung (§4.5) **in dieser Reihenfolge**: Status nicht 2xx ⇒ `"http_status"` (+ `status_code`);
     `Content-Type` (ohne Parameter, lowercase) ≠ `application/timestamp-reply` ⇒ `"content_type"`;
     `len(body) > 65536` ⇒ `"oversize"`.
  4. `verdict = verify_token(response.content, content_hash=content_hash, tsa_name=profile.name)` —
     **dieselbe** Funktion wie beim späteren Verify, keine zweite Implementierung.
     `verdict.status != "ok"` ⇒ `TimestampError(name, verdict.status)` (also `"malformed"`,
     `"mismatch"` oder `"untrusted"`).
  5. Rückgabe `StampResult(profile.name, response.content, verdict.gen_time)`.
     Gespeichert wird **`response.content`** (die volle `TimeStampResp`-DER, wie empfangen) — das
     ist exakt das, was `decode_timestamp_response` später wieder liest.
- `FallbackTimeStamper.stamp` iteriert über die Stamper, sammelt bei `TimestampError` das `label()`
  in einer lokalen Liste, setzt `self.failures` **bei jedem Aufruf neu** (kein akkumulierender
  Zustand über Quellen hinweg) und wirft den **letzten** Fehler, wenn alle scheitern. Bei Erfolg
  enthält `failures` die Labels der vorher gescheiterten. `aclose` schließt **alle** inneren
  Stamper einzeln in `contextlib.suppress(Exception)`.

### `src/wortlaut/timestamp/settings.py` (neu)
Muster **exakt wie** `src/wortlaut/archive/settings.py`, `env_prefix="WORTLAUT_TSA_"`, drei Felder
aus §3.2. Zusätzlich eine Methode
`profile_names(self) -> list[str]`, die `profiles` an `,` splittet, trimmt und leere Einträge
verwirft.

### `migrations/versions/0004_source_timestamp.py` (neu)
`revision = "0004"`, `down_revision = "0003"`. Rohes SQL (Muster: `0002_ingest_adapter_source.py`
vorher lesen):
```sql
CREATE TABLE source_timestamp (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id  uuid NOT NULL REFERENCES source(id),
  tsa_name   text NOT NULL,
  token_ref  text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_source_timestamp_tsa UNIQUE (source_id, tsa_name)
);
CREATE INDEX ix_source_timestamp_source ON source_timestamp(source_id);
CREATE TRIGGER trg_source_timestamp_immutable BEFORE UPDATE OR DELETE ON source_timestamp
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
```
`forbid_mutation()` existiert bereits aus 0002 — **nicht** neu anlegen. `downgrade()` droppt
Trigger, Index und Tabelle (nicht die Funktion).

### `src/wortlaut/store/models.py` (ändern — nur anhängen)
`SourceTimestamp(Base)` nach dem Muster der bestehenden Klassen (`PgUUID(as_uuid=True)`,
`server_default=func.gen_random_uuid()`, `TIMESTAMP(timezone=True)`, `ForeignKey("source.id")`).
**Keine** bestehende Klasse anfassen.

### `src/wortlaut/store/timestamps.py` (neu)
- `insert_source_timestamp`: `session.add(...)`, `flush()`, `commit()`, gibt die `id` zurück;
  `IntegrityError` **propagiert** an den Aufrufer (Muster `insert_source`).
- `list_sources_without_timestamp`: `select(Source.id, Source.content_hash, Source.raw_bytes_ref)`
  mit `.where(~exists().where(SourceTimestamp.source_id == Source.id))`, stabil sortiert nach
  `Source.created_at, Source.id`, optionales `.limit(limit)`. Gibt `list[PendingSource]` zurück.
- `get_timestamps_for_source`: alle Zeilen zu einer `source_id`, sortiert nach `created_at`.

### `src/wortlaut/pipeline/timestamp.py` (neu)
`timestamp_source(pending, *, session, worm, stamper)`:
1. `raw = await worm.get(pending.raw_bytes_ref)` — **jeder** Fehler ⇒
   `TimestampOutcome("worm_missing", …)` + WARNING (Muster `pipeline/verify.py`).
2. `if content_hash(raw) != pending.content_hash:` ⇒ `TimestampOutcome("hash_mismatch", …)`,
   ERROR-Log mit `source_id` — **und der Stamper wird nicht aufgerufen** (AC10).
3. `result = await stamper.stamp(raw, content_hash=pending.content_hash)`; `TimestampError` ⇒
   `TimestampOutcome("tsa_failed", …, failures=getattr(stamper, "failures", ()) or (exc.label(),))`
   + WARNING.
4. `token_ref = await worm.put(f"{pending.content_hash}.{result.tsa_name}.tsr", result.token_der,
   content_type="application/timestamp-reply")`.
5. `insert_source_timestamp(...)`; `IntegrityError` ⇒ `await session.rollback()` und Status
   trotzdem `stamped` (paralleler Lauf war schneller — das Ergebnis ist dasselbe, AC12/§4.10).
6. Erfolg ⇒ `TimestampOutcome("stamped", pending.source_id, result.tsa_name,
   failures=<labels der übersprungenen TSAs>)`.

### `src/wortlaut/pipeline/verify.py` (ändern)
Die drei neuen Felder **ans Ende** der Dataclass mit Defaults (`"missing"`, `None`, `None`), damit
alle bestehenden positionalen Konstruktionen in Tests unverändert gültig bleiben.
Nach der bestehenden Hash-Prüfung — und **ohne** `ok`/`status` zu verändern (§3.4):
- `rows = await get_timestamps_for_source(session, source_id)`; leer ⇒ Felder bleiben auf
  `"missing"`/`None`.
- sonst **die erste** Zeile nehmen: `token = await worm.get(row.token_ref)`. Schlägt der WORM-Read
  fehl ⇒ `timestamp_status = "unreadable"` (**nicht** `"missing"`) + WARNING, `timestamp_tsa` wird
  trotzdem gesetzt.
  > **Warum ein eigener Status (Review-Nachtrag):** „Zeile existiert, Token nicht lesbar" und „nie
  > gestempelt" sind **nicht** dasselbe, und die Verwechslung wäre still und dauerhaft:
  > `list_sources_without_timestamp` geht nach **Zeilen-Existenz**, die Quelle ist also nicht mehr
  > „pending" und wird **nie wieder** gestempelt — während `/verify` „missing" meldet. Der
  > Betreiber ließe den Pass laufen, bekäme `pending=0` und hielte das für in Ordnung. Ein
  > zerstörter Nachweis muss als zerstörter Nachweis sichtbar sein.

  dann
  `verdict = verify_token(token, content_hash=source.content_hash, tsa_name=row.tsa_name)` und
  `timestamp_status/tsa/gen_time` daraus füllen.
- Die Rückgabe im Zweig `worm_missing` und `source_not_found` bleibt **unverändert** (kein
  Token-Lookup, wenn es keine source gibt).

### `src/wortlaut/serving/schemas.py` + `app.py` (ändern)
Drei Felder an `VerifyResult` **anhängen** (`timestamp_status: str`, `timestamp_tsa: str | None`,
`timestamp_gen_time: datetime | None`) und in `app.py` im `VerifyResult(...)`-Aufruf aus dem
`report` durchreichen. Sonst **nichts** in `serving` ändern. `datetime` importieren.

### `src/wortlaut/cli.py` (ändern)
- Subparser `timestamp` mit `--limit` (int, default None), `--no-migrate`, `--dry-run`.
- `main`: die Prüfung `subcommand != "ingest"` wird zu „`subcommand` muss in
  `{"ingest", "timestamp"}` liegen, sonst Meldung nach stderr + `return 2`"; die bestehende
  Fehlermeldung für den Ingest-Fall bleibt sinngemäß erhalten. Danach Dispatch auf `_run` bzw.
  `_run_timestamp`.
- `_run_timestamp(args)` als **eigene** Funktion (nicht in `_run` hineinbauen — `max-complexity 15`,
  `PLR0915`): Settings (`DbSettings`, `WormSettings`, `TimestampSettings`) im selben Muster laden
  (Fehler ⇒ Exit 2) · Engine + Sessionmaker · `upgrade_head` außer bei `--no-migrate` ·
  `worm.ensure_bucket()` · Stamper bauen:
  `FallbackTimeStamper([Rfc3161Tsa(load_profile(n), timeout_seconds=…) for n in settings.profile_names()])`
  (unbekannter Profilname ⇒ `ValueError` ⇒ Exit 2) · `pending = await list_sources_without_timestamp(…, limit=args.limit)` ·
  bei `--dry-run` nur `pending=<n>` ausgeben und 0 zurückgeben · sonst je Quelle `timestamp_source`
  in einer eigenen Session · Zähler + `Counter` über `outcome.failures` in **einem** Datenbündel
  (Muster `_RunStats`) · Circuit-Breaker auf aufeinanderfolgende `tsa_failed` (Exit 3) ·
  am Ende Summary + Exit **4**, wenn `hash_mismatch > 0`, sonst 0.
- Summary-Zeile:
  `pending=<n> stamped=<n> hash_mismatch=<n> worm_missing=<n> tsa_failed=<n> reasons=<label>=<k>,…`
  (`reasons=-` wenn leer, Sortierung wie in `_RunStats._ordered_reasons`).
- Bei `hash_mismatch` **je Vorkommen** eine Zeile nach stderr, die die `source_id` nennt.
- `finally`: Stamper und Engine schließen, jede Cleanup-Aktion einzeln in
  `contextlib.suppress(Exception)` (Muster `_run`).
- **Der bestehende `ingest`-Pfad wird nicht verändert** — nur die Subcommand-Auswahl davor.

### `.importlinter` (ändern — nur anhängen)
```ini
# Spec 0076: wortlaut.timestamp ist ein eigener Infrastruktur-Layer (wie wortlaut.archive)
# und importiert keinen anderen wortlaut-Layer — der gepinnte Trust-Anker darf nicht von
# Kern-, Store- oder Serving-Code abhängen.
[importlinter:contract:timestamp-ist-unabhaengig]
name = Timestamp-Layer importiert keinen anderen wortlaut-Layer
type = forbidden
source_modules =
    wortlaut.timestamp
forbidden_modules =
    wortlaut.ingest
    wortlaut.evidence
    wortlaut.store
    wortlaut.retrieval
    wortlaut.serving
    wortlaut.pipeline
    wortlaut.archive
```

### Tests (neu)
- **Keine** Netz-Calls, **keine** echten Wartezeiten. TSA-Antworten werden über einen
  `httpx.MockTransport` oder einen Fake-Client gemockt; als Antwort-Body dient das **echte**
  Fixture-Token aus `tests/fixtures/tsa/`.
- Die Fixture-Dateien werden über `pathlib.Path(__file__).parent.parent / "fixtures" / "tsa"`
  gelesen — **keine** absoluten Pfade, **keine** Netz-Downloads.
- Für AC2 wird der erwartete Hash verfälscht, indem **ein Zeichen** des Hex-Strings geändert wird
  (Länge bleibt 64).
- Für AC10/AC11 sind `WormStore` und `TimeStamper` schlanke Fakes im Test (Protokoll erfüllen,
  Aufrufe zählen) — kein MinIO im Unit-Test.
- `pytest.mark.integration` **nicht** verwenden (die Integrationstests legt der Architekt an).

## 12. Do-NOT (hart)
- KEINE git-, docker-, uv-, npm-, alembic- oder pytest-Befehle ausführen — nur das in §13.
- KEINE anderen als die in §10 genannten Dateien anlegen oder ändern. Insbesondere **NICHT**
  `pyproject.toml`, **NICHT** `uv.lock`, **NICHT** `src/wortlaut/pipeline/ingest.py`.
- KEINE Netz-Calls in Tests. KEIN echtes `asyncio.sleep`. KEINE neuen Fixture-Dateien erzeugen.
- KEIN Token persistieren, das nicht vorher verifiziert wurde (§4.3) — das ist der Kern von AC9.
- KEIN Root-/TSA-Zertifikat aus dem Token, aus ENV oder aus dem Netz beziehen. NUR die gepinnten
  PEMs aus `wortlaut/timestamp/trust/`.
- KEIN `verify_message` benutzen (nur `verify` mit dem fertigen Digest).
- `tsa_certificate(...)` am `VerifierBuilder` NIEMALS weglassen und NIEMALS durch `common_name(...)`
  ersetzen — ohne den Leaf-Pin akzeptiert die Prüfung fremde Tokens (§0a-🔴). Das ist der eine
  Fehler, der dieses Increment wertlos machen würde.
- KEIN Stempeln des Hex-Strings — gestempelt werden die Rohbytes (§4.1).
- KEIN UPDATE/DELETE auf `source`/`span`. KEINE Änderung an `ok`/`status` von `VerifyReport`.
- KEIN `timestamp_pending`-Feld, KEINE Spalte auf `source`.
- KEIN LLM, KEINE Secrets in Logs oder URLs, KEIN Pickle.
- KEINE erfundenen Feld-/Spalten-/Bibliotheksnamen — die Signaturen in §3 sind verifiziert;
  bestehende Dateien vorher lesen.

## 13. Abschluss (und NUR das an Kommandos ausführen)
- `git status --porcelain` ausgeben.
