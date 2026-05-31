# Spec — Data Extraction

> Inherits `_shared/CONVENTIONS.md`. Behavioural contract only ("what", not
> line-by-line "how" — that lives in `tasks.md`). Traces PTD §10 and PRD FR-7,
> NFR-1, NFR-6. Slug: `data-extraction`. Risk tier of the tool: `read`
> (proposes only; never writes a system of record).

## 1. Summary

Data Extraction is a **core service** plus one **MCP tool** (`document.extract`)
and one **prompt** (`extraction_schema`). Given a document (PDF/image) or an
inbound email body, it produces a typed **ExtractionProposal**: a set of fields
mapped to the normalised domain model, each with a confidence in `[0,1]`, source
provenance, and a `requires_verification` flag for low-confidence values.

Pipeline (PTD §10): **text layer** (pdfplumber / PyMuPDF) → **OCR fallback**
(Tesseract) per page where no usable text exists → **LLM structuring** through a
**provider-agnostic client** guided by `extraction_schema` → domain-model fields
+ per-field confidence → **threshold gating**. The service **proposes only**; it
never writes to a system of record. Inference is bound by a configurable
**residency allow-list** (fail-closed).

## 2. Scope & out-of-scope

**In scope:** text extraction; OCR fallback; provider-agnostic LLM structuring;
per-field confidence; threshold + low-confidence gating; domain-model mapping
(data-driven); the `document.extract` tool; the `extraction_schema` prompt;
inference residency/privacy config; page-coverage accounting; per-run metadata
audit.

**Out of scope:** intake/routing workflows (consumers); the concrete LLM
provider; QC check internals (`qc-verification`); document generation;
connector reads/writes to vendor systems; model training/hosting. See
`requirements.md §3`.

## 3. Domain types used / introduced

**Used (from PTD §4, do not redefine):** `Contact`, `Matter`, `Document`,
`Deadline`, `Communication`, `Task`. Extracted fields are proposals that map
onto these types' attributes; this service does not construct persisted records.

**Introduced (feature-local Pydantic v2 types; propose shared ones in
`platform-foundation` if reused):**

```python
class ExtractionSource(BaseModel):
    page: int | None                  # 1-based page index, None for email body
    method: Literal["text", "ocr", "llm"]   # how this value was obtained
    span: str | None                  # optional char range / bbox ref

class ExtractedField(BaseModel):
    key: str                          # dotted domain path, e.g. "contact.email"
    target_type: Literal["Contact","Matter","Document","Deadline",
                         "Communication","Task"]
    value: object | None              # parsed value (str/date/number/...)
    raw_text: str | None              # the literal text it was read from
    confidence: float                 # INVARIANT: 0.0 <= confidence <= 1.0
    requires_verification: bool       # confidence < threshold OR rule-forced
    sources: list[ExtractionSource]   # provenance (>=1 when value present)

class PageCoverage(BaseModel):
    total_pages: int
    text_pages: list[int]
    ocr_pages: list[int]
    skipped_pages: list[int]          # explicitly unprocessable, surfaced
    # INVARIANT: sorted(text ∪ ocr ∪ skipped) == [1..total_pages] (disjoint)

class ExtractionProposal(BaseModel):
    run_id: str
    input_ref: str                    # doc id / uri / email message ref
    input_checksum: str               # sha256 of input bytes (no content)
    fields: list[ExtractedField]
    page_coverage: PageCoverage
    structuring_status: Literal["ok","unavailable","skipped"]
    provider: str | None              # provider id used (none if not called)
    deterministic: bool               # True only when no LLM path was used
    warnings: list[str]
```

`object` denotes a JSON-serialisable value; concrete typing per `key` is driven
by the field-mapping config (§4), not hard-coded.

## 4. Interfaces (MCP tools/resources/prompts, ports, internal APIs)

### 4.1 MCP tool — `document.extract` (risk tier `read`)

Input (Pydantic):

```python
class ExtractInput(BaseModel):
    input_ref: str                    # doc id/uri OR email message ref
    content_kind: Literal["pdf","image","email_body"]
    mapping_profile: str | None       # which field-mapping config to apply
    threshold: float | None           # override default; must be in [0,1]
    structuring: bool = True          # allow LLM stage; False = text/OCR only
    async_ok: bool = True             # permit async job for OCR/LLM heavy runs
```

Output: `ExtractionProposal` (synchronous) **or** a job handle
`{run_id, status_resource}` when the work is deferred (large/scanned input).
The tool **validates → runs core → writes metadata audit → returns**. It never
invokes a connector write. `threshold` outside `[0,1]` ⇒ validation error.

### 4.2 Prompt — `extraction_schema`

