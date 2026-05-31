# Spec — Workflow Orchestration

> Behavioural contract for the durable, resumable state-machine engine (PTD §7).
> Describes the "what" — behaviour, interfaces, data shapes, edge cases — not
> line-by-line code. Implementation steps live in `tasks.md`. Inherits
> `specs/_shared/CONVENTIONS.md` and `concept/PTD.md`.

## 1. Summary

The orchestration engine runs multi-step workflows as **durable state
machines** persisted in Postgres. A run advances one step at a time, persisting
after each step, so it survives restarts and can pause at a human gate for
hours/days. Each step's external effect is idempotent on `(run_id, step)`.
Transient errors retry with backoff; fatal/exhausted errors **park** the run for
a human (never silent-fail). Runs start from a webhook `Event`, a schedule, or an
explicit `workflow.run` call. A `GATE:*` step parks the run in
`awaiting_approval` and resumes only when a valid approval arrives via one of
three channels — **MCP tool callback, web UI, or email action** — each
authorised by a signed, single-use, expiring token. The engine exposes
`workflow.run` and `workflow.status` to the agent.

## 2. Scope & out-of-scope

**In scope:** workflow definition DSL/decorator; run engine + step state
machine; run/step persistence schema; idempotency; retry/compensation; parked-
run handling; trigger intake; gate abstraction + the three approval-channel
adapters; approval token issuance/verification; `workflow.run` / `workflow.status`
tools; resume-after-crash.

