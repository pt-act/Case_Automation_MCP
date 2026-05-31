# Spec — Deadline Engine

> Feature slug: `deadline-engine` · Wave 1 · **Liability-critical**
> Behavioural contract. Inherits `_shared/CONVENTIONS.md`; builds on
> `concept/PTD.md §8` (deadline engine), §4 (domain model), §5 (MCP surface).
> Traces: PRD **FR-12**, **NFR-3**, NFR-1/2/7/8.

## 1. Summary

The Deadline Engine is a first-class, independently-monitored subsystem that
computes, persists, reminds on, and escalates date obligations from declarative,
versioned rule sets, and **never silent-fails**. It computes dates with
business-day calendars and holiday tables (never naive math), keeps every
computed date auditable back to its rule version + inputs, arms redundant
reminders via a scheduler abstraction, escalates on slippage, watches its own
scheduler with a dead-man's-switch, and reconciles its deadlines against the
case system of record. It exposes two MCP tools (`deadline.compute`,
`deadline.schedule`) and two resources (`deadline-rules://{jurisdiction}`,
`calendar://upcoming`).

US federal immigration is the first encoded jurisdiction, but **rules are data,
not code** (CONVENTIONS §4, §8): the firm's specific offsets/triggers are
authored as rule data and flagged `ASSUMPTION (confirm)` until confirmed.

## 2. Scope & out-of-scope

**In scope:** rule-set schema + loader + content-hash versioning; deterministic
business-day computation with auditable derivation; reminder scheduling with
redundancy and idempotency; escalation with monotonic level + widening
recipients; dead-man's-switch scheduler-liveness monitoring; reconciliation
against the case port; the two tools + two resources.

**Out-of-scope:** specific US immigration rule *contents* beyond illustrative
examples (`ASSUMPTION (confirm)`); the scheduler backend choice (Celery vs
APScheduler — spec against the `Scheduler` abstraction); notification *delivery*
internals (uses the notification/email port); the durable state-machine engine
and gate plumbing (`workflow-orchestration`); the case/CRM/email/doc adapters
(`connector-framework`); domain model, persistence, audit-log, secrets,
observability primitives (`platform-foundation`). Full detail in
`planning/requirements.md §3`.

## 3. Domain types used / introduced

**Used (from PTD §4, owned by `platform-foundation`):**
- `Deadline { id, matter_id, name, due_at, rule_id, status: pending|reminded|done|missed, escalation_level }`.
  This engine is the primary writer of `Deadline`. `rule_id` is extended in
  usage to reference a *versioned* rule (see `RuleRef` below); if the core
  `Deadline.rule_id` cannot carry a version, the version is stored in the
  engine's own `ScheduledDeadline` record and linked.
- `Matter` (read, for reconciliation context), `Contact` (recipient resolution
  only via notification port — not redefined here).

**Introduced (engine-local, proposed for reuse where shared — raise shared types
in `platform-foundation` per CONVENTIONS §5):**

```python
class RuleRef(BaseModel):
    rule_id: str              # logical id, e.g. "rfe_response_window"
    rule_version: str         # content hash of the rule definition (immutable)
    jurisdiction: str         # e.g. "US"
    practice_area: str | None

class DeadlineRule(BaseModel):          # parsed from YAML, validated on load
    rule_id: str
    jurisdiction: str
    practice_area: str | None
    trigger: str                        # name of the trigger date field
    offset: Offset                      # {days|business_days|weeks|months|years: int}
    adjust: Literal["next_business_day","previous_business_day","none"] = "next_business_day"
    calendar_id: str = "us_federal"     # named business-day/holiday calendar
    reminders: list[ReminderOffset]     # e.g. [-90d, -30d, -7d, -1d]
    escalation: EscalationPolicy
    description: str
    assumption_unconfirmed: bool = True # set when rule content is ASSUMPTION (confirm)

class Offset(BaseModel):
    days: int = 0; business_days: int = 0
    weeks: int = 0; months: int = 0; years: int = 0

class ReminderOffset(BaseModel):
    amount: int                         # negative = before due
    unit: Literal["days","business_days"]
    label: str | None

class EscalationPolicy(BaseModel):
    levels: list[EscalationLevel]       # ordered; index = level

class EscalationLevel(BaseModel):
    trigger: Literal["reminder_missed","approaching","after_due"]
    after: ReminderOffset | None        # when this level activates
    recipients: list[str]               # role refs, resolved via notification port

class ComputationTrace(BaseModel):      # the auditable derivation of a date
    rule_ref: RuleRef
    trigger_field: str; trigger_value: datetime
    offset: Offset; raw_due: datetime   # pre-adjustment
    calendar_id: str; calendar_version: str
    adjustment: str; due_at: datetime   # final
    computed_at: datetime; engine_version: str

class ScheduledDeadline(BaseModel):     # engine's persisted record
    deadline: Deadline
    rule_ref: RuleRef
    trace: ComputationTrace
    reminders: list[ScheduledReminder]
    flagged_past_due_on_create: bool

class ScheduledReminder(BaseModel):
    id: str; deadline_id: str
    fire_at: datetime; offset: ReminderOffset
    status: Literal["armed","fired","missed","skipped_past"]
    idempotency_key: str                # f"{deadline_id}:{offset}"
    redundant_arm_ids: list[str]         # independent scheduling handles
```

