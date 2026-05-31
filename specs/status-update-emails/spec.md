# Spec — Status Update Emails

> Feature slug: `status-update-emails` · Wave 1 · Traces: FR-10, FR-6; NFR-2, NFR-4.
> Behavioural contract only ("what", not line-by-line "how" — that is `tasks.md`).
> Inherits `_shared/CONVENTIONS.md` and `concept/PTD.md`. Domain types are the
> shared PTD §4 model; risk tiers and gates are CONVENTIONS §6.

## 1. Summary

A durable, resumable workflow that turns a **matter status change** into an
on-brand client email, sent exactly once after human approval. The workflow is
registered with the orchestrator (`workflow-orchestration`) and triggered by a
normalised status-change `Event` (webhook, FR-3) or a scheduled sweep. It
computes a matter delta, drafts an email via the Email **port** using the
`status_update_email` prompt, runs the recipient-integrity QC check
(`qc-verification`) as a gate input, parks at a `gated (human)` approval, then
sends via the Email port and records the result. Idempotency guarantees one send
per status change (NFR-3); auditability and human control are non-negotiable
(NFR-2, NFR-4).

## 2. Scope & out-of-scope

**In scope:** the workflow definition and its steps (detect → delta → draft → QC
→ gate → send → log); both trigger paths; delta computation; consuming
`email.draft` / `email.send` through the Email port; consuming the
`status_update_email` prompt; consuming the recipient-integrity QC check;
idempotency keyed on the status change; audit writes; observability for this
workflow.

**Out of scope:** the Email connector **adapter** (vendor specifics — see
`connector-framework`); the **internals** of the recipient-integrity check (see
`qc-verification`); **template/brand authoring** of the prompt; the orchestration
**engine** itself (gates, idempotency primitives, persistence — see
`workflow-orchestration`); non-email channels; historic backfill.

## 3. Domain types used / introduced

**Used (from PTD §4 / `platform-foundation`, not redefined):**
- `Matter` — source of `status`, `client`, participants, `external_ids`,
  `practice_area`.
- `Communication` — the email itself: `direction="out"`, `channel="email"`,
  `status ∈ {draft, pending_approval, sent}`, `participants`.
- `Contact` — recipient resolution (client + any policy-permitted participants).

**Introduced (feature-local, proposed for reuse via `platform-foundation` if
other workflows need them):**
- `MatterStatusDelta` — `{ matter_id, from_status, to_status, change_id,
  source: Literal["webhook","sweep","manual"], detected_at, context: dict }`.
  `change_id` is a **stable, deterministic** identifier for the status change
  (see §8) and is the idempotency anchor for the whole feature.
- `StatusUpdateRunState` — feature-local run context persisted by the
  orchestrator: `{ run_id, delta, draft_comm_id, qc_result, approval, send_result }`.

> `ASSUMPTION (confirm):` whether `MatterStatusDelta` / `change_id` should be a
> shared platform type. Proposed as shared because `client-intake` and
> `deadline-engine` may also key off status transitions.

## 4. Interfaces (MCP tools/resources/prompts, ports, internal APIs)

### MCP tools consumed (not defined here; PTD §5.1)
- `email.draft` → Email port `create_draft(Communication) -> draft_id`. Risk tier
  **draft only**.
- `email.send` → Email port `send(draft_id, idem_key) -> message_id`. Risk tier
  **gated (human)**.
- `qc.verify` (recipient-integrity check) → returns `pass | warn | fail` + reason;
  consumed as a gate input.
- `workflow.run` / `workflow.status` — start/inspect this workflow (agent or
  sidecar).

### Prompt consumed (PTD §5.3)
- `status_update_email` — versioned prompt that renders an on-brand update from
  the `MatterStatusDelta`. The run records the exact prompt version used.

### Ports consumed (PTD §6)
- **Email port:** `create_draft`, `send` (idempotency-key aware).
- **Case port (read):** `get_matter(id)` to read current status/participants for
  delta + recipient resolution and the sweep comparison.

### Resources referenced (read-only, PTD §5.2)
- `matter://{id}` for the full matter view used to compute the delta and resolve
  recipients.

### Workflow registration (PTD §7)
The feature registers one workflow, e.g. `@workflow("status_update_email")`, with
steps roughly:
`detect_change → compute_delta → resolve_recipients → draft_email →
qc_recipient_integrity → GATE:human_review → send_email → record_outcome`.
The orchestrator owns persistence, gate mechanics, retry/compensation, and
idempotency keys per `(run_id, step)`.

## 5. Behaviour & flows (happy path + state transitions)

**Trigger (two paths, one run):**
1. **Webhook:** the sidecar receives a status-change event, the connector
   framework verifies + normalises + dedupes it into an `Event`, and enqueues a
   trigger. This feature's trigger handler maps the `Event` → `MatterStatusDelta`.
