# Tasks — Workflow Orchestration

> Phase 3 of the process in `specs/_shared/CONVENTIONS.md`. Sizes: `XS/S/M/L`
> (§3.5; `L` should usually be split). Dependencies: intra-spec by task id,
> cross-spec as `<slug>#<anchor>`. Cross-spec anchors reference components in
> sibling specs that are not yet task-numbered; they name the consumed
> capability. Focused tests: 2–8 per group (§3.6). Definition of done at the end.

## Overview (task groups + critical path + parallelisation)

| Group | Theme | Size | Parallelisable after deps |
|---|---|---|---|
| G1 | Definition DSL & workflow registry | M | with G2 |
| G2 | Persistence schema & state-machine model | M | with G1 |
| G3 | Run engine + step executor | L → split (G3a/G3b) | after G1, G2 |
| G4 | Idempotency layer | S | after G2 |
| G5 | Retry / compensation / parking | M | after G3a, G4 |
| G6 | Trigger intake (event/schedule/agent) | S | after G3a |
| G7 | Gate abstraction core (token + resolve) | M | after G2, G3a |
| G8 | Approval channel adapters (MCP / web / email) | M | after G7 |
| G9 | MCP tools (`workflow.run` / `workflow.status`) | S | after G3a, G6 |
| G10 | Resume-after-crash recovery | S | after G3, G5, G7 |
| G11 | Observability & alerts | S | after G3, G5, G7 |
| G12 | Docs & CI discipline | XS | last |

**Critical path:** G2 → G3a → G7 → G8 → G10. G1, G4, G6, G9 hang off the path
and parallelise. PBT/security (Phase 4) layer onto G3–G10.

---

## Group 1: Definition DSL & workflow registry
- [ ] **1.1** `@workflow(name, version, input_schema)` decorator + class
      registration into a process registry. — **S**; depends on: —; parallel: yes;
      AC: a decorated class is discoverable by name+version; duplicate
      name+version raises at import; `steps` list and optional `gates` map parsed.
- [ ] **1.2** Step resolution: map step names → async handlers; detect `GATE:*`
      prefix; validate every non-gate step has a handler and every gate has
      config (or risk-tier default). — **S**; depends on: 1.1; parallel: no;
      AC: missing handler or malformed gate config fails registration with a
      clear error.
- [ ] **1.3** Definition versioning + input-schema validation entrypoint used by
      `workflow.run`. — **XS**; depends on: 1.1; parallel: yes; AC: context
      validated against `input_schema`; version recorded on run start.
- **Focused tests:** (a) decorated workflow registers & resolves by name/version;
  (b) duplicate registration errors; (c) `GATE:*` step recognised vs normal step;
  (d) missing handler fails; (e) context schema mismatch rejected.

## Group 2: Persistence schema & state-machine model
- [ ] **2.1** SQLAlchemy models + Alembic migration for `workflow_runs`,
      `workflow_step_states`, `gate_requests`, `approval_decisions`,
      `approval_tokens` (schema per `spec.md` §8). — **M**; depends on:
      `platform-foundation#persistence`; parallel: with G1; AC: unique
      `(run_id, step)`, unique `approval_tokens.id`, unique `dedupe_key` index;
      JSONB input/output columns; migration up/down clean.
- [ ] **2.2** `RunStatus` / `StepStatus` enums + a transition table encoding the
      legal transitions in `spec.md` §5.2. — **S**; depends on: —; parallel: yes;
      AC: `is_legal(from, to)` returns true only for diagrammed edges.
- [ ] **2.3** Repository functions: create run + fan-out step rows; load run with
      steps; persist step transition atomically; persist run transition guarded
      by 2.2. — **M**; depends on: 2.1, 2.2; parallel: no; AC: illegal transition
      raises and writes nothing; create fans out one step row per step in `pending`.
- **Focused tests:** (a) create-run fan-out count == steps; (b) legal transition
  persists; (c) illegal transition rejected + no write; (d) load returns ordered
  steps; (e) `(run_id, step)` uniqueness enforced.

