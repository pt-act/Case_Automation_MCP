# Security Audit Prep — Client Intake

> Slug: `client-intake` · Wave 1. Re-checks this feature's surfaces against the
> shared baseline in `../_shared/CONVENTIONS.md §7` (the floor, not the ceiling)
> and PTD §12. Maps threats to NFR-1, NFR-2, NFR-4, NFR-6, NFR-8.

## Sensitive surfaces (data, actions, external calls)

**Data at rest / in flight:**
- `LeadPayload` — raw inbound lead; may carry immigration PII **before** a matter
  exists (first-touch PII). Confidential from `received`.
- `IntakeFields` — immigration PII: `a_number`, `dob`, `country_of_origin`,
  `current_status`, passport/bio data, address, prior filings.
- Run record (inputs/outputs per step), `DedupeResult`, `gaps` — persisted for
  audit/resume; carry PII.
- The welcome `Communication` draft — client-facing, privileged content.

**Actions (with risk tier):**
- `parse_lead`, `dedupe_contact`, `compute_deadlines` — `read`.
- `create_contact`, `create_matter`, `deadline.schedule`, `open_tasks` —
  `write (confirm)`.
- `draft_welcome` — `draft only`.
- `send_welcome` (`email.send`) — `gated (human)`, the only external/irreversible
  client-facing action.

**External calls (via ports only, vendor-agnostic):**
- `CRMConnector.find_contact` / `upsert_contact`, `CaseConnector.create_matter`,
  `EmailConnector.create_draft` / `send`.
- Cross-tool: `document.extract` (data-extraction), `deadline.compute/schedule`
  (deadline-engine) — PII may transit to extraction/LLM inference (residency-relevant).
- Inbound: webhook new-lead trigger (signature-verified upstream, PTD §6).

## Threats & mitigations (map to NFR-1, 2, 4, 6, 8)

| Threat | Surface | Mitigation | NFR |
|---|---|---|---|
| PII leak to logs/telemetry/traces | every step | log field **names/counts** only; PII scrubber on structlog/OTel; no client content in telemetry | **NFR-1** |
| PII at rest exposure | run record, `IntakeFields` | AES-256 at rest; A-number/passport/DOB/status encrypted; least-privilege DB access | **NFR-1** |
| Premature / unauthorised client send | `send_welcome` | default-on human gate before any `email.send`; no code path sends pre-approval; exactly-once idempotency | **NFR-4** |
| Auto-merge of wrong identities (privilege/PII cross-contamination) | `dedupe_contact` | ambiguous never auto-merges → blocking gap + human disambiguation | **NFR-1, NFR-4** |
| Privileged content routed to wrong recipient | `draft_welcome`/`send_welcome` | matter privileged on create; recipient-integrity check (recipient ∈ matter participants) before send | **NFR-1** |
| Missing/incomplete audit of state changes | all write + gate steps | hash-chained audit record written **before** step completes; gate approver + decision recorded; exportable | **NFR-2** |
| PII residency violation via extraction/LLM inference | cross-tool calls | inference provider is provider-agnostic + region-configurable (CONVENTIONS §4); residency is a deployment config; document data flow | **NFR-6** |
| Credential exposure for CRM/Case/Email ports | external calls | secrets from central store, runtime-injected, never logged, never in source; no env-dumping | **NFR-8** |
| Duplicate writes/sends from retries or webhook replays | write steps, send | run-level + `(run_id, step)` idempotency keys; webhook `provider_event_id` dedupe | **NFR-2, NFR-4** |
| Unauthorised invocation of `intake.run` or gate approval | tool + gate | RBAC scope on tool + approval; agent under constrained service identity | **NFR-1, NFR-4** |

## AuthZ & privilege checks

- **RBAC:** `intake.run` restricted to intake/case roles; gate approval
  restricted to the matter's responsible attorney (fallback intake-coordinator
  role) — **ASSUMPTION (confirm)** (`requirements.md §8`).
- **Constrained service identity:** the AI agent invokes intake under a
  least-privilege identity; cannot approve its own gate (separation of duties).
- **Connector least privilege:** CRM/Case/Email ports hold minimum OAuth scopes
  (find/create/draft/send) — no broader access (CONVENTIONS §7).
- **Privilege from creation:** `Matter.privileged = true` at create; privilege
  carried into routing/QC checks so the welcome cannot be sent to a non-participant.

## Audit log coverage

- One hash-chained record per state-changing step: `create_contact`,
  `create_matter`, `deadline.schedule`, `open_tasks`, `draft_welcome`,
  **gate decision** (approver + approve/reject), `send_welcome`.
- Each record: `actor, action, inputs (PII-free / referenced not inlined),
  outputs (ids), approval, timestamp, run_id` (CONVENTIONS §7).
- Record written **before** the step is considered complete (NFR-2); chain
  integrity verifiable; export supported for review.
- Read steps (`parse_lead`, `dedupe_contact`) logged for observability but the
  state-changing audit chain covers 100% of writes (NFR-2).

## PII handling & residency

- **Classification:** immigration PII (A-number, passport, DOB,
  country-of-origin, status, biometrics) treated as heightened-sensitivity
  (CONVENTIONS §7).
- **Minimisation in logs:** log field names/counts and `run_id`; never PII values.
- **Encryption:** TLS in transit; AES-256 at rest; OAuth tokens column-encrypted.
- **Residency:** data store + any LLM inference region configurable
  (GDPR-aware, configurable residency — CONVENTIONS §4 / NFR-6); document where
  lead PII flows during extraction.
- **Retention/subject handling:** intake run records inherit
  `platform-foundation` retention + data-subject (GDPR) handling; rejected/abandoned
  leads' PII subject to the same retention policy — **ASSUMPTION (confirm)**
  retention window for non-converted leads.

## Pre-audit checklist

- [ ] No PII value appears in any log, trace, metric label, or error message
      (scrubber verified against `IntakeFields`).
- [ ] All `IntakeFields` PII columns encrypted at rest; tokens column-encrypted.
- [ ] `send_welcome` unreachable unless gate state is `approved`; verified by
      property test (`pbt-properties.md` #2).
- [ ] Ambiguous dedupe never creates/merges a contact (property #5).
- [ ] Every write + gate step emits a hash-chained audit record before completing;
      chain verifies; export works.
- [ ] RBAC enforced on `intake.run` and gate approval; agent cannot self-approve.
- [ ] Connector scopes are least-privilege; no secrets in source/logs; secret
      store rotation-friendly.
- [ ] Recipient-integrity check enforced before any send (recipient ∈ matter
      participants).
- [ ] Idempotency verified: double-submit + webhook replay create no duplicate
      contact/matter/send (property #1).
- [ ] Data-residency config documented for storage + extraction/LLM inference.
- [ ] All `ASSUMPTION (confirm)` items (intake fields, case types, required
      fields, checklists, approver, retention) logged for firm sign-off before
      go-live (PRD §10 legal/compliance sign-off).
