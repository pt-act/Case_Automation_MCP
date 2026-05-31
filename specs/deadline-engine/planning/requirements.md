# Requirements — Deadline Engine

> Feature slug: `deadline-engine` · Wave 1 · **Liability-critical**
> Inherits `_shared/CONVENTIONS.md` (process, domain model, risk tiers,
> compliance baseline). Builds on `concept/PRD.md` and `concept/PTD.md §8`.

## 1. Context & problem

Missed date obligations are the single highest-impact failure mode for an
immigration practice: a blown RFE/NOID window, a missed master-calendar
hearing, or a lapsed work-authorization expiration is a malpractice-grade event
(PRD §9, Risk row 2 — "Missed deadline due to engine bug → Critical
(malpractice)"). Today these dates live in people's heads, spreadsheets, and
disconnected calendars (PRD §2).

The Deadline Engine is the "liability killer" of Wave 1 (PRD §7, use case 3): a
first-class, **independently-monitored** subsystem that computes, tracks,
reminds, and escalates date obligations and **never silent-fails** (PTD §8). It
turns date obligations into declarative, versioned, auditable rule data — not
hard-coded logic — so US federal immigration is the first encoded jurisdiction
but additional rule sets are configuration, not code (CONVENTIONS §4, §8).

A computed deadline must always be traceable back to the exact rule version and
inputs that produced it, must always be adjusted off weekends/holidays per its
rule, and must never silently emit a past-due date on creation. The scheduler
that fires reminders is itself monitored by a dead-man's-switch, and the
engine's persisted deadlines are reconciled against the case system of record so
drift is detected, not discovered too late.

## 2. In scope

- **Rule-set schema, loader, and versioning** — declarative YAML rule sets per
  jurisdiction/practice area (PTD §8 shape: `trigger`, `offset`,
  `adjust`, `reminders[]`, `escalation`), validated on load, content-hashed,
  and immutably versioned. (FR-12)
- **Deadline computation** — business-day-aware date math using holiday tables
  (US federal holidays baseline), never naive arithmetic; every computed date is
  auditable back to `(rule_id, rule_version, trigger inputs, calendar version)`.
  (FR-12, NFR-2)
- **Reminder scheduling** — persist computed deadlines and arm reminders at each
  declared offset; **redundant scheduling** so a single scheduler tick loss
  cannot drop a reminder. (FR-12, NFR-3)
- **Escalation** — on slippage (approaching/after due, missed reminder), raise
  the escalation level and widen the recipient set; level is monotonic
  non-decreasing.
- **Dead-man's-switch monitoring** — detect and alert if the scheduler itself
  stops firing (a missed reminder is a safety incident). (PTD §8, §13)
- **Reconciliation** — periodically compare engine-held deadlines against the
  case system of record (via `connector-framework`) and report any drift.
- **MCP surface** — tools `deadline.compute` (read) and `deadline.schedule`
  (write); resources `deadline-rules://{jurisdiction}` and `calendar://upcoming`.
- **Notification dispatch via port** — reminders/escalations are emitted through
  the shared notification/email port; this feature owns *when/whom*, not the
  delivery internals.

## 3. Out of scope

- The specific **US immigration rule CONTENTS** beyond illustrative examples —
  actual offsets, triggers, and recipients are rule *data*, authored/confirmed
  separately. `ASSUMPTION (confirm)` (see §8).
- The **scheduler infrastructure choice** (Celery beat vs APScheduler). This
  spec targets a `Scheduler` abstraction; the concrete backend is an open
  decision (PTD §18.3). (Open question, §8)
- **Notification delivery internals** (email/SMS rendering, transport, retry of
  the *send*) — owned by the notification/email port and the
  `status-update-emails` / connector layers. This engine calls the port.
- The durable **state-machine engine, gates, and trigger plumbing** — owned by
  `workflow-orchestration`; this engine is invoked by and emits triggers into it.
- The **case/CRM/email/doc connectors themselves** — owned by
  `connector-framework`; reconciliation depends on the case port, not an adapter.
- General domain model, persistence, audit-log, secrets, observability primitives
  — owned by `platform-foundation`.

## 4. Users / actors

| Actor | Interest in this feature |
|---|---|
| **Paralegal / Case Manager** | Never miss a deadline; receive timely, escalating reminders; trust the dates. |
| **Fee-earner / Attorney** | Owns matter risk; is the escalation target; needs an audit trail of every computed date and reminder. |
| **Operations / Admin** | Authors/maintains rule sets; monitors scheduler liveness and reconciliation alerts. |
| **The AI Agent** | Calls `deadline.compute` / `deadline.schedule`; reads `deadline-rules://` and `calendar://upcoming` with predictable, typed contracts. |
| **Compliance / Auditor** | Verifies every computed date traces to rule + inputs; verifies missed-reminder incidents are logged and alerted. |

## 5. Functional requirements (trace to PRD FR-xx)

- **REQ-1 (FR-12)** Load declarative, versioned rule sets per
  jurisdiction/practice area from YAML; validate schema on load; reject and
  alert on malformed/ambiguous rules (fail-closed, never silently skip a rule).
- **REQ-2 (FR-12)** Compute a deadline `due_at` from a rule's `trigger` input +
  `offset`, then apply `adjust` (default `next_business_day`) against the
  applicable business-day calendar + holiday table. Never naive date math.
- **REQ-3 (FR-12, NFR-2)** Every computed date carries an auditable derivation:
  rule id + rule version (content hash), trigger field + value, offset, calendar
  id + version, and the adjustment applied.
- **REQ-4 (FR-12, NFR-3)** Recompute determinism: the same rule version + same
  inputs always yields the identical `due_at` (pure function of versioned data).
- **REQ-5 (FR-12)** On `deadline.schedule`, persist the `Deadline` and arm all
  declared reminders at their offsets; arming is **redundant** (independent
  scheduling paths) so one lost tick cannot drop a reminder.
- **REQ-6 (FR-12)** Reminders are ordered earliest→latest and all fire strictly
  before `due_at`; an offset that would resolve to a past instant at
  schedule-time is flagged, not silently dropped.
- **REQ-7 (FR-12)** On slippage (reminder missed, due approaching, or past due),
  escalation raises the `escalation_level` (monotonic non-decreasing) and widens
  the recipient set per the rule's `escalation` block.
- **REQ-8 (FR-12, NFR-3)** **Never silent-fail on create:** if computation would
  produce a past-due `due_at` at create time, the engine returns a flagged,
  non-armed result requiring human attention — it does not persist a silently
  past-due, un-remindable deadline.
- **REQ-9 (FR-12, NFR-7)** Dead-man's-switch: the scheduler emits a periodic
  heartbeat; absence of heartbeat beyond a threshold raises a safety incident
  alert (scheduler liveness).
- **REQ-10 (FR-12, NFR-3)** Reconciliation: periodically compare engine-held
  deadlines against the case system of record and report drift (missing,
  extra, or divergent `due_at`) without auto-mutating the source of record.
- **REQ-11 (FR-17)** Expose `deadline.compute` (read) and `deadline.schedule`
  (write) as self-describing MCP tools, and `deadline-rules://{jurisdiction}` /
  `calendar://upcoming` as MCP resources.
- **REQ-12 (FR-18)** Ship rule-set schema docs and the engine spec as part of
  "done"; rule-set examples carry their `ASSUMPTION (confirm)` flags.

## 6. Non-functional requirements (trace to PRD NFR-xx)

- **NFR-3 (Reliability)** Computation is deterministic and idempotent;
  scheduling reminders twice (retry) never produces duplicate fired reminders
  (idempotency key per `(deadline_id, reminder_offset)`). Redundant scheduling +
  dead-man's-switch ensure no silently dropped reminder.
- **NFR-2 (Auditability)** 100% of computed dates, scheduled reminders, fired
  reminders, escalations, missed-reminder incidents, and reconciliation findings
  write immutable, hash-chained audit records (per `platform-foundation`).
- **NFR-1 (Confidentiality/privilege)** Deadline records reference matters by id;
  no client content/PII (A-numbers, passport, status) in reminder metadata or
  logs beyond what is strictly required; least-privilege access to rule sets and
  deadlines.
- **NFR-7 (Observability)** Metrics for reminders fired, reminders missed,
  escalations raised, scheduler heartbeat age, reconciliation drift count;
  structured logs correlated by `run_id` / `deadline_id`.
- **NFR-8 (Secret hygiene)** No secrets in rule-set files or logs; rule files are
  config data, not credential stores.
- **NFR-5 (Latency)** `deadline.compute` is a read returning < 2s typical;
  scheduling/reconciliation run async.

## 7. Dependencies (other feature specs, external systems)

- **`platform-foundation`** — `Deadline` domain type, persistence, hash-chained
  audit log, config/secrets, observability, RBAC baseline.
- **`workflow-orchestration`** — durable run engine, gate plumbing, and trigger
  intake; the engine emits reminder/escalation triggers and is invoked from
  workflows (e.g. `client-intake#compute_deadlines`).
- **`connector-framework`** — the **case** `CaseConnector` port
  (`list_deadlines`) used by reconciliation; the notification/email port used
  for dispatch; `ConnectorError` taxonomy.
- **External:** scheduler backend (Celery beat or APScheduler — open), Redis
  (idempotency keys/locks), PostgreSQL (deadlines, rule-version registry, audit).

## 8. Assumptions & open questions

- `ASSUMPTION (confirm)`: The **specific US immigration deadline rules**
  (triggers, offsets, recipients) are unconfirmed. Illustrative-only examples
  referenced in this spec — RFE/NOID response windows, biometrics appointment
  windows, EOIR master/individual hearing dates, visa-bulletin/priority-date
  tracking, status/EAD/work-authorization expirations, filing windows — are
  placeholders to be authored as rule data once the firm confirms priority case
  types (CONVENTIONS §8).
- `ASSUMPTION (confirm)`: Business-day calendar = standard US federal holidays.
  Whether **immigration-specific calendars** apply (e.g. USCIS field-office
  closures, EOIR court closures/continuances, DOS/consular post calendars) is
  unconfirmed; the engine supports multiple named calendars as data.
- `ASSUMPTION (confirm)`: The **scheduler backend** (Celery beat vs APScheduler)
  is not finalised (PTD §18.3). Spec targets a `Scheduler` abstraction.
- `ASSUMPTION (confirm)`: Reminder **offset semantics** are business-day vs
  calendar-day per offset; default assumed calendar-day unless an offset is
  marked `business`. To confirm with the firm.
- `ASSUMPTION (confirm)`: **Timezone** for due-instants and reminder firing
  (firm-local vs matter-jurisdiction vs UTC-stored, firm-local-displayed).
  Baseline: store UTC, compute and display in a configured firm timezone; some
  obligations (e.g. EOIR filing cutoffs) may be court-local.
- `ASSUMPTION (confirm)`: Reconciliation cadence and drift-resolution policy
  (alert-only vs propose-correction). Baseline: alert-only, never auto-mutate the
  case system of record.
- `ASSUMPTION (confirm)`: Whether a deadline may have **multiple triggers**
  (e.g. priority-date movement re-deriving a window). Baseline: single trigger
  per rule v1; multi-trigger deferred.
- Open: dead-man's-switch alert routing (who is paged) — depends on
  `platform-foundation` alerting/notification config.

## 9. Acceptance criteria (testable checklist)

- [ ] A rule set in valid YAML loads, validates, and is assigned a content-hash
      version; an invalid/ambiguous rule is rejected with a typed error and an
      audit record (fail-closed, no silent skip).
- [ ] `deadline.compute` for a rule whose naive due date lands on a Saturday,
      Sunday, or US federal holiday returns a `due_at` adjusted to the next
      business day per the rule's `adjust`.
- [ ] The same `(rule_version, trigger inputs)` produces an identical `due_at`
      across repeated calls and process restarts.
- [ ] A computed result includes a complete derivation trace
      (`rule_id`, `rule_version`, trigger field+value, offset, calendar
      id+version, adjustment) and an audit record is written.
- [ ] `deadline.schedule` arms all declared reminders, ordered earliest→latest,
      all strictly before `due_at`; re-invoking with the same idempotency key
      does not double-arm.
- [ ] Creating a deadline whose `due_at` is already past returns a flagged,
      non-armed result requiring human attention — no silently past-due,
      un-remindable deadline is persisted.
- [ ] On a missed/slipped reminder, `escalation_level` increases (never
      decreases) and the recipient set widens per the rule.
- [ ] Stopping the scheduler raises a dead-man's-switch alert within the
      configured heartbeat threshold, recorded as a safety incident.
- [ ] Reconciliation detects an injected divergence between an engine deadline
      and the case-system value and reports drift without mutating the source.
- [ ] `deadline-rules://{jurisdiction}` returns the active rule set + version;
      `calendar://upcoming` returns deadlines/reminders due within the window.
- [ ] Rule-set schema docs exist and every illustrative immigration rule is
      flagged `ASSUMPTION (confirm)`.