## Group 3: Run engine + step executor
### G3a — core executor
- [ ] **3.1** Worker loop: reserve a run (Redis lock, one worker per run), set
      `running`, find first `pending` step, execute, persist, advance. — **M**;
      depends on: 1.2, 2.3, 4.1; parallel: no; AC: steps run in order; lock
      prevents concurrent execution of the same run.
- [ ] **3.2** Step `ctx` object: immutable run context, prior outputs accessor,
      idempotency-scoped connector facade, `compensate(fn)` registration. — **S**;
      depends on: 3.1, 4.1; parallel: no; AC: handler can read context + prior
      outputs and register a compensation hook.
### G3b — gate yielding
- [ ] **3.3** On `GATE:*` step, hand off to gate core (G7), set
      `awaiting_approval`, stop advancing. — **S**; depends on: 3.1, 7.2;
      parallel: no; AC: worker yields at a gate; no post-gate step runs.
- **Focused tests:** (a) two-step run completes in order; (b) per-run lock blocks
  a concurrent worker; (c) handler reads prior step output; (d) compensation hook
  registered; (e) worker stops at `GATE:*` in `awaiting_approval`.

## Group 4: Idempotency layer
- [ ] **4.1** `IdempotencyStore` port + Redis adapter with Postgres durable
      backstop: `reserve(idem_key)`, `record(idem_key, output)`, `lookup`. — **S**;
      depends on: 2.1; parallel: yes; AC: `reserve` is atomic; `idem_key =
      f"{run_id}:{step}"`; recorded keys return stored output.
- [ ] **4.2** Executor short-circuit: before a step's effect, if key recorded,
      reuse output (skip handler effect); forward `idem_key` into connector calls.
      — **S**; depends on: 4.1, 3.1; parallel: no; AC: a re-run of a recorded step
      performs no external effect and returns the stored output.
- **Focused tests:** (a) first run reserves+records; (b) second run short-circuits
  (no handler effect); (c) connector receives forwarded idem-key; (d) Redis flush
  falls back to Postgres backstop.

## Group 5: Retry / compensation / parking
- [ ] **5.1** Classify errors via `connector-framework#error-taxonomy`:
      transient → retry, fatal → park. — **S**; depends on:
      `connector-framework#error-taxonomy`, 3.1; parallel: no; AC: transient
      schedules retry; fatal parks immediately.
- [ ] **5.2** Backoff scheduler: exponential + full jitter, base/cap/max-attempts
      (requirements §8); on cap exceeded → park. — **S**; depends on: 5.1;
      parallel: no; AC: attempts increment; after cap the run is `parked`, never
      `done`.
- [ ] **5.3** Parking: set `parked`, persist `last_error`, audit `run.parked`,
      emit alert; expose for re-drive/cancel. — **S**; depends on: 5.2, 2.3;
      parallel: no; AC: parked run is queryable; re-drive resumes; cancel
      terminates.
- [ ] **5.4** Compensation runner: invoke registered hooks for succeeded steps
      (idempotent, best-effort) before/at park. — **S**; depends on: 3.2, 5.3;
      parallel: no; AC: compensations run in reverse order; each is idempotent.
- **Focused tests:** (a) transient retries then succeeds; (b) transient exhausts →
  park; (c) fatal → immediate park (no retry); (d) parked run never reaches
  `succeeded`; (e) compensation reverses a prior effect once; (f) re-drive resumes.

## Group 6: Trigger intake (event/schedule/agent)
- [ ] **6.1** `start_run(workflow, context, trigger, idem_key)` single entrypoint
      with idempotent-start via `dedupe_key`. — **S**; depends on: 2.3, 1.3;
      parallel: yes; AC: duplicate `dedupe_key`/`idem_key` returns existing run,
      creates no second run.
- [ ] **6.2** Bindings: normalised `Event` (`connector-framework#webhook-event`)
      and schedule triggers map to workflow+context and call `start_run`. — **S**;
      depends on: 6.1, `connector-framework#webhook-event`; parallel: no; AC: an
      `Event` with the same id starts at most one run.
