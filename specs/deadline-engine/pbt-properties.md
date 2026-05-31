# Property-Based Tests — Deadline Engine

> Slug: `deadline-engine` · Baseline: **Hypothesis** (CONVENTIONS §4). These
> properties assert invariants that must hold for **all** inputs, complementing
> the focused tests in `tasks.md`. They cover the PBT FOCUS: business-day
> adjustment, recompute determinism, reminder ordering + all-before-due,
> never-silently-emit-past-due-on-create, escalation monotonicity, and
> reconciliation detects drift. This subsystem is liability-critical (PRD Risk
> row 2), so these properties are gating, not advisory. Pseudocode is
> illustrative, not final code.

## Invariants under test (plain English)

- **P1 — Adjustment lands on a business day.** For any rule with
  `adjust = next_business_day | previous_business_day`, the final `due_at` is a
  business day in its calendar (never a weekend or holiday). *(traces FR-12;
  spec §5, §6)*
- **P2 — Recompute determinism.** Identical `(rule_version, calendar_version,
  trigger_inputs)` ⇒ identical `due_at` and `ComputationTrace`. Recomputation is
  a pure function of versioned inputs. *(NFR-3; spec §5)*
- **P3 — Trace reconstructs the date.** The `due_at` is fully derivable from its
  `ComputationTrace` + the versioned rule + versioned calendar — no hidden
  state. *(NFR-2; spec §10)*
- **P4 — Reminders ordered and all before due.** For a scheduled deadline, every
  *armed* reminder's `fire_at < due_at`, and reminders are strictly ordered;
  any offset resolving to ≥ `due_at` is `skipped_past`, never armed. *(spec §5)*
- **P5 — Never silently emit past-due on create.** If `due_at <= now` at
  schedule time, the result is `flagged_past_due_on_create = True`, not armed,
  and emits a `deadline.past_due_on_create` event — it is never silently
  persisted as a normal armed deadline. *(spec §5, REQ-8)*
- **P6 — Reminder delivery is exactly once.** Despite redundant (two-path)
  arming and retries, a reminder for `(deadline_id, offset)` dispatches at most
  once. *(NFR-3; spec §5)*
- **P7 — Escalation level is monotonic non-decreasing.** Across any sequence of
  escalation events, `escalation_level` only ever increases or stays equal, and
  recipient sets only widen (never shrink). *(spec §5)*
- **P8 — Deadline status transitions are legal.** Status moves only along
  `pending → reminded → done` and `pending|reminded → missed`; `done`/`missed`
  are terminal; no reminder fires after a terminal state. *(spec §5)*
- **P9 — Reconciliation detects any drift.** If the engine's `ScheduledDeadline`
  set differs from the case system's `list_deadlines` in membership, `due_at`,
  or status, `reconcile` returns ≥1 `Drift`; identical sets ⇒ no drift. A
  connector failure is **never** reported as "no drift". *(spec §5, §6)*
- **P10 — Rule version integrity.** `rule_version` equals the content hash:
  identical content ⇒ identical version; any content change ⇒ a different
  version, with the old version still retrievable (append-only). *(spec §8, §10)*
- **P11 — Idempotent schedule.** Calling `deadline.schedule` twice with the same
  `idempotency_key` yields one persisted `ScheduledDeadline` and one set of armed
  reminders (the second call is a no-op returning the first). *(NFR-3)*

## Properties (Hypothesis-style pseudocode)