**Out-of-scope:** individual workflow definitions (the steps' business logic);
deadline scheduler internals; connector adapters and the `ConnectorError`
taxonomy definition; QC check internals; the audit-log table, RBAC model, secret
store, and observability plumbing (consumed from `platform-foundation`); webhook
signature verification/dedupe at the edge (consumed from `connector-framework`).
See `requirements.md` §3 for the full list.

## 3. Domain types used / introduced

**Used (from PTD §4, via `platform-foundation`):** `Contact`, `Matter`,
`Document`, `Deadline`, `Communication`, `Task` — appear inside step
inputs/outputs but are not redefined here.

**Introduced (engine-local, proposed for shared persistence layer):**

```python
class WorkflowRun(BaseModel):
    id: str                      # run_id (uuid)
    workflow: str                # registered workflow name
    workflow_version: int        # pinned definition version
    status: RunStatus            # see state machine §5.2
    current_step: str | None     # name of the next/active step
    trigger: TriggerRef          # how the run started
    context: dict                # immutable run-scoped inputs
    created_at: datetime
    updated_at: datetime
    last_error: StepError | None

class StepState(BaseModel):
    run_id: str
    step: str                    # step name
    seq: int                     # ordinal position in the workflow
    status: StepStatus           # pending|running|succeeded|failed|compensated|skipped
    attempt: int                 # retry counter
    idem_key: str                # = f"{run_id}:{step}"
    input: dict | None           # persisted for audit/resume
    output: dict | None
    error: StepError | None
    started_at: datetime | None
    ended_at: datetime | None

class GateRequest(BaseModel):
    id: str                      # gate_request_id
    run_id: str
    step: str                    # the GATE:* step
    risk_tier: Literal["gated (human)"]
    required_role: str           # RBAC role permitted to approve
    quorum: int = 1              # N-of-M (default 1)
    status: Literal["pending","approved","rejected","expired"]
    channels: list[Literal["mcp","web","email"]]
    created_at: datetime
    expires_at: datetime

class ApprovalDecision(BaseModel):
    gate_request_id: str
    run_id: str
    step: str
    decision: Literal["approve","reject"]
    actor: str                   # authenticated approver identity
    channel: Literal["mcp","web","email"]
    token_id: str                # the single-use token consumed
    reason: str | None
    decided_at: datetime

class TriggerRef(BaseModel):
    kind: Literal["event","schedule","agent"]
    source: str                  # event id / schedule id / agent identity
    dedupe_key: str | None       # for event-sourced idempotent start
```

> If any of these belong in the shared persistence layer rather than this
> feature, they are proposed in `platform-foundation` and reused (CONVENTIONS
> §5). They are listed here because this feature owns their behaviour.

## 4. Interfaces (MCP tools/resources/prompts, ports, internal APIs)

### 4.1 MCP tools (PTD §5.1)

- **`workflow.run`** — start a run.
  - Input: `{ workflow: str, context: dict, trigger?: TriggerRef, idem_key?: str }`.
  - Behaviour: validate workflow name + context against the definition's input
    schema; if `idem_key`/`trigger.dedupe_key` matches an existing run, return
    that run (no new run) — idempotent start; else create a run and enqueue it.
  - Output: `{ run_id, status, current_step }`.
  - Risk tier: **varies** — the *call to start* is `write (confirm)`; gated steps
    inside still require their own approval. Starting a run never bypasses a gate.
- **`workflow.status`** — inspect a run.
  - Input: `{ run_id: str }`.
  - Output: `{ run, steps: [StepState], gate?: GateRequest, last_error? }`.
  - Risk tier: **read**. Output is RBAC-scoped and PII-redacted per viewer role.

Both tools self-describe via Pydantic schemas + docstrings (FR-17).

### 4.2 Workflow definition DSL

```python
@workflow("intake", version=1, input_schema=IntakeInput)
class IntakeWorkflow:
    steps = [
        "parse_lead", "dedupe_contact", "create_contact", "create_matter",
        "compute_deadlines", "open_tasks", "draft_welcome",
        "GATE:human_review",          # parks → awaiting_approval
        "send_welcome",               # post-gate action
    ]
```

- Each non-gate step name maps to an async handler `async def parse_lead(ctx) ->
  StepResult`. `ctx` exposes the immutable run context, prior step outputs, an
  idempotency-scoped connector facade, and a `compensate(fn)` registration hook.
- A step name prefixed `GATE:` is a gate step; its config (`required_role`,
  `quorum`, `channels`, `ttl`) is declared via a `gates = {...}` map on the class
  or defaults from risk tier.
- `version` pins the definition; in-flight runs keep their started version.

### 4.3 Internal ports (consumed)

- **`TaskQueue`** port — `enqueue(run_id)`, `reserve()`, `ack()` (Celery/Redis or
  APScheduler adapter; ASSUMPTION confirm in requirements §8).
- **`IdempotencyStore`** port — `reserve(idem_key) -> bool`, `record(idem_key,
  output)`, `lookup(idem_key)` (Redis + Postgres durable backstop).
- **`AuditLog`** port — `append(actor, action, inputs, outputs, approval,
  run_id)` (from `platform-foundation`; hash-chained).
- **`SecretStore`** port — fetch the token-signing key + key id.
- **`ConnectorError`** taxonomy — imported from `connector-framework`; classifies
  errors as `transient` (retry) vs `fatal` (park).

### 4.4 Approval-channel port (the gate abstraction)

```python
class ApprovalChannel(Protocol):
    name: Literal["mcp","web","email"]
    async def emit(self, gate: GateRequest, token: ApprovalToken) -> None: ...
    # Inbound decisions arrive via channel-specific entrypoints (below) and are
    # funnelled to a single resolve_gate(decision, token) core function.
```

- **MCP tool callback adapter:** exposes an `approval.decide` MCP tool taking
  `{ token, decision, reason? }`; the agent (or a human operating the agent)
  approves/rejects.
- **Web UI adapter:** the sidecar serves a minimal authenticated approval page at
  `/approvals/{token}` (GET renders the gate context; POST submits the decision).
- **Email action adapter:** the request email contains signed approve/reject
  action links (`/approvals/{token}?decision=approve`) and/or a reply-to-approve
  path; clicking resolves the gate after authentication.

All three converge on one `resolve_gate(token, decision, actor, channel)`
function — the single authority that verifies the token, checks authz, records
the `ApprovalDecision`, audits it, and resumes (or rejects) the run.

## 5. Behaviour & flows (happy path + state transitions)

### 5.1 Happy path (run with a gate)

1. **Trigger** arrives (event/schedule/agent) → `workflow.run` resolves it to a
   workflow name + context; idempotent-start check; create `WorkflowRun`
   (`status=queued`) and one `StepState` per step (`pending`); audit `run.created`.
2. **Enqueue** → worker reserves the run, sets `running`, advances to the first
   `pending` step.
3. **Per step:** mark step `running`; `reserve(idem_key)`; if already recorded,
   reuse stored output (no re-effect); else execute handler, persist `output`,
   `record(idem_key, output)`, mark step `succeeded`, audit `step.succeeded`,
   persist run progress, advance.
4. **Gate step** (`GATE:*`): create a `GateRequest` (`pending`), issue an
   `ApprovalToken` per channel, set run `awaiting_approval`, `emit` on configured
   channels, audit `gate.requested`. The worker yields — no further steps run.
5. **Approval** arrives on any channel → `resolve_gate` verifies token + authz,
   records `ApprovalDecision(approve)`, marks `GateRequest=approved`, audits
   `gate.approved`, sets run `running`, re-enqueues, advances to the post-gate
   step.
6. **Completion:** when the last step succeeds, run → `succeeded`; audit
   `run.succeeded`.

### 5.2 Run state machine (legal transitions only)

```
queued ─► running ─► succeeded
   ▲         │  ├─► awaiting_approval ─►(approve)─► running
   │         │  │                      └─►(reject)─► rejected
   │         │  ├─►(transient, attempts<cap)─► running   (retry, same step)
   │         │  └─►(fatal | attempts==cap)─► parked
   └─(resume after crash: recover queued/running/awaiting_approval)
parked ─►(human re-drive)─► running
parked ─►(human cancel)─► cancelled
running ─►(human cancel)─► cancelled
```

- **Step states:** `pending → running → {succeeded | failed}`; `failed` either
  loops to `running` (retry) or escalates the run to `parked`. A
  `compensated` state marks a step whose effect was reversed; `skipped` marks a
  step bypassed by a (future) conditional branch.
- Any transition not on this diagram is rejected and never persisted (PBT:
  "only valid state transitions occur").

### 5.3 Trigger intake (one enqueue path)

- **Event:** normalised `Event` from `connector-framework` → mapped to a
  workflow + context by a registered binding; `dedupe_key = Event.id` ensures one
  run per event (idempotent start).
- **Schedule:** a scheduled trigger (e.g. from `deadline-engine`) calls the same
  start path with a `schedule` `TriggerRef`.
- **Agent:** `workflow.run` directly. All three converge on
  `start_run(workflow, context, trigger, idem_key)`.

### 5.4 Idempotency

- `idem_key = f"{run_id}:{step}"`. Before a step's external effect, the engine
  `reserve`s the key; the connector call also receives this key (forwarded per
  PTD §6 "be idempotent on writes"). If the key is already recorded, the engine
  short-circuits to the stored output. This gives exactly-once *external effect*
  under at-least-once execution.

### 5.5 Retry / compensation

- On `ConnectorError(transient)` or unhandled transient exception: increment
  `attempt`, schedule a retry with exponential backoff + full jitter (base/cap/
  max-attempts per requirements §8), keep the run `running` (or `queued` for the
  delayed retry).
- On `ConnectorError(fatal)` or retries exhausted: run → `parked`; persist
  `last_error`; audit `run.parked`; emit a parked-run alert. Registered
  `compensate` hooks for already-succeeded steps may be invoked to reverse
  partial effects before parking (best-effort, each compensation idempotent).

### 5.6 Gate resolution (all three channels, one authority)

`resolve_gate(token, decision, actor, channel)`:
1. Verify token signature (HMAC, key id) → reject if tampered.
2. Check not expired, not already used (single-use) → else `expired`/`used`.
3. Resolve `actor` and check they hold `GateRequest.required_role` → else 403.
4. Atomically mark token `used`, record `ApprovalDecision`, update `GateRequest`.
5. Audit `gate.approved` / `gate.rejected` with actor, channel, token id.
6. On approve: set run `running`, enqueue, advance past the gate. On reject: run
   → `rejected` (terminal); no post-gate action runs.

### 5.7 Resume-after-crash

- On startup, a recovery sweep finds runs in `queued`/`running`/
  `awaiting_approval`. `awaiting_approval` runs are left parked at their gate
  (no work to redo). `queued`/`running` runs are re-enqueued; the worker resumes
  at the first `pending` step. Because every completed step recorded its
  idem-key + output, re-entry never re-applies an external effect (resume
  invariant).

## 6. Edge cases & error handling (incl. ConnectorError handling)

- **Duplicate trigger / duplicate `workflow.run`:** matched by
  `idem_key`/`dedupe_key` → returns the existing run; no second run, no second
  effects. (Returns the existing `run_id`; a true duplicate `workflow.run` is a
  no-op create.)
- **Step partially applied then crash:** on resume, `reserve(idem_key)` finds the
  key recorded → output reused, no re-apply. If the effect happened but the
  output was not yet recorded (crash in the gap), the connector's own idem-key
  makes the re-call a no-op and returns the prior result.
- **`ConnectorError(transient)`:** retry with backoff up to cap, then park.
- **`ConnectorError(rate-limit)`:** treated as transient with a longer/Retry-
  After-honoring backoff; never counts toward fatal until cap.
- **`ConnectorError(auth | not-found | fatal)`:** park immediately; do not retry
  a non-retryable error.
- **Gate token expired:** run stays `awaiting_approval`; a fresh request/token
  can be re-issued; the expired token cannot resume the run.
- **Reused token:** second use returns `used` (409), is audited, and does **not**
  resume the run again.
- **Unauthorised approver:** 403, audited; gate stays pending.
- **Reject decision:** run → `rejected`; post-gate steps never execute; audit
  records the rejecter + reason.
- **Two approvals racing (different channels):** the atomic `mark used` ensures
  exactly one decision resolves the gate; the loser sees `used`/already-resolved.
- **Illegal transition request** (e.g. resume a `succeeded` run): rejected, not
  persisted, audited as `transition.rejected`.
- **Unknown workflow name / context schema mismatch:** `workflow.run` returns a
  validation error; no run created.
- **Definition changed mid-flight:** in-flight run keeps its pinned
  `workflow_version`; new version applies only to new runs.
- **Parked-run backlog:** surfaced via metric + alert; `workflow.status` and an
  admin listing expose parked runs for re-drive or cancel.

## 7. Risk tiers & gates for each action

| Action / step | Risk tier | Gate behaviour |
|---|---|---|
| `workflow.run` (start a run) | `write (confirm)` | Creates a run; does not bypass inner gates. |
| `workflow.status` (read) | `read` | None; RBAC-scoped, PII-redacted output. |
| Non-gate step (read sub-action) | `read` | None. |
| Non-gate step (reversible write) | `write (confirm)` | Idempotent; auditable; reversible via compensation. |
| `GATE:*` step | `gated (human)` | Parks run in `awaiting_approval`; requires a valid, single-use approval token from an authorised approver before the post-gate step runs (NFR-4, default-on). |
| `approval.decide` / web / email decision | `gated (human)` control-plane | Verifies token + authz; records decision; resumes/rejects. |

Default-on: any step a consuming workflow marks `gated (human)` (e.g.
`email.send`, filing) must be preceded by a `GATE:*` step; the engine refuses to
run a `gated (human)` post-gate action without a recorded approval (PBT focus).

## 8. Data & persistence

- **Postgres tables** (SQLAlchemy 2 + Alembic, owned with `platform-foundation`):
  `workflow_runs`, `workflow_step_states`, `gate_requests`, `approval_decisions`,
  `approval_tokens` (id, gate_request_id, key_id, hash, channel, expires_at,
  used_at). Unique constraint on `(run_id, step)` for step states; unique on
  `approval_tokens.id`; `dedupe_key` unique index for idempotent starts.
- **Inputs/outputs** of each step stored as JSONB for audit, resume, and replay;
  large/binary artefacts referenced by URI (object store), not inlined.
- **Redis** holds idempotency reservations + distributed locks (one worker per
  run at a time) + the task queue; Postgres is the durable source of truth and
  backstop so a Redis flush cannot lose run state.
- **Token storage:** only a hash of each token is stored; the raw token lives in
  the delivered request (email link / UI URL / MCP arg). `used_at` enforces
  single-use atomically.
- **PII:** step JSONB may contain immigration PII; encrypted at rest (AES-256);
  redacted before logging; never copied into telemetry (CONVENTIONS §7).

## 9. Observability (logs/metrics/traces for this feature)

- **Logs (structlog):** correlation id = `run_id`; events `run.created/started/
  succeeded/parked/rejected/cancelled`, `step.started/succeeded/failed/retried/
  compensated`, `gate.requested/approved/rejected/expired`; PII-scrubbed.
- **Metrics (Prometheus):** runs by state, step retry count, **gate dwell time**,
  parked-run backlog gauge, approval decisions by channel, idempotency
  short-circuit count, resume-recovery count on startup.
- **Traces (OpenTelemetry):** a span per run, child span per step and per
  connector call; gate wait represented as a span/event boundary.
- **Alerts:** parked-run backlog threshold, gate dwell exceeding TTL, token
  verification failures spike (possible forgery attempts).

## 10. Security & privilege considerations

- **Approval tokens** are signed (HMAC-SHA256 + key id), single-use, expiring,
  and bound to exactly one `(gate_request_id, run_id, step)`. Signing key from
  the secret store; never logged. Verification is constant-time.
- **AuthZ:** every decision checks the approver holds the gate's `required_role`
  (RBAC from `platform-foundation`); the agent's MCP-callback approvals run under
  its constrained service identity and cannot self-approve gates it is not
  authorised for. No privilege escalation via resume — resuming a run executes
  under the original run identity, never the approver's, and never elevates.
- **Audit:** every approval/rejection/park/resume and every state transition is
  appended to the hash-chained audit log *before* completion (NFR-2).
- **Confidentiality/privilege:** step payloads carrying privileged or PII content
  are encrypted at rest and redacted in logs; `workflow.status` output is
  RBAC-scoped so a viewer only sees what their role permits.
- Full threat enumeration in `security-audit-prep.md`.

## 11. Dependencies & integration points

- **`platform-foundation`** — persistence, audit log, RBAC/service identity,
  secret store, observability. *(hard)*
- **`connector-framework`** — normalised `Event` intake, `ConnectorError`
  taxonomy (drives retry-vs-park), idempotent connector writes. *(hard)*
- **Consumers** — `deadline-engine`, `client-intake`, `status-update-emails`,
  `qc-verification`, and all workflows run on this engine and supply gate inputs.
- **Sidecar** — hosts the web-UI and email-action approval entrypoints and the
  webhook/schedule trigger sources (PTD §14).

## 12. Test strategy (focused tests + pointer to PBT)

- **Focused tests (pytest, per task group in `tasks.md`):** registration of a
  `@workflow`; run creation row/step fan-out; per-step persist + advance; forced
  re-run short-circuit; transient-retry-then-park; fatal-immediate-park; gate
  parks + emits on each channel; approval via each channel resumes; token
  expired/reused/unauthorised rejected; reject terminates; crash-resume from each
  state; illegal-transition rejected; idempotent start on duplicate trigger.
- **Connector interactions** stubbed via respx/vcr; queue/Redis via a fake/in-
  memory adapter for determinism.
- **Property-based tests:** see `pbt-properties.md` — step idempotency, resume
  invariant, gate-before-post-gate-action, no-duplicate-effects-under-retry,
  only-valid-transitions.
- **Security verification:** see `security-audit-prep.md` — token unforgeability,
  authz, single-use, no privilege escalation via resume.

## 13. Open questions

- **Approval web UI scope:** standalone minimal page vs richer queue view — see
  requirements §8 (`ASSUMPTION (confirm)`).
- **Queue backing for v1:** Celery+Redis vs APScheduler (PTD §18 Q3).
- **Token TTL, retry cap/backoff, approver-role defaults, N-of-M quorum,**
  **signing algorithm** — all flagged `ASSUMPTION (confirm)` in requirements §8.
- **Compensation depth:** how far back the engine auto-compensates on park vs
  leaving partial effects for human review (default: compensate only steps that
  registered a hook).
- **Email-action authentication:** signed link alone vs link + authenticated
  session — see `security-audit-prep.md`.
