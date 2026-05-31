# Security Audit Prep — Deadline Engine

> Slug: `deadline-engine` · Wave 1 · **Liability-critical**. Re-checks this
> feature's surfaces against the CONVENTIONS §7 / PTD §12 baseline (the floor,
> not the ceiling). For this subsystem, **correctness and liveness are security
> properties**: a wrongly computed date, a silently missed reminder, or a
> tampered rule set is a malpractice/forfeiture event (PRD Risk row 2), not a
> mere defect. Threats map to NFR-1 (confidentiality/privilege), NFR-2
> (auditability), NFR-3 (reliability/no-silent-fail), NFR-4 (human control),
> NFR-6 (residency/compliance), NFR-8 (secret hygiene).

## Sensitive surfaces (data, actions, external calls)

| Surface | What it touches | Sensitivity |
|---|---|---|
| **Rule sets (`deadline-rules://{jurisdiction}`)** | The encoded statutory/regulatory logic that produces every due date | **Critical** — a wrong/tampered rule silently mis-dates every matter that uses it |
| **`deadline.compute` inputs/output** | Matter facts (trigger dates, case type, A-number), the computed `due_at` + `ComputationTrace` | **High** — immigration PII + the liability-bearing result |
| **`deadline.schedule` (write)** | Persists armed deadlines + reminders; arms the scheduler | **Critical** — the action that determines whether a deadline is actually watched |
| **Scheduler / reminder firing** | Liveness of the whole subsystem | **Critical** — silent scheduler death = missed deadlines |
| **Escalation** | Widens notification recipients on slippage | **High** — recipient correctness + PII in messages |
| **Reconciliation vs case system of record** | Reads case connector; detects drift | **High** — connector scope + drift trust |
| **Calendar / holiday tables** | Business-day math source data | **High** — a wrong holiday table mis-dates everything |
| **`calendar://upcoming` resource** | Lists matters + upcoming due dates | **High** — concentrates PII; needs RBAC scoping |
| **Audit record per compute/schedule/fire/escalate** | Hash-chained, immutable | **High** — non-repudiation of date obligations |

## Threats & mitigations (map to NFR-1, 2, 3, 4, 6, 8)

| # | Threat | Impact | Mitigation | NFR |
|---|---|---|---|---|
| T1 | Tampered or unversioned rule set silently changes computed dates | Mass mis-dating → forfeiture | Rule sets are content-hash **versioned** and tamper-evident; every computed date stores `rule_version`; rule changes go through review + ADR + docs CI (PTD §16); malformed rule rejected at load (fail-closed) | NFR-2, NFR-3 |
| T2 | Naive date math lands a deadline on a weekend/holiday | Late/missed filing | Computation is always calendar-aware; `adjust` enforced; PBT P1 (lands on business day) + P2 (determinism) gate the engine | NFR-3 |
| T3 | A reminder/escalation **silently** fails to fire (dropped job, scheduler down) | Deadline missed with no warning | Redundant arming via two independent scheduler paths (shared idempotency key); **dead-man's-switch** monitors scheduler heartbeat and alerts on silence; nothing fails silently (NFR-3) | NFR-3, NFR-4 |
| T4 | Past-due-on-create slips in unnoticed | False sense of coverage | Compute branch detects past-due, arms **nothing**, returns a flagged record and emits `deadline.past_due_on_create` for human attention; PBT P4 forbids silently arming a past reminder | NFR-3, NFR-4 |
| T5 | Drift between engine deadlines and case system of record | Two sources disagree; wrong date trusted | Periodic reconciliation reads the case connector and flags any drift; PBT P6 (reconciliation detects injected drift); *ASSUMPTION (confirm): alert-only baseline, no auto-overwrite* | NFR-3 |
| T6 | Immigration PII leaks into reminder/escalation messages, logs, or traces | Confidentiality/residency breach | Notifications carry matter refs + redacted identifiers, not raw PII; PII-redaction utility from `platform-foundation`; "no client content in telemetry" test | NFR-1, NFR-6 |
| T7 | `calendar://upcoming` / `deadline-rules://` exposes data across matters/roles | Over-broad disclosure | Resources are RBAC-scoped to the caller's permitted matters; no cross-matter enumeration; access audited | NFR-1, NFR-4 |
| T8 | Duplicate firing under retry double-sends/double-escalates | Spam, eroded trust, wrong state | Idempotency key per `(deadline_id, reminder_id, fire_window)`; firing is idempotent; PBT P7 (no duplicate effect under retry) | NFR-3 |
| T9 | Audit record missing/mutable for a computed or armed deadline | Loss of non-repudiation | Exactly-one hash-chained record per compute/schedule/fire/escalate, written **before** the action completes (PTD §12); export verified | NFR-2 |
| T10 | Non-deterministic recompute makes a disputed date unreproducible | Cannot defend the date | `due_at` is a pure function of `(rule_version, calendar_version, trigger_inputs)`; injected `now`; trace stored; PBT P2 | NFR-2 |
| T11 | Reconciliation connector scope too broad / credential misuse | Lateral data access | Case connector used read-only with least-privilege scopes; runs under the constrained service identity; tokens encrypted (inherited) | NFR-1, NFR-8 |
| T12 | Escalation widens to the wrong recipients | Misdirected sensitive info | Escalation recipients resolved from matter roles via the notification port; recipient set audited; level monotonic non-decreasing (PBT P5) | NFR-1, NFR-4 |
| T13 | Residency: PII computed/stored in a disallowed region | Compliance breach | Engine is in-region with the host; no external inference; configurable residency inherited from deployment (CONVENTIONS §4) | NFR-6 |
| T14 | Secret exposure | Credential leak | Engine reads only scheduler/connector creds from the secret store; no env-dumping; no secrets in logs/audit | NFR-8 |