A versioned prompt template that instructs the LLM to map raw text into the
domain model and **emit per-field confidence**. It declares: the target schema
(from the active `mapping_profile`), the required JSON output shape
(`ExtractedField[]`), an instruction to return `confidence ∈ [0,1]` per field,
and an instruction to leave unknown fields absent (not guessed). Prompt content
is data-driven by the mapping profile; immigration form specifics live in config
(`ASSUMPTION (confirm)` which profiles first).

### 4.3 Internal service interfaces (ports — swappable)

```python
class TextExtractor(Protocol):
    def extract(self, content: bytes, kind: str) -> tuple[dict[int, str], PageCoverage]: ...

class OcrEngine(Protocol):
    def ocr(self, content: bytes, pages: list[int]) -> dict[int, tuple[str, float]]: ...
    # returns text + per-page OCR confidence

class LLMStructuringClient(Protocol):           # PROVIDER-AGNOSTIC
    def structure(self, text: str, schema: MappingProfile,
                  prompt: str) -> list[ExtractedField]: ...
    @property
    def provider_id(self) -> str: ...
    @property
    def endpoint(self) -> str: ...               # checked against residency allow-list

class FieldMapper(Protocol):
    def to_domain(self, fields: list[ExtractedField],
                  profile: MappingProfile) -> list[ExtractedField]: ...
```

The `LLMStructuringClient` is the **one swappable seam** for the provider. A
`ResidencyGuard` wraps it: before any call it checks `endpoint`/region against
the configured allow-list and **fails closed** if not permitted.

### 4.4 Config (12-factor; from `platform-foundation`)

- `EXTRACTION_DEFAULT_THRESHOLD` (default `ASSUMPTION (confirm): 0.80`).
- `EXTRACTION_PROVIDER` + provider creds (secret store).
- `EXTRACTION_RESIDENCY_ALLOWLIST` — endpoints/regions permitted for inference.
- `EXTRACTION_ALLOW_EXTERNAL_INFERENCE` (default `false` until confirmed).
- `EXTRACTION_OCR_LANGS`, `EXTRACTION_MAX_PAGES`, `EXTRACTION_MAX_BYTES`.
- Mapping profiles (data-driven field maps) by name.

## 5. Behaviour & flows (happy path + state transitions)

**Happy path (native PDF):**
1. Validate input; resolve `mapping_profile` + effective `threshold`.
2. **Text stage:** extract text layer per page (pdfplumber/PyMuPDF). Pages with
   usable text → `text_pages`.
3. **OCR stage:** for pages with no usable text layer → Tesseract → `ocr_pages`,
   capturing per-page OCR confidence. Truly unprocessable pages → `skipped_pages`
   (surfaced, never dropped).
4. **Coverage check:** assert `text ∪ ocr ∪ skipped == all pages`, disjoint.
5. **Structuring stage** (if `structuring=True` and provider available): pass
   combined text + active schema + `extraction_schema` prompt through
   `ResidencyGuard → LLMStructuringClient` → `ExtractedField[]` with confidence.
6. **Mapping stage:** `FieldMapper` maps fields onto domain paths/types.
7. **Gating stage:** for each field set `requires_verification = confidence <
   threshold` (or rule-forced true for sensitive keys).
8. Build `ExtractionProposal`; set `deterministic = (no LLM stage ran)`.
9. Write **metadata audit** record (no raw content); return proposal.

**Run state transitions (async path):** `received → extracting_text →
ocr → structuring → mapping → gating → completed`; on fatal error →
`failed` (parked with typed error, never silent). Large/scanned inputs run
async with a status resource; small native PDFs may complete synchronously.

**Email body input:** skip page logic; `content_kind="email_body"`; coverage has
`total_pages=0`; provenance `page=None`.

## 6. Edge cases & error handling (incl. ConnectorError handling)

| Case | Handling |
|---|---|
| Empty / zero-byte input | Validation error; no stages run. |
| PDF with partial text layer | Per-page split: text where present, OCR elsewhere; coverage reflects both. |
| OCR yields empty text for a page | Page recorded in `ocr_pages`; field absent rather than fabricated; warning added. |
| Page truly unprocessable (corrupt) | Added to `skipped_pages`, surfaced in coverage + warning; run still completes. |
| Provider unavailable / timeout | `structuring_status="unavailable"`; return text + coverage; mark fields empty; graceful degradation (DX-NFR-8). |
| Non-allow-listed endpoint / external inference denied | **Fail closed:** `ResidencyError`; no outbound content call; run `failed` with typed reason. |
| `threshold` out of `[0,1]` | Input validation error (tool rejects before running). |
| LLM returns confidence outside `[0,1]` or NaN | Clamp to `[0,1]`; if unrecoverable, set field `requires_verification=true` and add warning; never propagate invalid confidence. |
| LLM hallucinates a field not in text | Mapping/validation drops unmapped keys; flagged via warning; absent ⇒ not proposed. |
| Oversized input (> max pages/bytes) | Reject with typed limit error (`ASSUMPTION (confirm)` limits). |
| Duplicate run for same checksum+config (non-LLM) | Deterministic output; safe to repeat (idempotent text path). |

