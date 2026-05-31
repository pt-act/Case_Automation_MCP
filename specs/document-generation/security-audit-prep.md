# Security Audit Prep — Document Generation

> Feature slug: `document-generation` · Phase 4. Re-checks this feature's
> surfaces against the inherited baseline in `_shared/CONVENTIONS.md §7` and
> PTD §12. This is the floor, not the ceiling. Threats map to PRD
> **NFR-1** (confidentiality/privilege), **NFR-2** (auditability), **NFR-4**
> (human control), **NFR-6** (residency/PII), **NFR-8** (secret hygiene).

---

## 1. Sensitive surfaces (data, actions, external calls)

| Surface | What flows through it | Sensitivity |
|---|---|---|
| **Resolved data context** | Matter/Contact PII bound into templates: names, DOB, A-numbers, passport numbers, country-of-origin, immigration status, biometrics references | **High** — immigration PII + privileged |
| **Template source** | Template bytes; may embed boilerplate + variable slots; must contain no secrets/credentials | Medium |
| **Rendered DOCX/PDF bytes** | Full client-facing document content | **High** — privileged work product |
| **`document.generate` / `form.prefill` tools** | Caller-supplied `matter_id`, `data_context`, `form_id` | High (write-path; matter-scoped) |
| **`template://{name}` resource** | Declared variable metadata (labels/hints), no values | Low–Medium |
| **`DocStoreConnector.put`** (external call) | Document bytes + metadata leaving to object store | **High** — residency + encryption critical |
| **`qc.verify` call** | Rendered artefact + resolved/declared variables | High |
| **Audit record** | Input **digest**, output checksum/version, gaps, QC verdict | Medium (must not hold raw PII) |
| **Logs / metrics / traces** | Counts, timings, status — **never** values/bytes | Must stay PII-free |
| **Gap list** | Variable names + human labels (not values) | Low–Medium |

---

## 2. Threats & mitigations (map to NFR-1,2,4,6,8)

| # | Threat | Mapped NFR | Mitigation |
|---|---|---|---|
| T1 | Privileged generated document later routed/sent to an external recipient | NFR-1 | Set `Document.privileged` from template metadata at generation (default `true`, `ASSUMPTION (confirm)`); downstream `document-routing`/send gates enforce privilege; this feature never sends/files. |
| T2 | Immigration PII (A-number, passport, DOB) leaks into logs/traces/metrics | NFR-1, NFR-6 | PII-scrubbing structlog processor; log only counts/timings/status; assert-no-PII test; no document bytes or resolved values logged. |
| T3 | Raw PII persisted in the audit log | NFR-2 | Audit stores a salted **digest** of the resolved context, output checksum/version, gaps, verdict — not raw values. |
| T4 | Output bytes stored outside the configured residency region | NFR-6 | Store only via `DocStoreConnector` with residency-configured backend; verify region config at deploy; encryption at rest (AES-256). |
| T5 | LLM/agent fabricates a legally-significant value instead of surfacing a gap | NFR-4, NFR-1 | Gaps are surfaced, never auto-filled; `form.prefill` returns gaps and never invents; no generative fill in the render path. |
| T6 | Silent overwrite of an existing document version (loss of work product / tampering) | NFR-2, NFR-3 | Monotonic atomic version ledger + unique constraint; idempotency-key collision with differing content is refused and audited; prior bytes immutable. |
| T7 | Unauthorised generation against a matter the caller cannot access | NFR-1 | RBAC matter-scoping; agent runs under a constrained service identity; tool authorises `matter_id` before resolving any data. |
| T8 | Secrets (store/converter credentials) embedded in a template or leaked | NFR-8 | Credentials only from the central secret store at runtime; template validation rejects credential-shaped content; never logged. |
| T9 | Malicious template performs side effects / code execution (template injection) | NFR-1, NFR-8 | Templates are **data**: render in a sandboxed Jinja2 environment with autoescape and no arbitrary callables; reject non-deterministic/side-effecting templates at load (task 1.4). |
| T10 | A generated draft is treated as approved/sent without a human step | NFR-4 | Generation is `write (confirm)`; only `status=ready` is eligible downstream; no external/irreversible action here; downstream send/file is `gated (human)` via all three approval channels (CONVENTIONS §4, §6). |
| T11 | PDF converter subprocess (LibreOffice headless) used as an exfiltration/RCE vector | NFR-1, NFR-8 | Run converter with no network, least-privilege, time/resource limits; treat its output bytes as untrusted until checksummed; pin engine version. |
| T12 | Tampering with the audit chain to hide a generation | NFR-2 | Append-only hash-chained audit (CONVENTIONS §7); chain verification in CI/runbook. |