## 4. Interfaces (MCP tools/resources/prompts, ports, internal APIs)

### 4.1 MCP tools

**`deadline.compute`** — risk tier **read** (no side effects; PTD §5.1).
- Input: `{ rule_id, jurisdiction, trigger_inputs: dict[str, datetime], practice_area?, as_of? }`.
- Behaviour: resolve active `DeadlineRule` version → compute `raw_due` from
  `trigger + offset` → apply `adjust` against `calendar_id` → return
  `{ due_at, ComputationTrace, reminders_preview, past_due_flag }`.
- Pure read: writes an audit record of the computation but persists no
  `Deadline` and arms nothing.

**`deadline.schedule`** — risk tier **write** (PTD §5.1; reversible — a
scheduled deadline can be cancelled/superseded).
- Input: `{ matter_id, rule_id, jurisdiction, trigger_inputs, name, idempotency_key, practice_area? }`.
- Behaviour: compute (as above) → if `due_at` is past at create-time, return a
  **flagged, non-armed** `ScheduledDeadline` (`flagged_past_due_on_create=true`,
  `status` not armed) and raise a "past-due on create" event (REQ-8) — do **not**
  silently persist an un-remindable deadline; otherwise persist
  `ScheduledDeadline`, arm all reminders redundantly, write audit, return record.
- Idempotent on `idempotency_key`: a repeat is a no-op returning the existing
  record (NFR-3).

### 4.2 MCP resources (read-only)

- **`deadline-rules://{jurisdiction}`** — returns the active rule set for the
  jurisdiction: list of `DeadlineRule` (with `rule_version`, `assumption_unconfirmed`
  flags). Tamper-evident: includes each rule's content hash and the set's
  aggregate version.
- **`calendar://upcoming`** — returns `Deadline`s and pending `ScheduledReminder`s
  due within a window (default 90 days; query `?window=` and `?matter_id=`),
  ordered by `due_at`/`fire_at`. Honours the requesting identity's RBAC scope.

### 4.3 Ports consumed (from `connector-framework`)

- **`CaseConnector.list_deadlines(matter_id) -> list[Deadline]`** — reconciliation
  source of record.
- **`NotificationPort.notify(recipients, payload, idem_key) -> str`** — dispatch
  for reminders/escalations. The engine owns *when/whom*; the port owns delivery.
  (`ASSUMPTION (confirm)`: exact notification port shape is defined in
  `connector-framework`/`platform-foundation`; engine depends on the abstraction.)

### 4.4 Internal abstractions

- **`Scheduler`** (abstraction; backend = Celery beat or APScheduler — open):
  `arm(fire_at, job_ref, idem_key) -> arm_id`, `cancel(arm_id)`,
  `heartbeat() -> ts`. Two independent arm paths back each reminder (redundancy).
- **`Calendar`**: `is_business_day(date, calendar_id) -> bool`,
  `next_business_day(date, calendar_id)`, `add_business_days(date, n, calendar_id)`,
  `version(calendar_id) -> str`. Holiday tables are versioned data.
- **`RuleStore`**: `load(path) -> DeadlineRule`, `active(rule_id, jurisdiction) -> DeadlineRule`,
  `versions(rule_id) -> list[RuleRef]`. Content-hash versioning; append-only.
- **`Reconciler`**: `reconcile(matter_id|batch) -> list[Drift]`.
- **`Deadman`**: monitors `Scheduler.heartbeat()`; raises incident on staleness.

## 5. Behaviour & flows (happy path + state transitions)

**Compute (read):** resolve active rule version → validate `trigger_inputs`
contain the rule's `trigger` field → `raw_due = trigger_value + offset`
(business-day-aware where offset unit is business) → `due_at =
adjust(raw_due, calendar_id)` → build `ComputationTrace` → audit → return. No
persistence.

**Schedule (write):** compute → branch:
- *past-due-on-create* → persist nothing armed; return flagged record; emit
  `deadline.past_due_on_create` event for human attention (REQ-8).