This service does **not** call vendor connectors, so it raises its own typed
errors (`ResidencyError`, `UnsupportedInputError`, `ProviderUnavailableError`,
`InputLimitError`) rather than `ConnectorError`. Callers translate as needed.

## 7. Risk tiers & gates for each action

| Action | Risk tier | Gate |
|---|---|---|
| `document.extract` | **`read`** | None — proposes only; no side effect on any system of record. |
| Sending content to inference provider | n/a (internal) | **Residency allow-list, fail-closed** (policy gate, not human gate). |
| Persisting extracted fields into a system of record | **not in this spec** | Owned by the consuming workflow as `write (confirm)` / `gated (human)` per its own risk tier. Low-confidence fields MUST remain blocked until human-verified. |

The engine produces the **inputs** to a gate (the `requires_verification`
flags); it never clears one.

## 8. Data & persistence

- **No system-of-record writes.** The proposal is returned to the caller.
- **Run record (Postgres, via `platform-foundation`):** `run_id`, `input_ref`,
  `input_checksum`, `provider`, `threshold`, field counts,
  `structuring_status`, `deterministic`, timing, `page_coverage` summary, status.
  **No raw content, no PII, no field values.**
- **Transient content** (bytes, intermediate text) is held only for the duration
  of the run; not persisted to durable stores. If async staging is required,
  store encrypted with short TTL (`ASSUMPTION (confirm)` retention window).
- **Audit (hash-chained, per CONVENTIONS §7):** one metadata record per run.

## 9. Observability (logs/metrics/traces for this feature)

- **Spans** per stage: `extract.text`, `extract.ocr`, `extract.structuring`,
  `extract.mapping`, `extract.gating`, correlated by `run_id`.
- **Metrics:** runs total/success/failed; OCR-fallback rate; structuring
  unavailable rate; mean per-field confidence; low-confidence flag rate;
  residency-denied count; stage latencies; pages skipped count.
- **Logs:** structured, PII-scrubbed; record counts/decisions, **never** raw
  text or field values. Residency denials logged as security events.

## 10. Security & privilege considerations

- **Residency (NFR-6, NFR-1):** inference endpoint checked against allow-list;
  fail-closed; `EXTRACTION_ALLOW_EXTERNAL_INFERENCE=false` by default. This is
  the central security control for the feature.
- **Privilege/PII (NFR-1):** documents may be privileged / contain immigration
  PII; content encrypted in transit + at rest; redacted from logs/traces; never
  in telemetry. No raw content in audit or run records.
- **Secrets (NFR-8):** provider keys from central store at runtime; never logged.
- **Least privilege:** the tool has no write capability to any system of record
  by construction (architectural, not just policy).
- See `security-audit-prep.md` for the full surface/threat mapping.

## 11. Dependencies & integration points

- **`platform-foundation`** — domain model, persistence, audit, config/secrets,
  observability, security baseline. (Hard dependency.)
- **Consumers:** `client-intake#…`, `document-routing#…` call `document.extract`
  and own any subsequent gated persistence.
- **`qc-verification`** consumes `confidence` + `requires_verification`.
- **External libs:** pdfplumber/PyMuPDF, Tesseract, provider SDK (behind the
  swappable client), Pydantic v2, structlog (PTD §3 baseline).

## 12. Test strategy (focused tests + pointer to PBT)

- **Focused tests (examples):** native-PDF-no-OCR; scanned-PDF-triggers-OCR;
  mixed text/scan page split; email-body path; provider-unavailable degradation;
  residency-denied fail-closed; threshold override validation; redaction of
  logs; metadata-only audit; oversized-input rejection.
- **Property-based tests:** see `pbt-properties.md` — confidence range,
  low-confidence always flagged, no-system-of-record-write, deterministic
  non-LLM idempotency, page coverage completeness/disjointness.
- Provider is mocked behind `LLMStructuringClient`; OCR/text via small fixtures.

## 13. Open questions

- LLM provider + whether client data may leave the firm boundary
  (`ASSUMPTION (confirm)`).
- Default threshold value and global-vs-per-field configurability.
- Priority document/form types and their mapping profiles.
- Sync vs async latency budgets and async staging retention/TTL.
- How text/OCR-layer confidence combines with LLM-layer confidence into one
  per-field score.
- Supported input formats and size/page limits.
