# Workflow Engine — Technical Reference

## State machine

```
queued ──► running ──► succeeded
              │   ├──► awaiting_approval ──►(approve)──► running
              │   │                         └──►(reject)──► rejected
              │   ├──►(transient, attempts<cap)──► running   [retry]
              │   └──►(fatal | attempts==cap)──► parked
              └── parked ──►(re-drive)──► running
                           └──►(cancel)──► cancelled
```

Step states: `pending → running → succeeded | failed`
`failed` loops to `running` on retry or escalates run to `parked`.
`compensated` marks a reversed step; `skipped` marks a bypassed step.

## Idempotency

Every step's external effect is keyed on `"{run_id}:{step_name}"`.
Before executing, the engine checks the IdempotencyStore:
- **miss** → execute handler, `reserve`, `record` output.
- **hit** → reuse stored output, skip handler (no re-effect).

This gives **exactly-once external effect** under at-least-once execution.

## Gate lifecycle

1. Engine hits a `GATE:*` step.
2. `GateRequest` row created; tokens issued per channel (MCP / web / email).
3. Run status → `awaiting_approval`. Worker yields.
4. Approver calls one channel's endpoint with the raw token.
5. `resolve_gate()` verifies: signature → expiry → single-use (atomic) → authz.
6. `ApprovalDecision` recorded; `GateRequest.status` updated.
7. Approve → run status → `running`, re-enqueued. Reject → `rejected`.

## Retry/compensation

- `TransientError` → retry with exponential full-jitter, max 5 attempts.
- `FatalError` or attempts exhausted → park immediately.
- On park, registered compensation hooks run in reverse order (best-effort).

## Crash recovery

Startup sweep:
- `queued` / `running` runs → re-enqueued (idempotency prevents re-effect).
- `awaiting_approval` runs → left at gate (no work to redo).

## Workflow template

Every workflow ships a doc at `docs/workflows/<name>.md`:

```markdown
# Workflow: <name>

## Trigger
## Steps
## Gates
## Side effects & idempotency keys
## Failure handling
```
