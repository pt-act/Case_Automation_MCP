# Requirements — Document Generation

> Feature slug: `document-generation` · Wave 2 · Traces: PRD **FR-5**, **FR-9**;
> **NFR-3**. Inherits `_shared/CONVENTIONS.md` (process, domain model, risk
> tiers, compliance baseline) and PTD §9 (document generation pipeline).

---

## 1. Context & problem

Fee-earners and paralegals lose hours per matter hand-filling templates
(engagement letters, standard immigration filings, routine correspondence) by
copy-pasting scattered matter/contact data, then re-checking names and dates by
eye (PRD §2). Version chaos and silent blanks reach clients and, worse, get
filed. This feature turns a **template + domain data** into a **versioned,
checksummed DOCX/PDF**, where any field that cannot be filled from data is
surfaced as an explicit **gap** for a human to complete — never a silent blank
(PRD FR-9, PTD §9).

It is the rendering-and-gap engine only. It depends on `qc-verification` for the
actual completeness/consistency checks, and on `connector-framework`'s
`DocStoreConnector` port for storage. It does not classify or file the result
(that is `document-routing`).

This is a Wave 2, write-path capability: outputs are *generated and stored as
drafts* (`write (confirm)`), never auto-sent or auto-filed.

---

## 2. In scope

- **Template model** — a declared template with its required variables, optional
  variables, and metadata (format, target form id, privilege default). Templates
  are **data, not code** (CONVENTIONS §5, §8).
- **Render pipeline** — template (DOCX or HTML + Jinja2 vars) + domain data →
  render (docxtpl / python-docx + Jinja2) → DOCX → (WeasyPrint *or* LibreOffice
  headless) → PDF (PTD §3, §9).
- **Gap detection** — every required template variable that is unresolved from
  the supplied domain data is surfaced as a structured **gap**; rendering of a
  document with open gaps is blocked from being treated as complete (FR-9).
- **QC integration** — after render, call `qc.verify` (from `qc-verification`)
  for completeness (all vars resolved) and consistency (names/dates consistent);
  a QC `fail` blocks the output from being marked ready (FR-13).
- **Versioning + checksum** — each stored output gets a monotonically increasing
  `Document.version` and a content `checksum`; nothing overwrites silently
  (PTD §9, NFR-3).
- **Store via DocStore port** — persist bytes + `Document` metadata through the
  `DocStoreConnector` port (PTD §6); the adapter itself is out of scope.
- **MCP surface** — the tools `document.generate` and `form.prefill`, and the
  resource `template://{name}` (template + required variables) (PTD §5).

## 3. Out of scope

- The **DocStore adapter** (any vendor implementation of `DocStoreConnector`) —
  owned by `connector-framework` / future vendor work.
- **QC internals** — the check implementations live in `qc-verification`; this
  feature only calls `qc.verify` and reacts to its verdict.
- The **template library contents** — the actual engagement letters / form
  templates and their variable sets. `ASSUMPTION (confirm)`.
- **Document routing** — classification, naming, filing to the correct
  folder/ACL, and recipient permissioning are `document-routing`.
- **Data extraction** — pulling field values from PDFs/forms is `data-extraction`;
  this feature consumes already-structured domain data.
- **Email / sending / filing** of the generated document (separate gated actions).
- **E-signature** orchestration (e.g. DocuSign event handling).

---

## 4. Users / actors

| Actor | Need from this feature |
|---|---|
| **Paralegal / Case Manager** | One call turns matter data into a near-final draft; clearly told what is missing. |
| **Fee-earner / Attorney** | Trustworthy, consistent output; gaps surfaced not hidden; audit trail; control before anything leaves. |
| **The AI Agent** | Predictable typed tools (`document.generate`, `form.prefill`) and a `template://` resource describing required variables. |
| **Operations / Admin** | Add/replace templates as data without code changes; observability into render/QC failures. |

---

## 5. Functional requirements (trace to PRD FR-xx)

- **DG-FR-1 (FR-5)** Given a template name and a resolved data context derived
  from a `Matter`/`Contact`, the system renders a DOCX.
- **DG-FR-2 (FR-5)** The system converts the rendered DOCX to PDF via a
  configurable engine (WeasyPrint for HTML templates; LibreOffice headless for
  DOCX). Engine choice is configurable. `ASSUMPTION (confirm)` default engine.
- **DG-FR-3 (FR-9)** Each template **declares** its required and optional
  variables; the `template://{name}` resource exposes them.
- **DG-FR-4 (FR-9)** Every required variable not satisfied by the supplied data
  is surfaced as a structured **gap** (variable name, human label, why-missing);
  the output is marked `incomplete` and never silently blank.
- **DG-FR-5 (FR-9)** `form.prefill` returns the resolved fields **and** the open
  gaps for a target immigration form, for human completion — it does not invent
  values.
- **DG-FR-6 (FR-5)** Generated outputs are stored via the `DocStoreConnector`
  port and returned as a `Document` with `version` and `checksum`.
- **DG-FR-7 (FR-13)** After render, the feature runs `qc.verify` for completeness
  and consistency; a `fail` blocks marking the document ready.
- **DG-FR-8 (FR-5)** Versioning is monotonic per `(matter_id, template,
  logical_doc_key)`; a new generation never overwrites an existing version.