- **Focused tests:** (a) agent start creates a run; (b) duplicate event id → one
  run; (c) schedule trigger starts a run; (d) unknown workflow rejected.

## Group 7: Gate abstraction core (token + resolve)
- [ ] **7.1** `ApprovalToken` issuance: HMAC-SHA256 + key id from
      `platform-foundation#secret-store`, single-use, expiring, bound to
      `(gate_request_id, run_id, step)`; store only the hash. — **S**; depends on:
      2.1, `platform-foundation#secret-store`; parallel: no; AC: token verifies
      with current/rotated key; tamper fails; only hash persisted.
- [ ] **7.2** `create_gate_request`: build `GateRequest`, set
      `awaiting_approval`, persist pending approval, audit `gate.requested`. —
      **S**; depends on: 7.1, 2.3; parallel: no; AC: gate row created with
      `required_role`, `quorum`, `channels`, `expires_at`.
- [ ] **7.3** `resolve_gate(token, decision, actor, channel)`: verify signature →
      expiry → single-use (atomic `mark used`) → authz (`required_role`) → record
      `ApprovalDecision` → audit → resume/reject. — **M**; depends on: 7.1, 7.2,
      `platform-foundation#rbac`; parallel: no; AC: invalid/expired/reused/
      unauthorised all rejected + audited; approve resumes; reject terminates.
- **Focused tests:** (a) valid approve resumes run; (b) reject → `rejected`,
  no post-gate; (c) expired token rejected; (d) reused token → `used`/409, no
  second resume; (e) wrong role → 403 audited; (f) tampered token fails verify;
  (g) two racing approvals → exactly one resolves.

## Group 8: Approval channel adapters (MCP / web / email)
- [ ] **8.1** `ApprovalChannel` port + `emit` fan-out to configured channels. —
      **XS**; depends on: 7.2; parallel: no; AC: a gate emits on exactly its
      configured channels.
- [ ] **8.2** MCP tool callback adapter: `approval.decide` tool → `resolve_gate`.
      — **S**; depends on: 7.3, 8.1; parallel: yes (with 8.3, 8.4); AC: agent
      approval under its constrained identity resolves the gate when authorised.
- [ ] **8.3** Web UI adapter (sidecar): GET `/approvals/{token}` renders gate
      context (RBAC-scoped); POST submits decision → `resolve_gate`. — **S**;
      depends on: 7.3, 8.1; parallel: yes; AC: authenticated authorised user
      approves/rejects via the page; unauth redirected to login.
- [ ] **8.4** Email action adapter: request email with signed approve/reject
      action links → `resolve_gate`. — **S**; depends on: 7.3, 8.1; parallel: yes;
      AC: clicking a link resolves the gate after authentication; link is the
      single-use token.
- **Focused tests:** (a) emit hits only configured channels; (b) MCP-callback
  approve resumes; (c) web POST approve resumes; (d) email-link approve resumes;
  (e) the same gate resolvable via any one channel; (f) second channel after
  resolution sees `used`.

## Group 9: MCP tools (`workflow.run` / `workflow.status`)
- [ ] **9.1** `workflow.run` tool: validate name+context, idempotent start,
      enqueue, return `{run_id,status,current_step}`. — **S**; depends on: 6.1,
      1.3; parallel: yes; AC: starting never bypasses inner gates; duplicate
      start returns existing run.
- [ ] **9.2** `workflow.status` tool: return run + step states + current gate +
      last error, RBAC-scoped & PII-redacted. — **S**; depends on: 2.3,
      `platform-foundation#rbac`; parallel: yes; AC: output reflects live state;
      redaction applied per viewer role.
- **Focused tests:** (a) run starts & returns id; (b) status reflects step
  progression; (c) status shows current gate when parked at one; (d) redaction
  hides PII for an unprivileged viewer.

## Group 10: Resume-after-crash recovery
- [ ] **10.1** Startup recovery sweep: find `queued`/`running`/`awaiting_approval`
      runs; re-enqueue runnable ones; leave gated ones parked. — **S**; depends
      on: 3.1, 5.3, 7.2; parallel: no; AC: after a simulated crash, a run resumes
      at the first `pending` step with no duplicate effect.
