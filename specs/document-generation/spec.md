# Spec — Document Generation

> Feature slug: `document-generation` · Wave 2 · Phase 2 (behavioural contract).
> Traces PRD **FR-5**, **FR-9**, **FR-13**, **FR-15**, **FR-17**; **NFR-1/2/3/4/6/7/8**.
> Inherits `_shared/CONVENTIONS.md` and PTD §9. Describes the *what*; tasks live
> in `tasks.md`.

---

## 1. Summary

Render a declared template plus normalised matter/contact data into a versioned,
checksummed DOCX (and PDF), surfacing every unresolved required variable as an
explicit **gap** rather than a silent blank, running QC for completeness and
consistency, and storing the result through the `DocStoreConnector` port as a
`write (confirm)` draft `Document`. Exposes `document.generate` and
`form.prefill` tools and a `template://{name}` resource. Templates are data, not
code.

---

## 2. Scope & out-of-scope

**In scope:** template model (declared required/optional vars + metadata); render
pipeline DOCX→PDF; gap detection; QC integration (completeness/consistency);
versioning + checksum; store via `DocStoreConnector`; `form.prefill`; the two
tools + the `template://` resource.

**Out of scope:** the DocStore adapter implementation; QC check internals
(consumed from `qc-verification`); the actual template library contents
(`ASSUMPTION (confirm)`); document classification/filing/permissioning
(`document-routing`); data extraction (`data-extraction`); sending/filing the
output; e-signature.

---

## 3. Domain types used / introduced

**Used (from PTD §4 / `platform-foundation`, not redefined):** `Matter`,
`Contact`, `Document` (`version`, `checksum`, `classification`, `privileged`,
`mime_type`, `uri`, `matter_id`).

**Introduced (this feature; proposed for review in `platform-foundation` if
shared):**

```python
class TemplateVariable(BaseModel):
    name: str                      # Jinja2 variable path, e.g. "client.full_name"
    label: str                     # human label for gap surfacing
    required: bool = True
    type: Literal["string","date","number","bool","address","enum"]
    enum_values: list[str] | None = None
    source_hint: str | None = None # where the data normally comes from

class TemplateSpec(BaseModel):
    name: str                      # unique template id (resource key)
    version: int                   # template content version, pinned per render
    format: Literal["docx","html"]
    target_form_id: str | None     # e.g. "I-130" — ASSUMPTION (confirm)
    privileged_default: bool = True
    variables: list[TemplateVariable]
    checksum: str                  # checksum of the template source bytes

class Gap(BaseModel):
    variable: str                  # TemplateVariable.name
    label: str
    reason: Literal["missing","empty","type_mismatch","enum_violation"]

class GenerationResult(BaseModel):
    document: Document | None       # None while status != "ready"/"draft_stored"
    status: Literal["ready","incomplete","blocked","failed"]
    gaps: list[Gap]
    qc_verdict: Literal["pass","warn","fail","skipped"]
    template_name: str
    template_version: int
    idempotency_key: str
    run_id: str
```

> Generated `Document.classification` defaults from `TemplateSpec.target_form_id`
> or the template name; `Document.privileged` defaults from
> `TemplateSpec.privileged_default` (`ASSUMPTION (confirm)`: default `true`).

---

## 4. Interfaces (MCP tools/resources/prompts, ports, internal APIs)

### 4.1 MCP tools

**`document.generate`** — risk tier **`write (confirm)`** (PTD §5.1).

- Input: `{ matter_id: str, template_name: str, data_context: dict | None,
  doc_key: str | None, idempotency_key: str | None, render_pdf: bool = true }`.
  - `data_context` is an optional explicit override map; when absent, the feature
    builds the context from the `Matter`/`Contact` (see §5).
- Output: `GenerationResult`.
- Behaviour: resolve context → detect gaps → render DOCX (+PDF) → `qc.verify` →
  store via port → audit. Status reflects gaps/QC (see §5).

**`form.prefill`** — risk tier **`write (confirm)`**.

