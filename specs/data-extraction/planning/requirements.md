# Requirements — Data Extraction

> Inherits `_shared/CONVENTIONS.md` (process, domain model, risk tiers,
> compliance baseline, glossary). Traces to `concept/PRD.md` and
> `concept/PTD.md §10`. Feature slug: `data-extraction`. Wave 2.

## 1. Context & problem

Today, staff read PDFs, scanned forms, and inbound email and re-type the values
into the case management and CRM systems — the "Read PDF → type into system"
swivel-chair pain in PRD §2. It is slow, error-prone, and an attorney-client
privilege / immigration-PII exposure point.

Data Extraction provides one engine and one MCP tool that turns a document (or
an inbound email body) into **structured fields mapped to the normalised domain
model**, each carrying a **per-field confidence score in [0,1]**. The pipeline
is: native-PDF text layer (pdfplumber / PyMuPDF) → OCR fallback (Tesseract) for
scans/images → LLM structuring through a **provider-agnostic** client guided by
the `extraction_schema` prompt → domain-model fields + confidence (PTD §10).

The engine **proposes only**. It never writes to a system of record. Fields
below a configurable confidence threshold are **flagged for human verification
before** they can enter any system of record. Downstream workflows (intake,
routing) consume the proposal; they are out of scope here.

## 2. In scope

- Text extraction from native/digital PDFs (text layer).
- OCR fallback for scanned PDFs and image inputs (PNG/JPEG/TIFF).
- LLM structuring of raw text into domain-model fields via a **swappable
  provider interface** (provider not yet chosen).
- The `extraction_schema` prompt that guides structuring into the domain model.
- Per-field confidence scoring in `[0,1]`.
- Configurable confidence thresholding and **low-confidence gating** (flag for
  human verification; never silently accepted into a system of record).
- Mapping of extracted fields to the shared domain model (PTD §4) with field
  provenance (page / source span where available).
- The `document.extract` MCP tool (risk tier `read` — proposes, does not write).
- Privacy/residency configuration for **where inference runs** and **whether
  client content may leave the firm boundary** (NFR-1, NFR-6).
- Page-coverage accounting so no input page is silently dropped between the text
  and OCR stages.

## 3. Out of scope

- The workflows that **consume** extraction output (client intake, document
  routing) — they depend on this spec, not the reverse.
- The **specific LLM provider** choice — the interface is provider-agnostic; the
  concrete adapter is deferred (`ASSUMPTION (confirm)`).
- QC internals (consistency/completeness checks) — owned by `qc-verification`;
  this spec only emits confidence + flags that QC and workflows consume.
- Document generation, classification-for-filing, and ACL/routing decisions
  (`document-generation`, `document-routing`).
- Connector reads/writes to vendor systems — extraction receives bytes/text and
  returns a proposal; persistence to systems of record is a workflow concern.
- Training / fine-tuning models; model hosting/provisioning.

## 4. Users / actors

| Actor | Interest |
|---|---|
| Paralegal / Case Manager | Stop re-keying; trust that low-confidence fields are surfaced, not silently committed. |
| Fee-earner / Attorney | Privilege preserved; auditable provenance; control over what reaches a system of record. |
| Intake Coordinator | Turn an inbound lead document/email into structured fields fast. |
| Operations / Admin | Configure thresholds, provider, and residency policy; observe extraction health. |
| The AI Agent | Calls `document.extract`; receives a typed proposal + confidence to reason over. |

## 5. Functional requirements (trace to PRD FR-xx)

- **DX-FR-1** Accept a document reference or inbound email body and extract a
  text layer from native PDFs without OCR where a text layer exists. *(FR-7)*
- **DX-FR-2** Fall back to OCR (Tesseract) for pages/images with no usable text
  layer; record per-page which path produced the text. *(FR-7)*
- **DX-FR-3** Structure raw text into domain-model fields via a provider-agnostic
  LLM client guided by the `extraction_schema` prompt. *(FR-7)*
- **DX-FR-4** Return a **per-field confidence in `[0,1]`** for every extracted
  field. *(FR-7, FR-13)*
- **DX-FR-5** Apply a **configurable confidence threshold**; mark every field
  below threshold `requires_verification = true`. *(FR-7, FR-13, FR-14)*
- **DX-FR-6** Never write to a system of record; return a **proposal object**
  only. Persisting/committing is the caller's gated responsibility. *(FR-14, NFR-4)*
- **DX-FR-7** Account for **page coverage**: the union of pages handled by the
  text and OCR stages MUST equal the input page set; any unprocessable page is
  reported explicitly (never silently dropped). *(FR-7)*
- **DX-FR-8** Map fields to the shared domain types (`Contact`, `Matter`,
  `Document`, `Deadline`, `Communication`, `Task`) using configurable, data-driven
  field mappings — no hard-coded immigration form logic in core. *(FR-2, FR-7)*
- **DX-FR-9** Expose the engine as the `document.extract` MCP tool (risk tier
  `read`) and ship the `extraction_schema` prompt. *(FR-7, FR-17)*
- **DX-FR-10** Make the inference provider swappable behind one interface;
  selecting/configuring a provider is deployment config, not a code change. *(FR-16)*
- **DX-FR-11** Record an audit entry for each extraction run (no raw client
  content in the record) so the action is traceable. *(FR-15)*