- [ ] **10.2** Resume invariant guard: re-entry respects idempotency keys so a
      resumed run equals an uninterrupted run. — **XS**; depends on: 10.1, 4.2;
      parallel: no; AC: resumed run's effects/outputs equal the no-crash baseline.
- **Focused tests:** (a) kill mid-run → resume completes; (b) no step re-applied;
  (c) gated run stays parked across restart; (d) resumed run output == baseline.

## Group 11: Observability & alerts
- [ ] **11.1** Structlog events + `run_id` correlation; Prometheus metrics (runs
      by state, retries, gate dwell, parked backlog, approvals by channel,
      idempotency short-circuits, resume count). — **S**; depends on: 3.1, 5.3,
      7.3, `platform-foundation#observability`; parallel: yes; AC: metrics emit on
      the right events; logs PII-scrubbed.
- [ ] **11.2** Alerts: parked backlog threshold, gate dwell > TTL, token-verify
      failure spike. — **XS**; depends on: 11.1; parallel: yes; AC: alert fires in
      a synthetic breach test.
- **Focused tests:** (a) parked backlog gauge increments on park; (b) gate dwell
  recorded on resolve; (c) logs contain no PII; (d) token-failure counter
  increments on bad token.

## Group 12: Docs & CI discipline
- [ ] **12.1** Engine doc (`docs/workflows/_engine.md`) + per-workflow spec
      template (trigger, steps, gates, side effects, idem keys, failure handling);
      CI check that tool schemas have descriptions (FR-18, NFR-10). — **XS**;
      depends on: 9.1, 9.2; parallel: yes; AC: CI fails if a tool lacks a
      description or the engine doc drifts from the state machine.
- **Focused tests:** (a) CI flags an undescribed tool; (b) doc presence check
  passes for the engine.

---

## Dependency graph (intra-spec + cross-spec)

```
platform-foundation#persistence ─► 2.1 ─► 2.3 ─┬─► 3.1 ─► 3.2
platform-foundation#secret-store ─► 7.1        │     ├─► 4.2
platform-foundation#rbac ─► 7.3, 9.2           │     └─► 3.3
platform-foundation#observability ─► 11.1      │
connector-framework#error-taxonomy ─► 5.1      │
connector-framework#webhook-event ─► 6.2       │
                                               │
2.2 ─► 2.3                                      │
1.1 ─► 1.2 ─► 3.1 ;  1.1 ─► 1.3 ─► 6.1, 9.1     │
4.1 ─► 4.2 ;  4.1 ─► 3.1                         │
3.1 ─► 5.1 ─► 5.2 ─► 5.3 ─► 5.4                  │
6.1 ─► 6.2 ;  6.1 ─► 9.1                         │
7.1 ─► 7.2 ─► 7.3 ─► 8.1 ─► {8.2, 8.3, 8.4}      │
{3.1,5.3,7.2} ─► 10.1 ─► 10.2                    │
{3.1,5.3,7.3} ─► 11.1 ─► 11.2                    │
{9.1,9.2} ─► 12.1
```

**Critical path:** `platform-foundation#persistence` → 2.1 → 2.3 → 3.1 → 7.2 →
7.3 → 8.x → 10.1. **Parallel clusters:** {G1, G4}, {8.2, 8.3, 8.4}, {9.1, 9.2},
{11.x, 12.x}.

## Definition of done (code + tests + docs)

A task/group is **done** only when:
- **Code:** implemented against the PTD stack; passes `ruff` + `mypy`; no secrets
  in source; ≤ ~400 LOC per component (split if larger, per CONVENTIONS §3.5).
- **Tests:** the group's 2–8 focused tests pass; the relevant properties in
  `pbt-properties.md` pass; security checks in `security-audit-prep.md` that
  apply to the group are exercised.
- **Docs:** engine + per-workflow spec hooks updated; tool schemas described and
  CI-checked (FR-18, NFR-10); any `ASSUMPTION (confirm)` resolved or still
  visibly flagged.