- Input: `{ matter_id: str, form_id: str, data_context: dict | None,
  emit: Literal["fields_only","document"] = "fields_only" }`.
  - `form_id` ∈ the configured immigration form set (`ASSUMPTION (confirm)`:
    I-130, I-485, N-400, G-28).
- Output: `{ resolved_fields: dict, gaps: list[Gap], form_id, template_version }`
  when `emit="fields_only"`; otherwise a `GenerationResult` (delegates to
  `document.generate` with the form template).
- Behaviour: never fabricates values; unresolved required fields become gaps.

### 4.2 MCP resource

**`template://{name}`** — read-only. Returns the `TemplateSpec` (declared
required + optional variables, format, target form id, version, checksum).
Used by the agent to learn what data a template needs before generating.

### 4.3 Internal ports / services

- **`DocStoreConnector`** (from `connector-framework`, PTD §6): `put(doc,
  content) -> Document`. Used to persist bytes + metadata. Adapter out of scope.
- **`qc.verify`** (from `qc-verification`): called with the rendered artefact +
  resolved/declared variables; returns per-check `pass|warn|fail` + reasons.
- **`TemplateStore`** (internal): loads `TemplateSpec` + template bytes by
  `(name, version)`. Backed by config/template data (`ASSUMPTION (confirm)`
  storage location). Read-only here.
- **`Renderer`** (internal): `render(template_bytes, context) -> docx_bytes`
  (docxtpl/python-docx + Jinja2) and `to_pdf(docx_or_html_bytes) -> pdf_bytes`
  (WeasyPrint | LibreOffice headless). Must be deterministic (see §5, PBT).
- **`AuditLog`** (from `platform-foundation`): append hash-chained record.

> Each internal component should stay under ~400 LoC (CONVENTIONS §3.5). If
> `Renderer` grows past that, split into `DocxRenderer` and `PdfConverter`.

---

## 5. Behaviour & flows (happy path + state transitions)

**Happy path (`document.generate`):**

1. **Load template** `TemplateStore.get(template_name)` → `TemplateSpec` +
   bytes; pin `template_version`.
2. **Resolve context** — build a variable map from `Matter`/`Contact` (+ explicit
   `data_context` overrides). Apply deterministic formatting (dates, names,
   numbers) per config.
3. **Detect gaps** — for each `required` variable not satisfied (missing, empty,
   wrong type, enum violation) emit a `Gap`. (Optional variables never produce
   gaps.)
4. **Compute idempotency key** = `idempotency_key` if supplied, else
   `hash(template_name + template_version + canonical(resolved_context))`.
5. **Idempotency check** — if a stored version exists for this key, return that
   `Document` unchanged (no new version).
6. **Render DOCX** via `Renderer.render`. Unresolved required variables are
   rendered as a visible **gap placeholder token** (e.g. `[[MISSING: label]]`),
   never an empty string (FR-9). If `render_pdf`, convert to PDF.
7. **Checksum** the rendered primary artefact bytes.
8. **QC** `qc.verify` (completeness: all required vars resolved; consistency:
   names/dates consistent). Capture verdict.
9. **Version + store** — compute next `version` for `(matter_id, template_name,
   doc_key)`; call `DocStoreConnector.put`; receive `Document(version,
   checksum, uri)`.
10. **Audit** — write the generation record (inputs digest, output
    checksum/version, gaps, QC verdict, idempotency key, run_id).
11. **Return** `GenerationResult`.

**Status transitions (terminal status in `GenerationResult.status`):**

| Condition | status | stored? |
|---|---|---|
| No gaps **and** QC `pass`/`warn` | `ready` | yes (draft `Document`) |
| Gaps present (required var unresolved) | `incomplete` | yes, marked incomplete; version still created; gap placeholders visible |
| QC `fail` | `blocked` | yes, marked blocked; not eligible for downstream send/file |
| Render/convert error or fatal `ConnectorError` | `failed` | no document; audited as failed |

> Even `incomplete`/`blocked` outputs are stored and versioned so the human has a
> concrete artefact to complete/fix; they are **never** auto-advanced. Only
> `ready` is eligible to be picked up by `document-routing`/send flows.