- *normal* → persist `ScheduledDeadline` (status `pending`) → for each reminder,
  compute `fire_at` (must be `< due_at`, else mark `skipped_past` and flag) →
  arm via two independent `Scheduler` paths with shared `idempotency_key` →
  audit each arm → return record.

**Reminder firing:** scheduler invokes job → idempotency check on
`(deadline_id, offset)` (Redis) → if first delivery, call `NotificationPort`,
set reminder `fired`, set `Deadline.status = reminded`, audit; redundant second
arm finds the key already consumed → no-op (NFR-3).

**Escalation:** triggered by `reminder_missed` (reminder armed but heartbeat
shows no fire by `fire_at + grace`), `approaching`, or `after_due`. Engine
selects the next applicable `EscalationLevel`, sets `escalation_level =
max(current, level_index)` (monotonic non-decreasing), widens recipients,
dispatches via port, audits. On reaching `after_due` with no completion, sets
`Deadline.status = missed` and raises a safety incident.

**Dead-man's-switch:** `Deadman` checks `Scheduler.heartbeat()` age against
threshold on its own independent timer; staleness → safety-incident alert
(scheduler liveness). This monitor is intentionally not co-scheduled with the
reminder scheduler it watches.

**Reconciliation:** for each matter (or batch), fetch
`CaseConnector.list_deadlines` and compare to engine `ScheduledDeadline`s →
classify drift (missing-in-engine, missing-in-case, divergent `due_at`,
divergent status) → emit `Drift` findings + audit + metric. Alert-only; never
auto-mutates the source of record (`ASSUMPTION (confirm)` policy).

**Deadline status transitions:**
`pending → reminded` (a reminder fired) → `done` (matter/case marks complete) ·
`pending|reminded → missed` (past due, unsatisfied). `escalation_level` only
increases. `done`/`missed` are terminal for reminders (remaining armed reminders
cancelled on `done`).

## 6. Edge cases & error handling (incl. ConnectorError handling)

- **Malformed/ambiguous rule YAML** → loader rejects with typed
  `RuleValidationError`; the rule is *not* loaded (fail-closed), an audit record
  + alert is written. A partially-valid rule set does not silently load good
  rules and drop bad ones without surfacing the rejection.
- **Missing trigger input** for the rule's `trigger` field → `compute` returns a
  typed error, no date invented.
- **Past-due on create** → flagged, non-armed (REQ-8), never silently persisted.
- **Reminder offset resolves to past** at schedule-time → mark `skipped_past`,
  flag; remaining future reminders still arm.
- **Ambiguous/duplicate rule versions** → version is the content hash; identical
  content = identical version (idempotent); changed content = new version, old
  retained (append-only, tamper-evident).
- **Holiday/calendar gaps** (e.g. unknown future-year federal holidays) →
  calendar is versioned data; a computation references a specific
  `calendar_version`; if a required year is missing, computation fails loudly
  rather than guessing.
- **Scheduler down** → dead-man's-switch fires; redundant arm path and
  reconciliation provide defence-in-depth so no reminder is silently lost.
- **`ConnectorError` during reconciliation** (auth/rate-limit/transient/fatal per
  PTD §6): transient → retry with backoff (tenacity); auth/fatal → park
  reconciliation, alert, do **not** treat absence of case data as "no drift"
  (fail-loud, not fail-silent).
- **`NotificationPort` failure** on a reminder → retry per port policy; on
  exhaustion, escalate (a missed reminder is a safety incident), never drop.
- **Clock skew / DST** → store UTC, compute in configured calendar timezone;
  reminder ordering and "before due" checks done on UTC instants
  (`ASSUMPTION (confirm)` court-local cutoffs).

## 7. Risk tiers & gates for each action

| Action | Risk tier | Gate |
|---|---|---|
| `deadline.compute` | **read** | none (no side effects) |
| `deadline.schedule` | **write (confirm)** | reversible single-confirmation; idempotent |
| Fire reminder (notify) | internal/automated | no human gate; audited; idempotent |
| Escalation dispatch | internal/automated | no human gate; audited; widens recipients |
| Rule-set load/activate | **write (confirm)** | content-hash versioned; audited; admin-scoped |
| Reconciliation finding | **read** (alert-only) | never mutates source of record (`ASSUMPTION (confirm)`) |

No action in this feature sends client-facing external comms directly — reminders
go through the notification/email port, whose external sends are gated where the
port requires (per `status-update-emails` / NFR-4). The engine itself emits
internal/operational reminders.

## 8. Data & persistence

- **`scheduled_deadline`** (Postgres): `ScheduledDeadline` incl. embedded
  `ComputationTrace` (JSONB), `rule_ref`, `flagged_past_due_on_create`.