- **DX-FR-12** Deterministic non-LLM path: identical input bytes + identical
  config produce identical text-extraction + page-coverage output. The LLM
  structuring path is explicitly marked **non-deterministic**. *(NFR-3)*

## 6. Non-functional requirements (trace to PRD NFR-xx)

- **DX-NFR-1** **Residency / privacy of inference (NFR-6, NFR-1):** client
  content MUST only be sent to an **allow-listed** inference endpoint. If the
  configured provider/region is not allow-listed, the run fails closed (no call)
  with a typed error. Whether client data may leave the firm boundary is a
  per-deployment policy.
- **DX-NFR-2** **Confidentiality / privilege (NFR-1):** documents may be
  privileged or contain immigration PII (A-numbers, passports, biometrics,
  status, country-of-origin). Content is encrypted in transit/at rest; redacted
  from logs/traces; never placed in telemetry.
- **DX-NFR-3** **Auditability (NFR-2):** every extraction run writes a metadata
  audit record (actor, doc id/checksum, provider, thresholds, counts,
  timestamp, run_id) — without raw content.
- **DX-NFR-4** **Reliability / idempotency (NFR-3):** the deterministic
  (non-LLM) path is idempotent on identical input + config.
- **DX-NFR-5** **Latency (NFR-5):** small native-PDF extraction returns within
  the read-tool budget where feasible; OCR + LLM runs MAY exceed it and run as an
  async job with status. `ASSUMPTION (confirm)` numeric budgets.
- **DX-NFR-6** **Observability (NFR-7):** structured logs + spans per stage
  (text / OCR / structuring), PII-scrubbed, correlated by `run_id`.
- **DX-NFR-7** **Secret hygiene (NFR-8):** provider API keys read from the
  central secret store at runtime; never in source or logs.
- **DX-NFR-8** **Graceful degradation (NFR-9):** if the LLM provider is
  unavailable, the engine still returns extracted text + page coverage and marks
  structuring as unavailable, rather than failing the whole run.

## 7. Dependencies (other feature specs, external systems)

- **`platform-foundation`** (hard dependency): domain model, persistence, audit
  log, config & secrets, observability, security baseline.
- **External libraries** (PTD §3 baseline): pdfplumber / PyMuPDF, Tesseract,
  the LLM provider SDK behind the swappable interface, Pydantic v2, structlog.
- **Consumers (downstream, not dependencies of this spec):** `client-intake`,
  `document-routing` call `document.extract`.
- **`qc-verification`** consumes the `requires_verification` flags / confidence
  but is not required for this engine to function.

## 8. Assumptions & open questions

- `ASSUMPTION (confirm):` **LLM provider** is not chosen; the interface is
  provider-agnostic and the concrete adapter is deferred.
- `ASSUMPTION (confirm):` **Whether client data may leave the firm boundary** for
  inference (cloud provider vs in-VPC/self-hosted model). Default policy until
  confirmed: **deny external** — only an allow-listed in-boundary endpoint.
- `ASSUMPTION (confirm):` **Default confidence threshold** value (proposed
  `0.80`) and whether it is global or per-field-type configurable.
- `ASSUMPTION (confirm):` **Priority document/form types** to extract first
  (e.g. I-130, I-485, N-400, G-28, passports, EAD cards). Field mappings are
  data-driven config, so this changes config, not core code.
- `ASSUMPTION (confirm):` **Input formats** beyond PDF/PNG/JPEG/TIFF (e.g. DOCX,
  HEIC) and max file size / page count.
- `ASSUMPTION (confirm):` **OCR language packs** required (English plus likely
  Spanish and others for immigration documents).
- `ASSUMPTION (confirm):` **Latency budgets** for sync vs async execution.
- `ASSUMPTION (confirm):` How confidence is derived for the **text/OCR layer**
  (e.g. OCR engine confidence) vs the **LLM layer**, and how they combine into a
  single per-field score.

## 9. Acceptance criteria (testable checklist)

- [ ] `document.extract` accepts a supported input and returns a typed proposal
      with `fields[]`, each having `value`, `confidence ∈ [0,1]`, `source`
      (page/path), and `requires_verification`.
- [ ] Native PDF with a text layer is extracted **without** invoking OCR.
- [ ] A scanned/image PDF page with no text layer triggers OCR for that page.
- [ ] Every returned field's `confidence` is within `[0,1]` (no NaN/None/out-of-range).
- [ ] Every field with `confidence < threshold` has `requires_verification = true`.
- [ ] The proposal is returned without any write to a system of record (no
      connector write is invoked by the tool).
- [ ] Page coverage report: `set(text_pages) ∪ set(ocr_pages) ∪ set(skipped_pages)`
      equals the full input page set, and `skipped_pages` is surfaced explicitly.
- [ ] Identical input bytes + identical config produce byte-identical text +
      identical page-coverage output (deterministic non-LLM path).
- [ ] A non-allow-listed inference endpoint causes the run to fail closed with a
      typed residency error and **no** outbound content call.
- [ ] Logs/traces for a run contain no raw document content and no PII (verified
      by redaction test).
- [ ] An audit record is written per run containing metadata only (no raw content).
- [ ] With the LLM provider unavailable, the engine still returns text + page
      coverage and marks structuring unavailable (graceful degradation).