**`form.prefill` flow:** load form template (by `form_id`) → resolve context →
gaps → if `emit="fields_only"` return `{resolved_fields, gaps}`; if
`emit="document"` delegate to `document.generate`. Never fabricates values.

---

## 6. Edge cases & error handling (incl. ConnectorError handling)

- **Unknown template / form id** → typed `TemplateNotFound`; tool returns an
  error result (no document, no version), audited.
- **Required variable missing** → gap, `status=incomplete`; **not** an error.
- **Type mismatch / enum violation** (e.g. a string where a date is required) →
  gap with `reason=type_mismatch`/`enum_violation`; no silent coercion.
- **Extra/unknown keys in `data_context`** → ignored; logged at debug (no PII).
- **PDF conversion failure** → if DOCX rendered, store DOCX with
  `status` reflecting partial output and a `warn`; surface convert error; do not
  fail the whole generation silently. `ASSUMPTION (confirm)`: store DOCX-only or
  mark `failed` when PDF is mandatory.
- **DocStore `ConnectorError`** taxonomy (PTD §6): `transient` → retry with
  backoff (tenacity) under the same idempotency key; `rate-limit` → backoff;
  `auth`/`fatal` → `status=failed`, park for human attention, audit; never
  double-store (idempotency key guards retries).
- **Idempotency-key collision with differing content** → treat as a defect:
  refuse to overwrite, return error, audit (must never silently overwrite an
  existing version — NFR-3).
- **Concurrent generations for same `(matter, template, doc_key)`** → version
  allocation must be atomic (DB sequence / row lock) so two runs cannot claim the
  same version; loser retries and gets the next version.
- **QC unavailable** → `qc_verdict=skipped` with a `warn`; document stored but
  **not** marked `ready` (cannot certify completeness without QC). `ASSUMPTION
  (confirm)`: block vs warn when QC is down.
- **Non-deterministic template** (uses `now()`, random) → flagged at template
  validation time; such templates are rejected because they break determinism
  (DG-NFR-1).

---

## 7. Risk tiers & gates for each action

| Action | Risk tier | Gate |
|---|---|---|
| `template://{name}` (read) | `read` | none |
| `document.generate` (render + store draft) | `write (confirm)` | single confirmation; reversible (versioned draft, nothing sent/filed) |
| `form.prefill` (`fields_only`) | `read`/`write (confirm)` | `read` when only returning fields; `write (confirm)` when `emit="document"` |
| `form.prefill` (`document`) | `write (confirm)` | as `document.generate` |

No action in this feature is `gated (human)` — nothing here is external or
irreversible. Sending/filing a generated document is a **separate** gated action
in another feature (NFR-4, CONVENTIONS §6). Approval, when downstream features
gate on a generated doc, is surfaced via all three channels (CONVENTIONS §4).

---

## 8. Data & persistence

- **`Document`** rows (via `platform-foundation` persistence + `DocStoreConnector`
  for bytes): `version`, `checksum`, `classification`, `privileged`, `mime_type`,
  `uri`, `matter_id`, plus this feature's metadata: `template_name`,
  `template_version`, `doc_key`, `idempotency_key`, `status`, `qc_verdict`.
- **Version ledger** — monotonic `version` per `(matter_id, template_name,
  doc_key)`, allocated atomically (DB sequence/unique constraint on
  `(matter_id, template_name, doc_key, version)`). Old versions immutable.
- **Idempotency** — unique index on `idempotency_key`; a repeat returns the
  existing `Document` (NFR-3).
- **Gaps** — persisted with the generation record for human follow-up and audit.
- **Templates** — `TemplateSpec` + bytes stored as **data** (config/object store,
  `ASSUMPTION (confirm)` location), versioned and checksummed; pinned per render
  for historical reproducibility.
- **Audit** — append-only hash-chained record per generation (CONVENTIONS §7).
- **Residency** — output bytes stored in the residency-configured object store
  (NFR-6).

---

## 9. Observability (logs/metrics/traces for this feature)

- **Logs** (structlog, `run_id` correlation, PII-scrubbed): template name+version,
  gap count, status, qc_verdict, idempotency hit/miss, version assigned. **No**
  resolved values, names, A-numbers, or document bytes in logs.