2. **Schedule:** a periodic sweep reads matters (Case port), compares last-known
   status (persisted) vs current; any difference yields a `MatterStatusDelta`
   with `source="sweep"`.

Both converge on `workflow.run("status_update_email", delta)`.

**Happy path (state transitions):**
1. `detect_change` — establish `change_id`; if `to_status` ∉ configured trigger
   allow-list, **end run as `skipped`** (no draft, no send).
2. `compute_delta` — finalise `MatterStatusDelta` (from/to status, context needed
   by the prompt). Persist last-known status for future sweeps.
3. `resolve_recipients` — derive recipient set from `Matter` participants + role
   policy (client always; others per `ASSUMPTION (confirm)` policy).
4. `draft_email` — call `email.draft` (Email port `create_draft`) with a
   `Communication(status="draft", direction="out", channel="email")` rendered via
   the `status_update_email` prompt. Persist `draft_comm_id`. → `draft`.
5. `qc_recipient_integrity` — run the recipient-integrity check; attach result.
   `fail` → park run as `blocked` (gate cannot clear); `warn` → annotate;
   `pass` → continue. → `Communication.status = pending_approval`.
6. `GATE:human_review` — park run `awaiting_approval`; surface via all three
   channels. Approve / edit-then-approve / reject. Reject → run `rejected`, no
   send.
7. `send_email` — on approval, call `email.send(draft_id, idem_key)` where
   `idem_key` is derived from `change_id` (see §8). → `Communication.status = sent`.
8. `record_outcome` — mark `change_id` **delivered**; write final audit record.
   Run `done`.

**Re-trigger of an already-handled `change_id`:** short-circuits at
`detect_change` (or at the orchestrator idempotency guard) to a `noop` — no second
draft, no second send.

## 6. Edge cases & error handling (incl. ConnectorError handling)

- **Duplicate webhook / webhook+sweep overlap:** same `change_id` → orchestrator
  idempotency guard returns the existing run; no new draft/send (NFR-3).
- **Status flaps (A→B→A) quickly:** each distinct transition is a distinct
  `change_id`; a revert to a previously-notified status is a *new* change and may
  notify again `ASSUMPTION (confirm)` whether reverts should suppress.
- **`to_status` not in allow-list:** run ends `skipped`; audited as skipped with
  reason.
- **Recipient-integrity `fail`:** gate cannot be approved into a send; run parks
  as `blocked` for human attention; never auto-sends.
- **No resolvable recipient (missing client email):** run parks as `blocked`;
  surfaced as a gap (no silent drop).
- **Email port `ConnectorError`** (taxonomy from `connector-framework`):
  - `auth` / `fatal` on `create_draft` or `send` → park run for human attention;
    do **not** mark `change_id` delivered.
  - `rate-limit` / `transient` → orchestrator retries with backoff (tenacity);
    `send` retries reuse the same `idem_key` so a retried send cannot duplicate.
  - `not-found` (draft id missing at send) → re-create draft is **not** automatic;
    park as `blocked` (a missing approved draft is a human-attention event).
- **Approval channel race (two approvers):** first decision wins; the gate is
  single-resolution (mechanics owned by `workflow-orchestration`); a late second
  approval is a no-op.
- **Send succeeds but `record_outcome` fails:** because `idem_key` is bound to
  `change_id`, a retried send is de-duplicated by the Email adapter; outcome
  recording is itself retried; `change_id` is only marked delivered after the
  audit write (so a crash never both sends-twice and loses the record).
- **Matter changed between draft and approval:** the draft snapshots the delta;
  if the underlying status changed again, that is a *new* `change_id` / new run;
  the stale draft, if approved, still sends the snapshotted content `ASSUMPTION
  (confirm)` whether to re-validate freshness at the gate.

## 7. Risk tiers & gates for each action

| Step / action | Risk tier | Gate |
|---|---|---|
| `detect_change`, `compute_delta`, `resolve_recipients` | `read` | none |
| `email.draft` (create draft) | `draft only` / `write (confirm)` | no human gate (reversible) |
| `qc_recipient_integrity` | `read` (gate input) | feeds the gate; `fail` blocks |
| `GATE:human_review` | — | **explicit human approval, default-on (NFR-4)** |
| `email.send` | **`gated (human)`** | only reachable post-approval |
| audit writes | internal | precede step completion (NFR-2) |

The **only** externally-irreversible action is `email.send`, and it is reachable
**only** after a recorded approval and a non-`fail` recipient-integrity result.

## 8. Data & persistence

- **`change_id` (idempotency anchor):** deterministic hash of
  `(matter_id, from_status, to_status, transition_sequence_or_event_id)`.
  - For webhooks: prefer the provider event id (already deduped at ingestion) as
    the transition discriminator.
  - For sweeps: use a monotonic per-matter transition counter / last-known-status
    record so the same observed transition yields the same `change_id`.
  - `ASSUMPTION (confirm):` exact composition once vendor status semantics are
    known; documented in the workflow's `docs/workflows/status-update-email.md`.