```python
from hypothesis import given, strategies as st, settings

# --- P1: adjustment lands on a business day -------------------------------
@given(rule=rules(adjust=st.sampled_from(["next_business_day","previous_business_day"])),
       trig=trigger_inputs())
def test_adjustment_is_business_day(rule, trig):
    res = compute(rule, trig)
    cal = calendar(rule.calendar_id, res.trace.calendar_version)
    assert cal.is_business_day(res.due_at)

# --- P2: recompute determinism --------------------------------------------
@given(rule=rules(), trig=trigger_inputs())
@settings(max_examples=300)
def test_recompute_determinism(rule, trig):
    a = compute(rule, trig)
    b = compute(rule, trig)                      # same versioned inputs
    assert a.due_at == b.due_at
    assert canonical(a.trace) == canonical(b.trace)

# --- P3: trace reconstructs the date --------------------------------------
@given(rule=rules(), trig=trigger_inputs())
def test_trace_reconstructs_due(rule, trig):
    res = compute(rule, trig)
    rebuilt = reconstruct_due_from_trace(res.trace)   # uses only trace + versions
    assert rebuilt == res.due_at

# --- P4: reminders ordered & all armed ones before due --------------------
@given(rule=rules(), trig=future_trigger_inputs())   # ensures due_at > now
def test_reminders_before_due_and_ordered(rule, trig):
    sched = schedule(rule, trig, now=fixed_now)
    armed = [r for r in sched.reminders if r.status == "armed"]
    fire_times = [r.fire_at for r in armed]
    assert fire_times == sorted(fire_times)          # strictly ordered
    assert all(ft < sched.deadline.due_at for ft in fire_times)
    for r in sched.reminders:
        if r.status == "skipped_past":
            assert resolve_fire_at(r, sched) >= sched.deadline.due_at

# --- P5: never silently emit past-due on create ---------------------------
@given(rule=rules(), trig=past_trigger_inputs())     # forces due_at <= now
def test_past_due_on_create_is_flagged_not_armed(rule, trig):
    with capture_events() as events:
        sched = schedule(rule, trig, now=fixed_now)
    assert sched.flagged_past_due_on_create is True
    assert all(r.status != "armed" for r in sched.reminders)
    assert any(e.type == "deadline.past_due_on_create" for e in events)

# --- P6: reminder delivery is exactly once --------------------------------
@given(rule=rules(), trig=future_trigger_inputs(), arm_paths=st.integers(1, 3))
def test_reminder_exactly_once(rule, trig, arm_paths):
    sched = schedule(rule, trig, now=fixed_now)
    with capture_notifications() as sent:
        for _ in range(arm_paths):                   # redundant arms + retries
            fire_due_reminders(sched, now=first_fire_at(sched))
    per_offset = group_by(sent, key=lambda s: (s.deadline_id, s.offset))
    assert all(len(v) == 1 for v in per_offset.values())

# --- P7: escalation level monotonic, recipients widen ---------------------
@given(events=escalation_event_sequences())
def test_escalation_monotonic(events):
    level, recips = 0, set()
    for ev in events:
        new_level, new_recips = apply_escalation(level, recips, ev)
        assert new_level >= level
        assert recips <= new_recips                  # only widens
        level, recips = new_level, new_recips

# --- P8: legal status transitions only ------------------------------------
@given(transitions=status_event_sequences())
def test_status_transitions_legal(transitions):
    legal = {("pending","reminded"),("reminded","done"),("pending","done"),
             ("pending","missed"),("reminded","missed")}
    state = "pending"
    fired_after_terminal = False
    for t in transitions:
        nxt = step_status(state, t)
        if state in {"done","missed"}:
            assert nxt == state                      # terminal
            if t == "reminder_fire": fired_after_terminal = True
        else:
            assert (state, nxt) in legal or nxt == state
        state = nxt
    assert not fired_after_terminal

# --- P9: reconciliation detects any drift ---------------------------------
@given(engine_set=deadline_sets(), case_set=deadline_sets())
def test_reconciliation_detects_drift(engine_set, case_set):
    drifts = reconcile(engine_set, case_set)
    if normalise(engine_set) == normalise(case_set):
        assert drifts == []
    else:
        assert len(drifts) >= 1
    # connector failure is never silently "no drift"
    with case_connector_raising(ConnectorError("auth")):
        assert reconcile_live(matter_id="m1").status == "parked"   # not []

# --- P10: rule version integrity ------------------------------------------
@given(rule_a=rule_yaml(), mutation=st.text(min_size=1))
def test_rule_version_is_content_hash(rule_a, mutation):
    v1 = rule_store.load(rule_a).rule_version
    v1b = rule_store.load(rule_a).rule_version
    assert v1 == v1b                                 # idempotent
    rule_b = apply_semantic_change(rule_a, mutation)
    v2 = rule_store.load(rule_b).rule_version
    assert v2 != v1
    assert rule_store.get(v1) is not None            # old retained

# --- P11: idempotent schedule ---------------------------------------------
@given(rule=rules(), trig=future_trigger_inputs(), key=idem_keys())
def test_schedule_idempotent(rule, trig, key):
    s1 = schedule(rule, trig, now=fixed_now, idempotency_key=key)
    s2 = schedule(rule, trig, now=fixed_now, idempotency_key=key)
    assert s1.deadline.id == s2.deadline.id
    assert reminder_ids(s1) == reminder_ids(s2)      # no duplicate arming
    assert persisted_count(key) == 1
```

## Generators / input domains

- **`rules(...)`** — `DeadlineRule`s with `offset` drawn across
  days/business_days/weeks/months/years (incl. 0 and large values), `adjust` ∈
  {next_business_day, previous_business_day, none}, `reminders` lists with mixed
  negative offsets (some resolving before, some after `due_at`), and varied
  `escalation` policies.
- **`trigger_inputs()` / `future_trigger_inputs()` / `past_trigger_inputs()`** —
  trigger date maps; the future/past variants constrain `due_at` relative to a
  fixed injected `now` to exercise P4/P5 deterministically.
- **`calendar(...)`** — `us_federal` plus synthetic calendars with injected
  holidays (including holidays adjacent to weekends and multi-day clusters).
- **`escalation_event_sequences()`** — ordered sequences mixing
  `reminder_missed`/`approaching`/`after_due` to probe monotonicity.
- **`status_event_sequences()`** — sequences of status events including illegal
  attempts (e.g. fire after `done`) to confirm they are rejected.
- **`deadline_sets()`** — sets of `Deadline`/`ScheduledDeadline` with controlled
  overlaps/divergences in membership, `due_at`, and `status` for reconciliation.
- **`rule_yaml()` / `idem_keys()`** — raw rule definitions and idempotency keys.
- **`canonical(...)`** — normaliser dropping `computed_at`/timing fields so
  determinism compares derivation, not wall-clock.

## Known edge inputs to seed

- Offset landing exactly on a federal holiday that abuts a weekend (3-day skip).
- `previous_business_day` adjustment crossing a month/year boundary.
- Reminder offset of `0` and offsets that all resolve after `due_at`
  (⇒ all `skipped_past`, none armed).
- `due_at == now` at schedule time (boundary: treated as **not** past-due).
- `due_at == now - 1s` (boundary: past-due → flagged, not armed).
- Trigger input missing the rule's `trigger` field (⇒ typed error, no date).
- Unpopulated calendar year (⇒ loud failure, never a guessed date).
- Identical rule content loaded twice (⇒ same version) vs whitespace-only change
  that is *semantically* identical (define + test the hashing normalisation).
- Reconciliation with the case connector raising `auth`/`fatal` (⇒ parked +
  alert, never empty drift list).
- Redundant double-arm firing simultaneously (⇒ exactly-once delivery).
- DST-transition day as `due_at` and as a `fire_at`.
