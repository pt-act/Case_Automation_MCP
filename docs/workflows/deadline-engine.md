# Deadline Engine — Technical Reference

## Overview
Liability-critical subsystem.  **Never silent-fail.**  Every computation is
fully auditable (ComputationTrace stores all inputs and intermediate values).

## Rule sets
Rules are **data, not code** — authored as YAML, versioned by content hash.
`rule_version = sha256(yaml)[:16]`.  Identical content → identical version
(idempotent load).  Registry is append-only (old versions never deleted).

## Business-day computation
All date math goes through the `Calendar` abstraction.  No `timedelta(days=n)`
applied directly to business-day offsets.  Missing holiday data for a year →
loud failure, never a guess.

## deadline.compute (read — no side effects)
Resolves the active rule → computes raw_due → adjusts to business day →
returns `{due_at, trace, reminders_preview, past_due_flag}`.
Writes one audit record; persists no Deadline row.

## deadline.schedule (write — idempotent)
- **Past-due-on-create**: if `due_at < now` → flagged, non-armed, raises
  `PastDueOnCreateError` for human attention.  Never silently persisted.
- **Reminder arming**: each reminder is armed via **two independent** scheduler
  paths sharing one idempotency key.
- **Idempotency**: same `idempotency_key` → returns existing record, no re-arm.

## Reminder firing
Exactly-once delivery via Redis dedup key `cam:reminder_fired:{idem_key}`.
Redundant second arm finds key already consumed → no-op.

## Escalation
`escalation_level` is monotonically non-decreasing.  Recipient set only widens.
Past-due + unsatisfied → `Deadline.status = missed` + safety incident log.

## Dead-man's-switch
Runs on an INDEPENDENT timer (not co-scheduled with the reminder scheduler).
Heartbeat age > threshold → paged safety incident.

## Reconciliation
Alert-only; never auto-mutates source of record.
ConnectorError → park reconciliation + alert.
Absence of case data is NEVER treated as "no drift".

## ASSUMPTION (confirm)
- Specific rule sets (RFE windows, EOIR hearing dates, etc.)
- Holiday calendars beyond US federal
- Reconciliation cadence and drift-resolution policy
- Staleness threshold for dead-man's-switch (default 300s)