---

## 3. AuthZ & privilege checks

- **Matter-scoped RBAC** — `document.generate`/`form.prefill` authorise the
  caller against `matter_id` *before* loading any Matter/Contact data; deny with
  no data leakage on failure.
- **Constrained service identity** — the agent acts under a least-privilege
  identity; the DocStore port holds least-privilege scopes (write to the matter's
  store location only).
- **Privilege classification** — every generated `Document` carries a
  `privileged` flag (from template metadata; default `true`, `ASSUMPTION
  (confirm)`); downstream features enforce it. This feature sets, never clears, it
  silently.
- **Template access** — `template://{name}` exposes metadata only (no values);
  still subject to role checks if templates are themselves sensitive.

## 4. Audit log coverage

- **One hash-chained record per generation** (success **and** `failed`), written
  *before* the generation is considered complete (NFR-2, CONVENTIONS §7).
- **Fields:** `actor`, `action` (`document.generate`/`form.prefill`),
  `matter_id`, `template_name` + `template_version`, **input digest** (not raw
  PII), output `checksum` + `version`, `gaps` (names/reasons), `qc_verdict`,
  `idempotency_key`, `status`, `run_id`, timestamp.
- **Excluded:** raw resolved values, document bytes, secrets.
- **Verifiable:** chain links to the prior record; exportable for review.

## 5. PII handling & residency

- **In transit:** TLS for all port/QC/store calls.
- **At rest:** AES-256 for stored documents (object store) and any DB metadata;
  template-embedded PII inherits the same.
- **Residency:** output bytes stored only in the residency-configured backend
  (NFR-6); inference, if any LLM is used elsewhere, is out of this feature's core
  path — none is used to fill values here.
- **Minimisation:** audit/logs hold digests/counts, not raw PII; gap labels are
  generic ("Date of birth"), not the values.
- **Immigration specifics:** A-numbers, passport numbers, biometrics references,
  and country-of-origin are treated as the highest-sensitivity class and never
  appear in telemetry (CONVENTIONS §7).

## 6. Pre-audit checklist

- [ ] PII-scrubbing verified: no resolved values, names, A-numbers, or document
      bytes in logs/metrics/traces (automated assert-no-PII test).
- [ ] Audit record written for every generation (success and `failed`); stores
      digest not raw PII; chain verifies.
- [ ] `Document.privileged` set per template metadata on every output; default
      `true` confirmed (`ASSUMPTION (confirm)`).
- [ ] RBAC matter-scoping enforced before any data load; unauthorised access
      denied with no leakage.
- [ ] Version ledger monotonic + atomic; no silent overwrite; collision refused.
- [ ] Stored bytes == rendered bytes (checksum verified on read-back).
- [ ] Object store backend matches configured residency region; encryption at
      rest confirmed.
- [ ] Jinja2 render sandboxed (autoescape, no arbitrary callables); non-
      deterministic/side-effecting templates rejected at load.
- [ ] PDF converter runs network-isolated, least-privilege, resource-limited;
      engine version pinned.
- [ ] No secrets in templates or code; credentials only from the secret store;
      env-dumping commands never used.
- [ ] Generation is `write (confirm)` only; no send/file side effect; only
      `ready` is eligible downstream (NFR-4).
- [ ] Gaps are surfaced, never auto-filled; `form.prefill` fabricates nothing.