- **DG-FR-9 (FR-15)** Every generation writes an audit record (actor, template,
  inputs digest, output checksum/version, gaps, QC verdict, approval state).
- **DG-FR-10 (FR-17)** Both tools self-describe via Pydantic schemas; the
  template resource is self-describing.

## 6. Non-functional requirements (trace to PRD NFR-xx)

- **DG-NFR-1 (NFR-3)** **Idempotent + deterministic.** Re-running generation
  with identical template version + identical input context yields byte-identical
  output (same checksum) and does **not** create a spurious new version; an
  idempotency key prevents duplicate stored versions on retry.
- **DG-NFR-2 (NFR-1)** Confidentiality/privilege: generated documents carry a
  `privileged` classification; template-embedded PII is handled under the
  baseline (encryption at rest, no client content in logs/telemetry).
- **DG-NFR-3 (NFR-2)** 100% of generations (state-changing stores) are logged
  immutably to the hash-chained audit log before being considered complete.
- **DG-NFR-4 (NFR-4)** Generation is `write (confirm)`; the output is a stored
  **draft**. No external/irreversible action (send/file) happens here.
- **DG-NFR-5 (NFR-6)** PII inside templates and outputs is handled per
  configurable residency; object-store location honours residency config.
- **DG-NFR-6 (NFR-5)** Render+convert+QC+store runs async with status for large
  documents; small documents return inline within typical latency budget.
- **DG-NFR-7 (NFR-7)** Structured logs/metrics/traces per generation (render ms,
  convert ms, QC verdict, gap count) correlated by `run_id`.
- **DG-NFR-8 (NFR-8)** No secrets in templates or code; the converter and store
  read credentials from the central secret store.

---

## 7. Dependencies (other feature specs, external systems)

- **`platform-foundation`** — domain model (`Document`, `Matter`, `Contact`),
  persistence, hash-chained audit log, config/secrets, observability, the
  provider-agnostic LLM/client abstractions (not required by this feature's core
  path).
- **`connector-framework`** — the `DocStoreConnector` port (`put`/`get`),
  `ConnectorError` taxonomy, idempotency-key plumbing.
- **`qc-verification`** — the `qc.verify` check registry; this feature consumes
  the completeness and consistency checks and reacts to the verdict.
- **External** — a templating engine (docxtpl / python-docx + Jinja2), a PDF
  engine (WeasyPrint or LibreOffice headless), and an S3-compatible / firm doc
  store behind the port (PTD §3).

## 8. Assumptions & open questions

- `ASSUMPTION (confirm)`: **Which immigration forms `form.prefill` targets
  first** — e.g. **I-130, I-485, N-400, G-28**. Each target form is template
  *data* with a declared variable set, not core code (CONVENTIONS §5, §8).
- `ASSUMPTION (confirm)`: **Template library contents** (engagement letter,
  cover letters, G-28, etc.) and each template's required/optional variable set
  are supplied by the firm; specs treat them as data.
- `ASSUMPTION (confirm)`: **Default PDF engine** — LibreOffice headless for
  fidelity of DOCX layouts, WeasyPrint for HTML templates. Confirm whether DOCX
  or HTML is the primary authoring format.
- `ASSUMPTION (confirm)`: **Logical document key** used to group versions
  (proposed: `(matter_id, template_name, optional caller-supplied doc_key)`).
- `ASSUMPTION (confirm)`: **Default privilege classification** for generated
  documents is `privileged = true` unless the template metadata says otherwise.
- `ASSUMPTION (confirm)`: Whether `form.prefill` outputs a fillable PDF/DOCX or
  only a structured field+gap map for downstream `document.generate`.
- `ASSUMPTION (confirm)`: Numeric/date/locale formatting rules (US date format,
  name ordering) to guarantee render determinism and consistency checks.
- **Open:** Maximum template/output size and async threshold (ties to NFR-5).
- **Open:** Whether template version pinning is required for reproducibility of
  historical documents (proposed: yes — store template version in `Document`).

## 9. Acceptance criteria (testable checklist)

- [ ] `template://{name}` returns the template's declared required and optional
      variables, format, and target form id (if any).
- [ ] `document.generate` with a complete data context returns a stored
      `Document` with `version ≥ 1`, a non-empty `checksum`, and `gaps == []`.
- [ ] A missing required variable produces a structured gap (name + label +
      reason); the output is marked `incomplete` and contains **no** silent blank
      where the variable appeared.
- [ ] Re-running `document.generate` with identical template version + identical
      input context returns the **same checksum** and does **not** create a new
      version (idempotent; see DG-NFR-1).
- [ ] A new generation for an existing `(matter, template, doc_key)` yields
      `version = previous + 1` and never overwrites the prior version's bytes.
- [ ] Stored bytes returned by the DocStore port match the rendered bytes
      (checksum verifies on read-back).
- [ ] When all required variables resolve, `qc.verify` completeness returns
      `pass`; a QC `fail` leaves the document `blocked` (not `ready`).
- [ ] `form.prefill` for an `ASSUMPTION (confirm)` target form returns resolved
      fields plus open gaps and never fabricates a value.
- [ ] Every generation writes exactly one audit record with template, input
      digest, output checksum/version, gaps, and QC verdict.
- [ ] No client PII or secret appears in logs/traces for any generation.
- [ ] Generation is `write (confirm)`; no send/file side effect occurs.
