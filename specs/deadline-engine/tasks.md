# Tasks — Deadline Engine

> Slug: `deadline-engine` · Wave 1 · **Liability-critical**. Sizes: `XS/S/M/L`
> (CONVENTIONS §3.3, §10). Cross-spec deps use `<slug>#<id>`. Definition of done
> = code + tests + docs. Builds on `spec.md` and `concept/PTD.md §8`.

## Overview (task groups + critical path + parallelisation)

Eight groups. The **critical path** is: G1 (types + rule store) → G2 (calendar) →
G3 (computation) → G4 (persistence + scheduling) → G5 (firing + escalation) → G8
(resources + docs). G6 (dead-man's-switch) and G7 (reconciliation) are
defence-in-depth and run **parallel** to G5 once G4 lands. The highest-stakes
correctness work — deterministic business-day computation, never-silent-fail
scheduling, and the past-due-on-create guard — lives in G2–G5 and is gated by the
PBTs in `pbt-properties.md`.

```
G1 ─► G2 ─► G3 ─► G4 ─► G5 ─► G8
                   │     ▲
                   ├─► G6 ┘   (dead-man's-switch, parallel)
                   └─► G7      (reconciliation, parallel)
```

Cross-spec prerequisites (must exist as contracts before integration):
- `platform-foundation#domain-model` (`Deadline`), `#persistence`,
  `#audit-writer`, `#config-flags`, `#observability`, `#rbac`, `#pii-redaction`.
- `workflow-orchestration#scheduler`, `#run-store`, `#trigger-intake`.
- `connector-framework#case-connector`, `#notification-port`, `#connector-error`.

---

## Group 1: Rule schema, validation & the versioned rule store
The declarative rule sets and their tamper-evident versioning (spec §3, §4.4).

- [ ] **1.1** Define Pydantic v2 types: `RuleRef`, `DeadlineRule`, `Offset`,
  `ReminderOffset`, `EscalationPolicy`, `EscalationLevel`, `ComputationTrace`,
  `ScheduledDeadline`, `ScheduledReminder`.
  — size: **M** · depends on: `platform-foundation#domain-model` · parallel: no
  - **Acceptance:** all types are Pydantic v2; `DeadlineRule` round-trips
    YAML↔model; `assumption_unconfirmed` defaults `True`; every type serialises
    to a stable JSON schema.
- [ ] **1.2** `RuleStore` with content-hash versioning: `load(yaml)`,
  `active(rule_id, jurisdiction)`, `versions(rule_id)`; append-only registry;
  `rule_version` = content hash.
  — size: **M** · depends on: 1.1, `platform-foundation#persistence` · parallel: no
  - **Acceptance:** identical content ⇒ identical version (idempotent load);
    changed content ⇒ new version, old retained; registry is append-only (no
    update/delete path); load/activate is admin-RBAC-scoped and audited.
- [ ] **1.3** Rule loader **fail-closed** validation: malformed/ambiguous YAML →
  typed `RuleValidationError`; rule not loaded; rejection audited + alerted.
  — size: **S** · depends on: 1.2 · parallel: yes
  - **Acceptance:** a bad rule in a set does not silently load the good ones
    without surfacing the rejection; unknown `trigger`/`calendar_id` rejected.

**Focused tests (G1):** (a) YAML round-trip; (b) identical content → same hash;
(c) changed content → new version + old retained; (d) registry append-only;
(e) malformed rule rejected & audited; (f) unknown trigger field rejected.

---

## Group 2: Calendar abstraction (business days & holidays)
Never-naive date math, versioned holiday tables (spec §4.4, §6).

- [ ] **2.1** `Calendar` abstraction: `is_business_day`, `next_business_day`,
  `previous_business_day`, `add_business_days`, `version(calendar_id)`.
  — size: **M** · depends on: 1.1 · parallel: no
  - **Acceptance:** weekends + holidays are non-business; `add_business_days`
    skips them; pure/stateless given a `calendar_version`.
- [ ] **2.2** Versioned holiday tables: `us_federal` baseline as data;
  `calendar_table` persistence; missing-year → loud failure, never a guess.
  — size: **M** · depends on: 2.1, `platform-foundation#persistence` · parallel: yes
  - **Acceptance:** a computation references a specific `calendar_version`; a
    request for an unpopulated year raises rather than guessing; US federal
    holidays for the configured horizon present. *(ASSUMPTION (confirm): any
    immigration-specific calendars beyond US federal.)*
- [ ] **2.3** Timezone/DST handling: store UTC, compute in the calendar's
  configured timezone, "before due"/ordering on UTC instants.
  — size: **S** · depends on: 2.1 · parallel: yes
  - **Acceptance:** DST transition dates compute correctly; no off-by-one across
    a DST boundary. *(ASSUMPTION (confirm): EOIR/DOS court-local cutoffs.)*

**Focused tests (G2):** weekend-adjust; holiday-adjust; `add_business_days`
across a holiday; missing-year raises; DST-boundary correctness; calendar
version pinning.

---

## Group 3: Computation engine & `deadline.compute`
The pure, auditable date derivation (spec §4.1, §5).

- [ ] **3.1** Computation core: `raw_due = trigger + offset` (business-day-aware
  when unit is business) → `due_at = adjust(raw_due, calendar)`; emit full
  `ComputationTrace`.
  — size: **M** · depends on: 2.1, 1.1 · parallel: no
  - **Acceptance:** every `due_at` is reconstructable from `trace` + rule
    version + calendar version; missing trigger input → typed error (no invented
    date); `engine_version` recorded.
- [ ] **3.2** `deadline.compute` MCP tool (risk tier **read**): resolve active
  rule version → compute → return `{due_at, trace, reminders_preview, past_due_flag}`;
  writes a computation audit record but **persists no `Deadline`**.
  — size: **S** · depends on: 3.1, `platform-foundation#audit-writer` · parallel: no
  - **Acceptance:** no side effects beyond the audit record; self-describing
    schema; past-due input is flagged not hidden.

**Focused tests (G3):** business vs calendar-day offsets; missing trigger →
error; trace reconstructs due date; compute persists nothing; preview reminders
match rule.

---

## Group 4: Persistence, `deadline.schedule` & redundant arming
Durable records and idempotent, redundant reminder scheduling (spec §4.1, §8).

- [ ] **4.1** Persistence: `scheduled_deadline`, `scheduled_reminder`
  (unique `idempotency_key`), Alembic migrations.
  — size: **M** · depends on: 1.1, `platform-foundation#persistence` · parallel: no
  - **Acceptance:** schema matches spec §8; `idempotency_key` uniqueness enforced
    at the DB level; trace stored as JSONB.
- [ ] **4.2** `deadline.schedule` MCP tool (risk tier **write (confirm)**):
  compute → past-due branch (flag, non-armed, emit `deadline.past_due_on_create`)
  vs normal branch (persist, arm); idempotent on `idempotency_key`.
  — size: **L** *(split: persist vs arm vs idempotency)* · depends on: 3.1, 4.1,
  `workflow-orchestration#scheduler` · parallel: no
  - **Acceptance:** past-due-on-create is **never** silently persisted as armed;
    repeat `idempotency_key` is a no-op returning the existing record; each
    reminder armed via **two independent** scheduler paths sharing one idem key.
- [ ] **4.3** Reminder `fire_at` resolution: each reminder must be `< due_at`,
  else `skipped_past` + flag; remaining future reminders still arm.
  — size: **S** · depends on: 4.2 · parallel: yes
  - **Acceptance:** a past reminder offset is marked `skipped_past`, not fired;
    future reminders unaffected; reminders ordered ascending by `fire_at`.

**Focused tests (G4):** idempotent re-schedule (no-op); past-due-on-create
flagged + not armed; double-arm shares idem key; `skipped_past` reminder; DB
unique-constraint on idem key; restart preserves armed reminders.

---

## Group 5: Reminder firing & escalation
The never-silent-fail dispatch path (spec §5, §6).

- [ ] **5.1** Reminder firing job: idempotency check on `(deadline_id, offset)`
  (Redis) → first delivery calls `NotificationPort`, sets reminder `fired`,
  `Deadline.status = reminded`, audits; redundant second arm → no-op.
  — size: **M** · depends on: 4.2, `connector-framework#notification-port` ·
  parallel: no
  - **Acceptance:** a reminder is delivered **exactly once** despite redundant
    arming; notification failure retries then escalates (never dropped); status
    transition `pending→reminded` recorded.
- [ ] **5.2** Escalation engine: triggers `reminder_missed | approaching |
  after_due`; `escalation_level = max(current, level_index)` (monotonic);
  widen recipients; on `after_due` unsatisfied → `Deadline.status = missed` +
  safety incident.
  — size: **M** · depends on: 5.1 · parallel: no
  - **Acceptance:** escalation level never decreases; recipient set only widens;
    a missed deadline raises a paged safety incident; idempotent per level.
- [ ] **5.3** Completion handling: matter/case marks done → cancel remaining
  armed reminders; `Deadline.status = done` (terminal).
  — size: **S** · depends on: 5.1 · parallel: yes
  - **Acceptance:** no reminder fires after `done`; armed reminders cancelled;
    transition audited.

**Focused tests (G5):** exactly-once delivery under redundant arm; notify-fail →
retry → escalate; escalation monotonicity; missed → incident; done cancels
reminders.

---

## Group 6: Dead-man's-switch (scheduler liveness) — *parallel to G5*
Watch the watcher (spec §5, §10).

- [ ] **6.1** `Deadman` monitor on an **independent** timer: checks
  `Scheduler.heartbeat()` age vs threshold; staleness → paged safety incident.
  — size: **M** · depends on: 4.2, `platform-foundation#observability` · parallel: yes
  - **Acceptance:** monitor is not co-scheduled with the scheduler it watches; a
    stopped scheduler trips an alert within the threshold; alert is paged, not a
    silent metric.

**Focused tests (G6):** stopped scheduler → alert within threshold; healthy
heartbeat → no alert; monitor independence (kills scheduler, monitor survives).

---

## Group 7: Reconciliation against the case system — *parallel to G5*
Detect drift; alert-only (spec §5, §6).

- [ ] **7.1** `Reconciler.reconcile(matter|batch)`: fetch
  `CaseConnector.list_deadlines` → classify `Drift` (missing-in-engine,
  missing-in-case, divergent `due_at`, divergent status) → findings + audit +
  metric; **never** auto-mutates the source of record.
  — size: **M** · depends on: 4.1, `connector-framework#case-connector` · parallel: yes
  - **Acceptance:** injected drift is detected for each type; a `ConnectorError`
    (auth/fatal) **parks** reconciliation and alerts — absence of case data is
    **never** treated as "no drift"; transient errors retry with backoff.
    *(ASSUMPTION (confirm): alert-only vs auto-resolve policy + cadence.)*

**Focused tests (G7):** each drift type detected; connector-down → park + alert
(not "no drift"); transient → retry; no mutation of source of record.

---

## Group 8: Resources, self-description & docs/CI
Close FR-17/FR-18, NFR-10 (spec §4.2; PTD §16).

- [ ] **8.1** `deadline-rules://{jurisdiction}` resource: active rule set with
  per-rule content hash + aggregate set version + `assumption_unconfirmed` flags;
  RBAC-scoped.
  — size: **S** · depends on: 1.2 · parallel: yes
  - **Acceptance:** tamper-evident (hashes present); unconfirmed rules clearly
    flagged; honours requester RBAC.
- [ ] **8.2** `calendar://upcoming` resource: `Deadline`s + pending reminders in
  a window (default 90d; `?window=`, `?matter_id=`), ordered; RBAC-scoped.
  — size: **S** · depends on: 4.1 · parallel: yes
  - **Acceptance:** window + matter filters work; ordered by `due_at`/`fire_at`;
    no out-of-scope matters returned.
- [ ] **8.3** Observability + docs/CI: metrics/spans/alerts per spec §9; feature
  doc `docs/workflows/deadline-engine.md`; CI fails if a tool lacks a
  schema/description or a rule lacks a doc entry.
  — size: **M** · depends on: 3.2, 4.2 · parallel: yes
  - **Acceptance:** all spec §9 metrics emitted; alerts wired
    (dead-man's-switch, missed, past-due-on-create, drift, rule-load fail); CI
    red on schema drift (NFR-10).

**Focused tests (G8):** rules resource exposes hashes + flags; calendar window
filter; RBAC scoping on both resources; CI red on missing tool description.

---

## Dependency graph (intra-spec + cross-spec)

```
Intra-spec:
  1.1 ─► 1.2 ─► 1.3
  1.1 ─► 2.1 ─► 2.2 ; 2.1 ─► 2.3
  2.1 ─► 3.1 ─► 3.2
  3.1 ─► 4.1 ─► 4.2 ─► 4.3
  4.2 ─► 5.1 ─► 5.2 ; 5.1 ─► 5.3
  4.2 ─► 6.1            (parallel)
  4.1 ─► 7.1            (parallel)
  1.2 ─► 8.1 ; 4.1 ─► 8.2 ; {3.2,4.2} ─► 8.3

Cross-spec (must exist as contracts):
  1.1  → platform-foundation#domain-model
  1.2,2.2,4.1 → platform-foundation#persistence
  3.2  → platform-foundation#audit-writer
  4.2  → workflow-orchestration#scheduler
  5.1  → connector-framework#notification-port
  7.1  → connector-framework#case-connector (+ #connector-error)
  6.1  → platform-foundation#observability
```

**Parallelisation:** after G4 lands, G5 (dispatch), G6 (dead-man's-switch) and
G7 (reconciliation) proceed concurrently — they share no code dependency. Within
G2, 2.2/2.3 parallel after 2.1. G8 tasks parallelise once their producers land.

## Definition of done (code + tests + docs)

A task/group is **done** only when:
- **Code:** implemented against `spec.md` contracts; ≤ ~400 LOC per component
  (split if larger, CONVENTIONS §3.5); **no naive date math** (always via
  `Calendar`); fail-loud, never fail-silent on the unknown.
- **Tests:** its focused tests pass **and** the relevant invariants in
  `pbt-properties.md` pass (business-day adjustment, recompute determinism,
  reminder ordering + all-before-due, never-silent-past-due, escalation
  monotonicity, reconciliation detects drift).
- **Docs:** `docs/workflows/deadline-engine.md` updated; every tool/resource
  self-describes (FR-17); `ASSUMPTION (confirm)` items remain listed until the
  firm confirms the rule contents, calendars, and reconciliation policy.
