# Requirements — Status Update Emails

> Feature slug: `status-update-emails` · Wave 1 · Traces: FR-10, FR-6; NFR-2, NFR-4.
> Inherits `_shared/CONVENTIONS.md` (process, domain model, risk tiers, compliance
> baseline) and the architecture in `concept/PTD.md`. This file is the "what & why".

## 1. Context & problem

When an immigration matter changes status (e.g. a petition is filed, USCIS issues
a receipt, biometrics are scheduled, an RFE is received, a case is approved), the
client needs a timely, accurate, on-brand update. Today these updates are
hand-written, inconsistent, and sometimes forgotten — causing client anxiety and
complaints (PRD §2, success metric "status updates sent on time > 95% within
SLA"). This feature detects a matter status change, drafts a tailored client
update using the `status_update_email` prompt over a computed **matter delta**,
runs a recipient-integrity quality check, routes the draft through a human
approval gate, then sends and logs it — exactly once per status change.

This is a Wave 1 workflow: high frequency, high client-satisfaction, low blast
radius because the send is always gated by a human (PRD §7 Wave 1, PTD §17).

## 2. In scope

- The status-change workflow end to end: trigger → delta → draft → QC → gate →
  send → log.
- Triggering from a webhook status-change `Event` (FR-3) **and** from a schedule
  (a periodic sweep that detects status changes not delivered via webhook).
- Computing the **matter delta** (old status → new status, plus the minimal
  supporting context the email needs).
- Drafting via the `email.draft` MCP tool through the Email **port** using the
  `status_update_email` prompt (FR-6, FR-10).
- Using the **recipient-integrity** QC check (from `qc-verification`) as a gate
  input before any send.
- The human **approval gate** (`gated (human)`, default-on) surfaced via all
  three channels (MCP tool callback, web UI, email action).
- Send via `email.send` after approval, then write the audit record and update
  the `Communication` status (FR-15, NFR-2).
- **Idempotency:** exactly one update sent per distinct status change, even under
  duplicate/replayed webhook events, scheduler overlap, or retries (NFR-3).

## 3. Out of scope

- The **email connector adapter** (vendor-specific create-draft/send) — defined
  in `connector-framework`; this spec depends on the Email port only.
- **QC check internals** — the recipient-integrity check logic lives in
  `qc-verification`; this spec consumes its result as a gate input.
- **Template / brand authoring** — the content/tone of the `status_update_email`
  prompt and any brand templates are authored separately; this spec consumes the
  prompt by name and version.
- The **orchestration engine itself** (state machine, gate mechanics, idempotency
  primitives) — provided by `workflow-orchestration`; this spec composes it.
- Multi-channel client messaging beyond email (SMS, portal) — not in v1.
- Bulk/backfill sends for historic status changes that predate go-live.

## 4. Users / actors

| Actor | Interest |
|---|---|
| **Paralegal / Case Manager** | Wants updates drafted automatically and sent on time without re-keying. |
| **Fee-earner / Attorney** | Owns matter & risk; approves/rejects drafts; needs audit trail and control. |
| **Client (recipient)** | Receives a correct, on-brand update about *their* matter only. |
| **The AI Agent** | May invoke `workflow.run` for this workflow; reads draft/QC results. |
| **Operations / Admin** | Configures the schedule sweep, gate routing, and SLA targets. |

## 5. Functional requirements (trace to PRD FR-xx)

- **R1 (FR-10, FR-3)** Detect a matter status change from (a) an inbound
  normalised status-change `Event` (webhook) and (b) a scheduled sweep that
  compares last-known vs current status per matter.
- **R2 (FR-10)** Compute a `MatterStatusDelta` capturing `matter_id`,
  `from_status`, `to_status`, the change source, a stable `change_id`, and the
  minimal supporting context required by the draft.
- **R3 (FR-6, FR-10)** Produce an email **draft** (not a send) via `email.draft`
  through the Email port, rendered with the `status_update_email` prompt over the
  delta. Draft is `Communication(status="draft")`.
- **R4 (FR-13 via `qc-verification`)** Run the **recipient-integrity** QC check
  (recipient ∈ matter participants; correct client) and attach its `pass/warn/fail`
  result to the run as a gate input. A `fail` blocks the gate.
- **R5 (FR-14, NFR-4)** Park the run at a **human approval gate** (`gated (human)`,
  default-on) surfaced via all three approval channels. Approve → proceed; reject
  → terminate without send; edit-then-approve supported.
- **R6 (FR-10)** On approval, **send** via `email.send` and transition the
  `Communication` to `sent`.
- **R7 (FR-15, NFR-2)** Write an immutable audit record for every state-changing
  step (draft created, QC result, approval decision, send) before the step is
  considered complete.
- **R8 (NFR-3)** Enforce **one send per status change**: a repeated or replayed
  status event for the same `change_id` never produces a second draft-and-send.
- **R9 (FR-3)** Both trigger paths converge on the **same** workflow run via the
  orchestrator (no divergent logic per trigger).

## 6. Non-functional requirements (trace to PRD NFR-xx)

- **NFR-2 (Auditability)** 100% of state-changing actions (draft, QC, approval,
  send) logged immutably with actor/inputs/outputs/approval/timestamp/run_id.
- **NFR-4 (Human control)** No send without an explicit, recorded approval; gate
  default-on; the send is the only `gated (human)` action here.
- **NFR-1 (Confidentiality / privilege)** Recipient integrity enforced so one
  client's update can never reach another; immigration PII never leaked
  cross-matter; no client content in logs/telemetry.
- **NFR-3 (Reliability)** Idempotent: safe retries, no duplicate sends under
  webhook replay or scheduler overlap.
- **NFR-5 (Latency)** Draft generation runs async with status; gate dwell time is
  human-bound and excluded from latency targets.
- **NFR-7 (Observability)** Structured logs, metrics, and traces per run
  (correlation id = `run_id`).

## 7. Dependencies (other feature specs, external systems)

- `platform-foundation` — domain model (`Matter`, `Communication`, `Contact`),
  hash-chained audit log, config/secrets, observability.
- `connector-framework` — Email port (`create_draft`, `send`), webhook ingestion
  + event normalisation + dedupe, `ConnectorError` taxonomy.
- `workflow-orchestration` — durable state machine, gates, idempotency keys,
  retry/compensation, triggers (webhook + schedule).
- `qc-verification` — recipient-integrity check (consumed as a gate input).
- External: the Email system of record (via adapter, out of scope) and the Case
  system of record (source of matter status, read through the Case port).

## 8. Assumptions & open questions (each `ASSUMPTION (confirm):`)

- `ASSUMPTION (confirm):` Which **status values** in the firm's case system should
  trigger a client update, and which are internal-only (no client email). Until
  confirmed, treat the trigger set as **configurable allow-list**, defaulting to
  empty (no auto-trigger) so nothing client-facing fires unconfigured.
- `ASSUMPTION (confirm):` Whether some status changes map to **no template /
  suppress** (e.g. purely internal reclassification) — modelled as a per-status
  policy in config.
- `ASSUMPTION (confirm):` The **SLA window** for "sent on time" (PRD success
  metric > 95% within SLA). Default placeholder: draft created within 15 min of
  the status change; send subject to human gate.
- `ASSUMPTION (confirm):` Whether the **client always** is the recipient, or
  whether a designated representative / G-28 attorney-of-record / interpreter may
  also be cc'd. Recipient set is derived from `Matter` participants + a
  configurable role policy.
- `ASSUMPTION (confirm):` The **scheduled-sweep cadence** (e.g. every 15 min) and
  whether webhook is the primary path with sweep as a safety net.
- `ASSUMPTION (confirm):` How **`status_update_email` prompt versioning** is
  pinned per environment (a workflow run records the prompt version used).
- `ASSUMPTION (confirm):` Whether a `warn` (not `fail`) recipient-integrity result
  should still require an explicit acknowledgement at the gate (default: surfaced
  but does not auto-block).
- Open: does the firm want **per-matter opt-out** of automated status emails?
  Modelled as a matter-level flag if confirmed.

## 9. Acceptance criteria (testable checklist)

- [ ] A normalised status-change `Event` whose `to_status` is in the configured
      trigger allow-list starts exactly one workflow run.
- [ ] A status change whose `to_status` is **not** in the allow-list starts **no**
      run and sends **no** email.
- [ ] The same status change delivered twice (duplicate webhook id or
      webhook+sweep overlap) results in exactly **one** draft and **one** send.
- [ ] The run computes a `MatterStatusDelta` with non-empty `from_status`,
      `to_status`, `matter_id`, and a stable `change_id`.
- [ ] A draft `Communication(status="draft")` is created via the Email port
      before any send is attempted.
- [ ] The recipient-integrity QC result is attached to the run; a `fail` blocks
      the gate (no send possible).
- [ ] No `email.send` occurs without a recorded `approval` record referencing the
      run; reject terminates the run with no send.
- [ ] Every state-changing step writes an audit record before completion;
      exporting the run's audit shows draft → QC → approval → send in order.
- [ ] A recipient never resolves outside the matter's participant set (enforced
      before send).
- [ ] On `email.send` success the `Communication` transitions to `sent` and the
      `change_id` is marked delivered so re-triggering is a no-op.