- **`scheduled_reminder`**: one row per reminder, `status`, `idempotency_key`
  (unique), `redundant_arm_ids`.
- **`rule_version_registry`**: append-only — `rule_id, jurisdiction,
  rule_version (content hash), yaml_blob, loaded_at, loaded_by, active`.
  Tamper-evident: hash is the version; history immutable.
- **`calendar_table`**: versioned holiday tables per `calendar_id`
  (`us_federal` baseline), `calendar_version`.
- **`reconciliation_finding`**: `Drift` records with timestamp + resolution
  status.
- **Audit:** every compute, schedule, arm, fire, miss, escalation, rule
  load/activate, reconciliation finding, and dead-man's-switch incident writes a
  hash-chained audit record (`platform-foundation` audit log; NFR-2).
- **Idempotency/locks:** Redis keys per `(deadline_id, reminder_offset)` and per
  `idempotency_key` on schedule.

## 9. Observability (logs/metrics/traces for this feature)

- **Metrics (Prometheus):** `deadline_reminders_fired_total`,
  `deadline_reminders_missed_total`, `deadline_escalations_total{level}`,
  `deadline_scheduler_heartbeat_age_seconds`, `deadline_past_due_on_create_total`,
  `deadline_reconciliation_drift_total{type}`, `deadline_compute_latency_seconds`,
  `deadline_rule_load_failures_total`.
- **Logs (structlog):** correlated by `deadline_id` / `run_id` / `rule_version`;
  no PII / client content (NFR-1).
- **Traces (OTel):** spans for compute, schedule, arm, fire, reconcile.
- **Alerts:** dead-man's-switch (heartbeat stale), any reminder missed, any
  past-due-on-create, reconciliation drift, rule-load failure → page (PTD §13).

## 10. Security & privilege considerations

- **Rule-set integrity:** versions are content hashes (tamper-evident);
  registry append-only; loads are admin-scoped and audited. A changed rule can
  never masquerade as an old version.
- **Audit of every computed date:** each `due_at` is reconstructable from its
  `ComputationTrace` + the versioned rule + versioned calendar (NFR-2).
- **Scheduler liveness as a security property:** a missed reminder is treated as
  a safety incident, not a warning — alerting is mandatory (NFR-7).
- **PII minimisation:** deadlines reference matters by id; reminder payloads
  carry only what delivery requires; immigration PII (A-numbers, passport,
  status) never enters logs/metrics (NFR-1, CONVENTIONS §7).
- **AuthZ:** rule authoring/activation, scheduling, and `calendar://upcoming`
  are RBAC-scoped; the agent acts under a constrained service identity.
- Full surface enumeration in `security-audit-prep.md`.

## 11. Dependencies & integration points

- `platform-foundation` — `Deadline` type, persistence, audit, config/secrets,
  observability, RBAC.
- `workflow-orchestration` — emits/consumes triggers; `client-intake`'s
  `compute_deadlines` step calls this engine.
- `connector-framework` — `CaseConnector.list_deadlines` (reconciliation),
  notification port (dispatch), `ConnectorError` taxonomy.
- External — `Scheduler` backend (open: Celery/APScheduler), Redis, Postgres.

## 12. Test strategy (focused tests + pointer to PBT)

- **Focused tests** (per `tasks.md` groups): weekend/holiday adjustment cases;
  determinism across restart; past-due-on-create flagging; idempotent
  re-schedule; reminder ordering; escalation monotonicity; dead-man's-switch
  trips on stopped scheduler; reconciliation detects injected drift; malformed
  rule rejected; resource RBAC scoping.
- **Property-based tests** (see `pbt-properties.md`): adjustment always off
  weekends/holidays; recompute determinism; reminders ordered & all before due;
  never silently emit past-due on create; escalation level monotonic
  non-decreasing; reconciliation detects any drift.

## 13. Open questions

- Scheduler backend: Celery beat vs APScheduler (PTD §18.3) — spec is abstract;
  pick before implementing the `Scheduler` adapter.
- Notification port exact contract (recipient role resolution, delivery gating)
  — defined in `connector-framework`/`platform-foundation`.
- Reconciliation cadence + drift-resolution policy (`ASSUMPTION (confirm)`:
  alert-only baseline).
- Timezone/court-local cutoff handling for EOIR/DOS obligations
  (`ASSUMPTION (confirm)`).
- Multi-trigger rules (priority-date re-derivation) — deferred past v1
  (`ASSUMPTION (confirm)`).
- Which immigration calendars beyond US federal holidays apply
  (`ASSUMPTION (confirm)`).