## AuthZ & privilege checks

- **Identity:** all engine actions run under the **constrained service identity**
  (PTD §12). Reconciliation uses the case connector **read-only** with
  least-privilege scopes; no write-back to the case system in v1 (*ASSUMPTION
  (confirm): alert-only reconciliation*).
- **Least privilege:** the engine needs only (a) scheduler access, (b) the
  notification port, (c) read-only case-connector scope for reconciliation. It
  needs no document or email send scopes.
- **Resource scoping:** `deadline-rules://{jurisdiction}` and
  `calendar://upcoming` are read-only and RBAC-scoped — a caller sees only rules
  they may view and deadlines for matters they may access. No cross-matter
  enumeration; access is audited.
- **No autonomous external action:** the engine **drafts attention** (reminders,
  escalations, flags) but performs no irreversible client/government-facing act
  itself; any such follow-on is a separate gated workflow (NFR-4).

## Audit log coverage

- **Every** `deadline.compute`, `deadline.schedule`, reminder fire, and
  escalation writes one hash-chained record: `actor`, `action`, `inputs`
  (trigger facts fingerprint, `rule_version`, `calendar_version`), `outputs`
  (`due_at`, `ComputationTrace`, armed reminder ids — redacted), `run_id`,
  `timestamp` — before the action completes (NFR-2).
- **Liveness events** are audited and alerted: `deadline.past_due_on_create`,
  dead-man's-switch heartbeat-miss, reconciliation drift detected.
- **Rule/calendar version changes** are recorded at deploy-time via ADR +
  docs/CI (PTD §16), so the exact logic behind any historical date is
  reconstructable for defence.
- Records contain **no raw PII** (T6); a test asserts redaction on
  representative immigration PII (names, DOB, A-numbers).

## PII handling & residency

- **Immigration PII in scope:** names, DOB, A-numbers, receipt/priority dates,
  case type, country-of-origin — present in compute inputs, scheduled records,
  and the upcoming-calendar resource (CONVENTIONS §7).
- **In transit/at rest:** TLS + AES-256 inherited from the platform; scheduled
  deadlines/reminders persisted in-region.
- **In telemetry/notifications:** logs, traces, and reminder/escalation messages
  are PII-scrubbed and reference matter refs + redacted identifiers, never raw
  values.
- **Residency:** engine is compute + persistence only, in-region with the host;
  configurable data residency inherited from deployment (NFR-6, CONVENTIONS §4).
  No data egress beyond the notification port and read-only reconciliation.
- **Retention:** scheduled deadlines, reminders, and audit records follow the
  platform retention policy. *ASSUMPTION (confirm): closed-matter deadlines are
  retained per the firm's records-retention schedule, not purged on matter
  close.*

## Pre-audit checklist

- [ ] Every computed `due_at` lands on a business day per its calendar; PBT P1
      passes (T2).
- [ ] Recompute is deterministic from versioned inputs; `ComputationTrace` +
      `rule_version` + `calendar_version` stored; PBT P2 passes (T1, T10).
- [ ] Rule sets are content-hash versioned and tamper-evident; malformed rules
      rejected at load; changes reviewed + ADR'd (T1).
- [ ] No reminder/escalation can fail silently: redundant arming + dead-man's-
      switch heartbeat with alerting; PBT P3 (all reminders before due) passes
      (T3).
- [ ] Past-due-on-create arms nothing, returns a flagged record, and emits an
      event for human attention; PBT P4 passes (T4).
- [ ] Firing/escalation idempotent under retry — no duplicate sends; PBT P7
      passes (T8).
- [ ] Escalation level is monotonic non-decreasing; recipients resolved from
      matter roles and audited; PBT P5 passes (T12).
- [ ] Reconciliation detects injected drift and alerts; connector access is
      read-only + least-privilege; PBT P6 passes (T5, T11).
- [ ] Exactly one audit record per compute/schedule/fire/escalate, written
      before completion, hash-chained, no raw PII (T9, T6).
- [ ] `deadline-rules://` and `calendar://upcoming` are RBAC-scoped read-only;
      no cross-matter enumeration; access audited (T7).
- [ ] Reminder/escalation messages, logs, and traces contain no raw PII;
      redaction test passes on a PII-laden fixture (T6).
- [ ] Engine holds only scheduler/connector/notification credentials; no secrets
      in logs/audit; least-privilege service identity (T14, T11).
- [ ] Residency inherited from deployment; no out-of-region compute/storage/
      egress (T13).
- [ ] All `ASSUMPTION (confirm)` items (scheduler backend, reconciliation
      policy, court-local timezones, immigration calendars, closed-matter
      retention) are listed for the firm and not silently baked in.