- **Idempotency table / Redis key:** `change_id → run_id` mapping plus a
  `delivered` flag; enforced before creating a run and before `send`.
- **Last-known-status store:** per `matter_id`, persisted so the sweep can detect
  changes and so reverts are distinguishable.
- **`Communication` persistence:** draft and sent records via
  `platform-foundation` persistence; `draft_comm_id` and `message_id` retained.
- **Run state:** persisted by the orchestrator (inputs/outputs per step for audit
  + resume).
- **Audit records:** append-only, hash-chained (CONVENTIONS §7): one per
  state-changing step — `draft_created`, `qc_result`, `approval_decision`,
  `email_sent`, plus `skipped` / `blocked` terminal reasons.
- **Retention/residency:** email bodies contain immigration PII → stored under the
  configured residency region; no body content in logs/telemetry (NFR-1, NFR-6).

## 9. Observability (logs/metrics/traces for this feature)

- **Logs (structlog, correlation id = `run_id`, PII-scrubbed):** trigger received
  (source), skipped (reason), draft created, QC result, gate entered/resolved,
  send attempted/succeeded/failed, change_id marked delivered.
- **Metrics (Prometheus):** runs started by source; skipped count; drafts created;
  QC pass/warn/fail counts; gate dwell time; sends succeeded/failed; duplicate
  triggers suppressed; time-from-status-change-to-draft (SLA tracking, PRD §8).
- **Traces (OpenTelemetry):** span per step; spans for Email port calls and the QC
  call, parented to the run.
- **Alerting:** parked/`blocked` run backlog; repeated send failures for one
  matter; QC `fail` rate spike.

## 10. Security & privilege considerations

- **Recipient integrity (NFR-1):** recipients are resolved strictly from the
  matter's participant set; the recipient-integrity QC check re-verifies recipient
  ∈ participants and correct client **before** the gate. A send can never target an
  address outside the matter — preventing cross-client leakage of immigration PII.
- **Privilege:** the email body and any attachments must respect privilege; this
  feature sends client-facing content only and never routes privileged internal
  notes externally (privilege-aware checks owned by `qc-verification` /
  `document-routing`).
- **AuthZ (RBAC):** only roles permitted to approve may resolve the gate; the agent
  runs under a constrained service identity and cannot self-approve a send.
- **Audit (NFR-2):** approval (who/when), QC result, and send are all recorded
  immutably before completion; exportable for SOC 2 / privilege review.
- **No content in telemetry:** subject/body never logged; only ids, statuses, and
  counts. Immigration PII (A-numbers, etc.) never appears in logs/metrics/traces.
- **Secrets:** Email credentials live in the connector layer's secret store, never
  surfaced to this workflow.

## 11. Dependencies & integration points

- **`platform-foundation`** — domain model, hash-chained audit, persistence,
  config/secrets, observability.
- **`connector-framework`** — Email port + adapter contract, webhook ingestion
  (verify/normalise/dedupe), `ConnectorError` taxonomy, idempotency-key forwarding.
- **`workflow-orchestration`** — workflow registration, durable run state, gate
  mechanics (all three approval channels), idempotency keys per `(run_id, step)`,
  retry/compensation, webhook + schedule triggers.
- **`qc-verification`** — recipient-integrity check consumed as a gate input.
- **Case port (read)** — current matter status/participants for delta + sweep.

## 12. Test strategy (focused tests + pointer to PBT)

- **Focused tests (pytest, respx/vcr for ports):** allow-list trigger vs skip;
  delta computation from webhook event and from sweep diff; draft created before
  send; QC `fail` blocks gate; reject → no send; approval → send with idem_key;
  duplicate event → one run/one send; ConnectorError handling (auth/transient/
  not-found) per §6; `Communication` status transitions.
- **Property-based tests:** see `pbt-properties.md` — one-send-per-status-change
  idempotency, recipient-in-participants invariant, no-send-without-approval, and
  draft-always-precedes-send.
- **Security checks:** see `security-audit-prep.md`.

## 13. Open questions

- Trigger allow-list of status values and per-status suppression policy
  (`ASSUMPTION (confirm)`).
- Recipient role policy beyond the client (cc representative / G-28 attorney /
  interpreter?) (`ASSUMPTION (confirm)`).
- Sweep cadence and webhook-primary-vs-sweep-safety-net relationship
  (`ASSUMPTION (confirm)`).
- Whether status **reverts** should suppress a repeat notification
  (`ASSUMPTION (confirm)`).
- Whether a stale-but-approved draft should be re-validated for freshness at send
  (`ASSUMPTION (confirm)`).
- Exact `change_id` composition once vendor status semantics are known
  (`ASSUMPTION (confirm)`).
- Per-matter opt-out flag for automated status emails (`ASSUMPTION (confirm)`).