- **Metrics** (Prometheus): `docgen_render_ms`, `docgen_convert_ms`,
  `docgen_total_ms`, `docgen_gap_count`, `docgen_status_total{status}`,
  `docgen_qc_verdict_total{verdict}`, `docgen_idempotent_hits_total`,
  `docstore_put_errors_total`.
- **Traces** (OpenTelemetry): spans `load_template`, `resolve_context`,
  `detect_gaps`, `render_docx`, `convert_pdf`, `qc_verify`, `store`, `audit`.
- **Alerts:** spike in `failed`/`blocked`, DocStore put error rate, QC-down
  (`skipped`) rate.

---

## 10. Security & privilege considerations

- **Privilege classification** — generated documents default to `privileged`
  per template metadata (`ASSUMPTION (confirm)`: default `true`); this flag is
  what `document-routing` enforces against external recipients.
- **PII in templates/outputs** — immigration PII (A-numbers, passport numbers,
  DOB, country-of-origin, biometrics) commonly appears in these documents. It is
  encrypted at rest (object store + DB), never written to logs/traces/metrics,
  and stored under the configured residency (NFR-1, NFR-6, CONVENTIONS §7).
- **Input digest, not inputs** — the audit record stores a salted digest of the
  resolved context, not the raw PII, plus the output checksum (NFR-2).
- **AuthZ** — tools and matters scoped to roles; the agent runs under a
  constrained service identity; a caller may only generate for matters they may
  access (RBAC, CONVENTIONS §7).
- **Secrets** — converter/store credentials from the central secret store; never
  in templates or code; never logged (NFR-8).
- **No fabrication** — gaps are surfaced, never auto-filled by the LLM, so the
  feature cannot invent legally-significant values (FR-9).
- Full surface enumeration in `security-audit-prep.md`.

---

## 11. Dependencies & integration points

- **`platform-foundation`** — `Document`/`Matter`/`Contact`, persistence, audit
  log, config/secrets, observability.
- **`connector-framework`** — `DocStoreConnector.put`, `ConnectorError`
  taxonomy, idempotency plumbing.
- **`qc-verification`** — `qc.verify` completeness + consistency checks.
- **Consumed by (downstream):** `document-routing` (files/permissions a `ready`
  document), and send/status flows that attach generated documents.
- Cross-spec task references use slugs (e.g. `qc-verification#<id>`,
  `connector-framework#<id>`) in `tasks.md`.

## 12. Test strategy (focused tests + pointer to PBT)

- **Focused tests** (pytest): happy-path generate (no gaps → `ready`); missing
  required var → gap + placeholder + `incomplete`; QC `fail` → `blocked`;
  unknown template → error; PDF convert failure handling; DocStore transient
  error retried once under same idempotency key; `form.prefill` returns
  fields+gaps and never fabricates; `template://` returns declared variables.
- **Property-based tests** (Hypothesis) — see `pbt-properties.md`: no silent
  blank; monotonic non-overwriting versioning; checksum integrity; render
  determinism; all-vars-resolved ⇒ QC completeness pass; idempotency.
- **Contract tests:** a fake `DocStoreConnector` (respx-style in-memory) verifies
  `put` round-trip (stored bytes == rendered bytes).

## 13. Open questions

- `ASSUMPTION (confirm)`: priority target forms for `form.prefill` (I-130,
  I-485, N-400, G-28) and the template library contents.
- `ASSUMPTION (confirm)`: default PDF engine (LibreOffice vs WeasyPrint) and
  primary authoring format (DOCX vs HTML).
- `ASSUMPTION (confirm)`: behaviour when PDF is mandatory and conversion fails
  (store DOCX-only vs `failed`).
- `ASSUMPTION (confirm)`: behaviour when QC is unavailable (block vs warn).
- `ASSUMPTION (confirm)`: `doc_key` definition for the version ledger.
- `ASSUMPTION (confirm)`: default privilege classification (`true`).
- Open: async threshold / max document size (NFR-5).
