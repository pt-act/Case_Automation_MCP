# Requirements — Workflow Orchestration

> Inherits the shared baseline in `specs/_shared/CONVENTIONS.md` (process,
> domain model, risk tiers, security/compliance floor) and the architecture in
> `concept/PTD.md` (esp. §7). This file states the "what & why" only.

## 1. Context & problem

Operational workflows in the firm are multi-step, long-lived, and cross several
external systems (case management, CRM, email, document store). A single run
must survive a process restart, pause for **hours or days** at a human-approval
gate, and resume cleanly without re-doing or double-applying any external
effect (e.g. never send a welcome email twice). Scripts cannot give this:
they lose state on crash, have no audit trail of where they paused, and have no
safe place to wait for a human. PRD Risk rows 1 and 2 ("AI sends wrong external
comms", "missed deadline due to engine bug") make *durable, resumable, gated,
never-silent-fail* orchestration a liability-grade requirement, not a
convenience.

This feature is the **durable, resumable state-machine engine** (PTD §7) that
every workflow spec runs on. It owns: the workflow definition model, run/step
persistence, idempotency, retry/compensation, parked-run handling, trigger
intake, the human-gate abstraction with its three approval channels, approval
token issuance/verification, and the `workflow.run` / `workflow.status` MCP
tools. It does **not** own the individual workflows themselves — those are
separate specs that depend on this one.

## 2. In scope

- **Workflow definition model:** a `@workflow(name)` decorator + ordered step
  list; steps are named callables; `GATE:*` steps mark human-approval points.
- **Run engine + step state machine:** start a run, advance step-by-step,
  persist after every step, drive the legal state transitions only.
- **Run persistence schema:** each run = one row plus per-step state rows in
  Postgres; per-step inputs/outputs stored for audit, replay, and resume.
- **Idempotency:** each step's external effect keyed on `(run_id, step)`; a
  re-executed step never re-applies its external effect.
- **Retry / compensation:** transient errors retry with exponential backoff +
  jitter (via the connector-framework taxonomy); fatal/exhausted errors **park**
  the run for human attention — never silent-fail.
- **Parked-run handling:** `parked` (error) and `awaiting_approval` (gate) are
  first-class, queryable, alertable states; runs resume from persisted state.
- **Trigger intake:** start a run from (a) a normalised webhook `Event`, (b) a
  schedule, or (c) an explicit `workflow.run` agent call — all through one
  enqueue path.
- **Gate abstraction:** a `GATE:*` step parks the run in `awaiting_approval`,
  emits an approval request, and resumes on an approval decision. **Three
  approval-channel adapters behind one interface:** MCP tool callback,
  lightweight web UI, and email action.
- **Approval token issuance & verification:** signed, single-use, expiring
  tokens that authorise resume of exactly one gate on one run.
- **Resume-after-crash:** on restart, in-flight runs are recovered and continued
  from their last persisted step with no duplicate side effects.
- **MCP tools:** `workflow.run` (start/trigger) and `workflow.status` (inspect).

## 3. Out of scope

- The individual workflows themselves (`client-intake`, `status-update-emails`,
  `deadline-engine` runs, etc.) — they are consumers of this engine.
- Deadline scheduler internals (rule sets, business-day math, escalation) —
  owned by `deadline-engine`; this engine only provides the run/step machinery
  and schedule-trigger intake it sits on.
- Connector adapters and the `ConnectorError` taxonomy definition — owned by
  `connector-framework`; consumed here.
- QC check internals — owned by `qc-verification`; consumed only as gate inputs.
- The audit log table, RBAC model, secret store, and observability plumbing —
  owned by `platform-foundation`; consumed here.
- Webhook signature verification / dedupe at the edge — owned by
  `connector-framework`; this engine receives already-normalised `Event`s.

## 4. Users / actors

| Actor | Interaction with this feature |
|---|---|
| **The AI Agent** | Calls `workflow.run` / `workflow.status`; may approve a gate via the MCP tool callback adapter under its constrained identity. |
| **Paralegal / Case Manager** | Receives gate approval requests (web UI / email); approves or rejects; checks run status. |
| **Fee-earner / Attorney** | Authoritative approver for `gated (human)` steps; relies on the audit trail of every approval/rejection. |
| **Operations / Admin** | Monitors parked-run backlog and gate dwell time; re-drives or cancels stuck runs. |
| **Sidecar (system actor)** | Webhook + scheduler triggers enqueue runs through the same orchestrator. |

## 5. Functional requirements (trace to PRD FR-xx)

- **WO-FR-1** Define workflows declaratively via `@workflow(name)` with an
  ordered step list and `GATE:*` markers. *(PTD §7; supports FR-5..FR-12 by
  hosting them.)*
- **WO-FR-2** Execute a workflow run as a durable state machine, persisting run
  + per-step state to Postgres after each step. *(FR-14; NFR-3, NFR-7.)*
- **WO-FR-3** Store each step's inputs and outputs for audit, resume, and replay.
  *(FR-15; NFR-2.)*
- **WO-FR-4** Make every step's external effect idempotent, keyed on
  `(run_id, step)`; safe retries never double-apply. *(FR-4; NFR-3.)*
- **WO-FR-5** Retry transient errors with backoff; on fatal/exhausted error,
  **park** the run for human attention and never silent-fail. *(NFR-3, NFR-9;
  PRD Risk rows 1–2.)*
- **WO-FR-6** Provide compensation hooks so a step can register a reverse action
  to undo a partially-applied effect when a later step fails. *(NFR-3.)*
- **WO-FR-7** Intake triggers from webhook `Event`, schedule, and explicit
  `workflow.run`, all via one enqueue path. *(FR-3; FR-12.)*
- **WO-FR-8** Implement a human gate: a `GATE:*` step parks the run in
  `awaiting_approval`, emits an approval request, and resumes only on a recorded
  approval decision. *(FR-14; NFR-4.)*
- **WO-FR-9** Support **all three** approval channels behind one abstraction:
  MCP tool callback, web UI, and email action. *(FR-14; CONVENTIONS §4.)*
- **WO-FR-10** Issue signed, single-use, expiring approval tokens; verify them
  on resume; record approver identity and decision. *(NFR-2, NFR-4; FR-15.)*
- **WO-FR-11** A gated step's **post-gate action never executes without a
  recorded, valid approval** for that gate. *(FR-14; NFR-4.)*
- **WO-FR-12** Recover and resume in-flight runs after a process crash/restart
  from last persisted state, with no duplicate side effects. *(NFR-3, NFR-9.)*
- **WO-FR-13** Expose `workflow.run` (start/trigger a run) and `workflow.status`
  (inspect run + step states) as self-describing MCP tools. *(FR-17.)*
- **WO-FR-14** Write an audit record (via the platform audit log) for every run
  state change, gate request, approval, rejection, park, and resume — *before*
  the change is considered complete. *(FR-15; NFR-2.)*
- **WO-FR-15** Ship a living workflow-engine doc and per-workflow spec hook
  (trigger, steps, gates, side effects, idempotency keys, failure handling) as
  part of "done". *(FR-18; NFR-10.)*

## 6. Non-functional requirements (trace to PRD NFR-xx)

- **NFR-3 (Reliability):** runs idempotent; retries safe; no duplicate
  sends/filings; resume-equals-continue invariant holds.
- **NFR-4 (Human control):** no `gated (human)` post-gate action without an
  explicit, valid approval (default-on gates).
- **NFR-9 (Graceful degradation):** a crash or one connector being down parks or
  retries affected runs rather than losing or corrupting them; the engine
  recovers on restart.
- **NFR-2 (Auditability):** 100% of run state changes and approvals logged
  immutably (hash-chained audit log, owned by platform-foundation).
- **NFR-7 (Observability):** structured logs, metrics, and traces per run/step
  (`run_id` as correlation id).
- **NFR-8 (Secret hygiene):** token-signing key from the secret store; never in
  source, never logged.
- **NFR-1 (Confidentiality):** step inputs/outputs may hold immigration PII;
  encrypted at rest; redacted in logs/telemetry.

## 7. Dependencies (other feature specs, external systems)

- **`platform-foundation`** — domain model, Postgres + SQLAlchemy/Alembic,
  hash-chained audit log, RBAC + service identity, secret store (token-signing
  key), structlog/OTel/Prometheus. *(hard dependency)*
- **`connector-framework`** — normalised webhook `Event`, `ConnectorError`
  taxonomy (transient vs fatal classification that drives retry-vs-park),
  idempotent connector writes. *(hard dependency)*
- **Consumers (depend on this spec):** `deadline-engine`, `client-intake`,
  `status-update-emails`, `qc-verification`, and every other workflow.
- **External systems:** Postgres (state + run/step rows), Redis (queue, locks,
  idempotency reservations), the MCP client (tool callback channel), an SMTP/
  email send path and a small web service for the other two approval channels.

## 8. Assumptions & open questions

- **ASSUMPTION (confirm):** Queue/runtime is **Celery + Redis** for v1 (PTD §3
  allows APScheduler for a lean start). The engine is written against an
  abstract task queue port so either backing is swappable. *(PTD §18 Q3.)*
- **ASSUMPTION (confirm):** Approval-token TTL defaults to **72 hours**, after
  which the token expires and a fresh approval request must be re-issued (the
  run stays parked in `awaiting_approval`, it is not auto-rejected). Gate dwell
  may legitimately run to days.
- **ASSUMPTION (confirm):** Max automatic retries per step = **5**, exponential
  backoff base **2s**, cap **5 min**, full jitter; exceeding this parks the run.
- **ASSUMPTION (confirm):** Approval authority is **role-based** — who may
  approve a given gate is configured per workflow/gate against RBAC roles from
  `platform-foundation` (e.g. attorney-only for external sends). Default if
  unset: a designated approver role, never "any authenticated user".
- **ASSUMPTION (confirm):** A gate may be configured to require **N-of-M**
  approvals; v1 default is **1-of-1**. The token/audit model supports >1 but the
  UI for it is deferred.
- **ASSUMPTION (confirm):** Approval tokens are signed with **HMAC-SHA256** using
  a rotating key from the secret store (key id embedded so rotation doesn't
  invalidate in-flight tokens). Asymmetric (Ed25519) is an option if external
  verification is later needed.
- **ASSUMPTION (confirm):** Workflow **definitions are versioned**; an in-flight
  run is pinned to the definition version it started on, so redeploying a changed
  workflow never re-shapes a parked run mid-flight.
- **ASSUMPTION (confirm):** Step execution is **at-least-once** (idempotency
  makes effects exactly-once); the engine does not promise exactly-once
  scheduling, it promises exactly-once *external effect* via idempotency keys.
- **Open question:** the concrete approval **web UI** scope (standalone page vs
  embedded in the sidecar) — see `spec.md` §13.

## 9. Acceptance criteria (testable checklist)

- [ ] A workflow declared with `@workflow` and an ordered step list (incl. a
      `GATE:*` step) registers and is runnable by name via `workflow.run`.
- [ ] Starting a run creates exactly one run row and one step-state row per step
      (status `pending`), with the definition version recorded.
- [ ] After each step, the run's persisted state reflects the completed step and
      its stored inputs/outputs; killing the process mid-run and restarting
      resumes from the next un-applied step.
- [ ] Re-executing a completed step (forced retry or post-crash replay) does not
      re-apply its external effect (verified via idempotency key + connector
      idem-key forwarding).
- [ ] A transient `ConnectorError` causes a retry with backoff; after the retry
      cap the run transitions to `parked` and an alert/audit record is written —
      the run is never marked `done` or silently dropped.
- [ ] A `GATE:*` step transitions the run to `awaiting_approval`, persists a
      pending approval, and emits a request on the configured channel(s).
- [ ] The post-gate step does not run until a valid approval is recorded; an
      attempt to resume without/with an invalid/expired/reused token is rejected
      and audited.
- [ ] An approval can be delivered and accepted through **each** of the three
      channels (MCP tool callback, web UI, email action) against the same gate.
- [ ] An approval token is single-use: a second presentation returns a 409/`used`
      result and does not resume the run again.
- [ ] Only an actor holding the configured approver role may approve; an
      unauthorised approval attempt is rejected (403) and audited.
- [ ] `workflow.status` returns the run state, per-step states, current gate (if
      any), retry counts, and last error for a given `run_id`.
- [ ] Every run state change, gate request, approval, rejection, park, and
      resume produces an audit record before the change is acknowledged.
- [ ] Only legal state transitions occur; an illegal transition request is
      rejected and never persisted.
