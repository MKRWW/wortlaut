# Increment-Spec: fetch — PDF-Validierung + Redirect-Guard (#25)

- **Story/Issue:** #25 · **Status:** Reviewed · **Phase/Layer:** phase/0 · `ingest`
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#6** (`DipPlenarprotokollAdapter.fetch`). **Blocker vor #7.**

## 1. Ziel
`fetch` soll **beweisen**, dass die geholten Bytes wirklich das amtliche PDF sind —
sonst hart failen. Damit kann der Kern in #7 niemals ein Redirect-Ziel, eine Captcha-
oder 200-Fehlerseite still als „application/pdf"-Protokoll hashen/archivieren
(Beweis-Integrität; externes #6-Review, Security §3.1/§3.2).

## 2. Nicht-Ziele (Scope-Grenze)
- **Kein** Cursor-Pagination-Fix (das ist #27), **kein** API-Key-Header-Umbau (#26).
- **Kein** PDF-*Parsing* (Phase 1) — nur die **Magic-Byte-/Header-Validierung**, dass es ein PDF ist.
- **Kein** Verändern von `discover`.

## 3. Betroffene Interfaces / Öffentliche Signaturen
```python
# src/wortlaut/ingest/dip.py — fetch wird gehärtet (Signatur UNVERÄNDERT: async def fetch(ref) -> RawSource)
class DipFetchError(Exception):
    """Fetch lieferte etwas anderes als ein direkt geliefertes, gültiges PDF."""

# Client-Konfiguration: follow_redirects=False + Timeout (Backpressure, Security §3.1)
#   httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(30.0))

# fetch-Ablauf (nach dem bestehenden Host-Pinning):
#   response = await client.get(ref.origin_url)
#   - 3xx (Redirect):     raise DipFetchError, Location wird geloggt   (kein stilles Folgen)
#   - status != 200:      raise DipFetchError
#   - Bytes ohne %PDF-  :  raise DipFetchError                          (Magic-Byte-Check)
#   - Content-Type nicht application/pdf (falls Header vorhanden): raise DipFetchError
#   - sonst: RawSource(..., mime_type="application/pdf")               (Positivfall)
```
- **Öffentliche Signatur von `fetch` bleibt gleich**; neu ist die Fehlerklasse `DipFetchError` und das
  strengere Verhalten. `mime_type` bleibt `"application/pdf"`, ist jetzt aber **verifiziert**, nicht behauptet.
- **Layering (R-ARCH-02):** unverändert — `ingest` importiert keinen Kern-Layer.

## 4. Design (kurz)
- **Redirect-Policy = fail-loud (konservativer Default, Architekt-Entscheidung):** `follow_redirects=False`;
  ein 3xx auf einer amtlichen PDF-URL ist unerwartet → `DipFetchError` + `Location` loggen, **nicht** still
  folgen. *Begründung:* Der Anker muss exakt das sein, was die gepinnte URL **direkt** liefert; ein Redirect
  wäre zudem eine SSRF-Fläche. **Falls** Live-Tests zeigen, dass DIP legitim (z. B. auf CDN) redirectet, ist
  die Erweiterung „folgen + finalen Host erneut gegen die Allowlist pinnen" ein kleiner Follow-up — der sichere
  Default failt bis dahin **laut**, nicht **still**.
- **Magic-Byte-Check:** die Bytes müssen mit `%PDF-` beginnen. Primärkriterium (robust gegen falsche/fehlende
  Content-Type-Header). Content-Type wird zusätzlich geprüft, wenn der Header vorhanden ist.
- **Timeout am Client** (Backpressure, Security §3.1) — kleine, hier gebündelte Härtung, weil wir die
  Client-Konfiguration ohnehin anfassen.
- **`pdf_host`-Allowlist:** bleibt wie #6 (Annahme `dserver.bundestag.de`); die Live-Verifikation (evtl. mehrere
  legitime PDF-Hosts) ist Teil des Live-Checks mit API-Key, nicht dieses Increments.

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)
- [ ] **AC1** *Given* ein 3xx-Response (mit `Location`), *When* `fetch`, *Then* `DipFetchError` (kein stilles
      Folgen), **kein** `RawSource`. `[unit]`
- [ ] **AC2** *Given* Status ≠ 200 (z. B. 200-Erwartung verletzt / 4xx / 5xx), *When* `fetch`, *Then*
      `DipFetchError`. `[unit]`
- [ ] **AC3** *Given* Status 200, aber Bytes beginnen **nicht** mit `%PDF-` (z. B. HTML-Captcha-Seite),
      *When* `fetch`, *Then* `DipFetchError`, **kein** `RawSource`. `[unit]`
- [ ] **AC4** *Given* Status 200 + `%PDF-`-Bytes, aber `Content-Type` vorhanden und ≠ `application/pdf`,
      *When* `fetch`, *Then* `DipFetchError`. `[unit]`
- [ ] **AC5** *Given* Status 200 + Bytes beginnen mit `%PDF-` (Content-Type `application/pdf` oder fehlend),
      *When* `fetch`, *Then* `RawSource` mit genau diesen Bytes (Positivfall). `[unit]`
- [ ] **AC6** *Given* der interne httpx-Client, *When* erzeugt, *Then* `follow_redirects` ist `False` **und**
      ein Timeout ist gesetzt. `[unit]`
> Jedes AC ist von einem automatisierten Test mit Ja/Nein beantwortbar.

## 6. Testplan (Test-zu-AC-Mapping)
- **Unit (rein, httpx gemockt), NEUE Datei:** `tests/unit/test_dip_fetch_validation.py`
  - `test_fetch_rejects_redirect` → AC1 · `test_fetch_rejects_non_200` → AC2
  - `test_fetch_rejects_non_pdf_bytes` → AC3 · `test_fetch_rejects_wrong_content_type` → AC4
  - `test_fetch_accepts_valid_pdf` → AC5 · `test_client_no_follow_redirects_and_timeout` → AC6
- Die bestehenden #6-Tests (`test_dip_adapter.py`) bleiben grün (Positiv-Fetch nutzt jetzt gültige `%PDF-`-Bytes).

## 7. Recht / Security
- **Beweis-Integrität (Kern-Wert):** der PDF-Anker muss **verifiziert** echt sein, nicht nur behauptet —
  sonst hasht/archiviert #7 Fremdbytes als Protokoll.
- **Redirect-Guard = SSRF-relevant (R-SEC-05):** kein stilles Folgen auf einen nicht kontrollierten Host.
- **Timeout/Backpressure (Security §3.1).** Keine Live-Calls im Test (R-TEST-03).

## 8. Risiken & offene Fragen / Entscheidungen
- **Entscheidung:** Redirect = **fail-loud** (kein Folgen). Relaxen zu „folgen + Host-Re-Pinning" nur, falls die
  Live-DIP-API legitim redirectet (dann Follow-up). Der sichere Default schadet nie: kein Redirect → alles gut;
  Redirect → laut statt still.
- **Content-Type ist sekundär:** manche Server liefern generische Typen → Magic-Byte-Check ist das harte Kriterium.

## 9. Definition of Done (Verweis)
[../docs/rules.md](../docs/rules.md) DoD: alle AC grün (Unit), alle Gates grün (Lint·Type·Test·Coverage ≥80,
Security, Architektur, SonarCloud), Review (Architekt + **Security**, Beweis-/SSRF-Pfad), keine Live-Calls,
kein Secret. PR referenziert **#25**.
