# Property-Based Tests — Workflow Orchestration

> Phase 4 of the process in `specs/_shared/CONVENTIONS.md`. Properties assert
> invariants that must hold for **all** generated inputs (Hypothesis is the
> baseline, CONVENTIONS §4). Focused/example tests live in `tasks.md`. Each
> property below covers a PBT-FOCUS item from the feature brief.

## Invariants under test (plain English)

1. **Step idempotency.** Re-running any single step (forced retry, replay, or
   post-crash re-entry) never applies its external effect more than once; the
   second execution returns the recorded output. *(NFR-3; FR-4.)*
2. **Resume invariant.** A run resumed from persisted state produces the same
   final state, the same per-step outputs, and the same set of external effects
   as the same run executed without any crash. *(NFR-3, NFR-9.)*
3. **Gate-before-action.** A gated step's post-gate action never executes unless
   a valid, recorded approval exists for that gate. *(FR-14; NFR-4.)*
4. **No duplicate side effects under retry.** For any sequence of transient
   failures and retries, each external effect occurs exactly once. *(NFR-3.)*
5. **Only valid state transitions occur.** Every persisted run/step transition is
   an edge in the state machine (`spec.md` §5.2); no other transition is ever
   stored. *(NFR-3.)*
6. **Single-use token.** An approval token resolves at most one gate decision;
   any subsequent use is rejected. *(NFR-2, NFR-4 — supports security spec.)*
7. **Idempotent start.** Starting a run twice with the same `dedupe_key`/
   `idem_key` yields exactly one run and one set of effects. *(FR-4.)*

## Properties (Hypothesis-style pseudocode)

```python
from hypothesis import given, strategies as st, settings
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant

# Shared fixtures: a FakeConnector counting external effects per idem_key,
# an in-memory IdempotencyStore + run repository, and a deterministic clock.

# --- P1: step idempotency -------------------------------------------------
@given(reruns=st.integers(min_value=1, max_value=10))
def test_step_effect_applied_at_most_once(reruns, engine, fake_conn):
    run = engine.start("wf_one_effect_step", context={})
    engine.run_to_completion(run.id)
    first_output = engine.step_output(run.id, "do_effect")
    for _ in range(reruns):
        engine.force_rerun_step(run.id, "do_effect")   # replay / retry
    assert fake_conn.effect_count("do_effect", run.id) == 1
    assert engine.step_output(run.id, "do_effect") == first_output

# --- P2: resume invariant -------------------------------------------------
@given(crash_after=st.integers(min_value=0, max_value=8),
       seed=st.integers())
def test_resume_equals_uninterrupted(crash_after, seed, make_engine):
    baseline = make_engine(seed)
    r1 = baseline.start("wf_multi", context={"seed": seed})
    baseline.run_to_completion(r1.id)

    crashy = make_engine(seed)
    r2 = crashy.start("wf_multi", context={"seed": seed})
    crashy.run_until_step_index(r2.id, crash_after)
    crashy.simulate_crash()
    crashy.recover_and_resume()                          # startup sweep
    crashy.run_to_completion(r2.id)

    assert crashy.final_status(r2.id) == baseline.final_status(r1.id)
    assert crashy.all_step_outputs(r2.id) == baseline.all_step_outputs(r1.id)
    assert crashy.effect_multiset(r2.id) == baseline.effect_multiset(r1.id)

# --- P3: a gated step never acts without a recorded approval --------------
@given(approve=st.booleans())
def test_post_gate_action_requires_approval(approve, engine, fake_conn):
    run = engine.start("wf_with_gate", context={})
    engine.run_until_gate(run.id)
    assert engine.status(run.id) == "awaiting_approval"
    assert fake_conn.effect_count("post_gate_send", run.id) == 0   # not yet
    if approve:
        token = engine.issue_token(run.id, "GATE:review")
        engine.resolve_gate(token, "approve", actor="attorney")
        engine.run_to_completion(run.id)
        assert fake_conn.effect_count("post_gate_send", run.id) == 1
    else:
        # no approval ever recorded → post-gate action must never run
        engine.try_advance(run.id)
        assert fake_conn.effect_count("post_gate_send", run.id) == 0

@given(bad=st.sampled_from(["expired", "tampered", "reused", "wrong_role"]))
def test_invalid_approval_never_triggers_action(bad, engine, fake_conn):
    run = engine.start("wf_with_gate", context={})
    engine.run_until_gate(run.id)
    token = engine.issue_token(run.id, "GATE:review")
    result = engine.resolve_gate(*engine.corrupt(token, bad))
    assert result.rejected
    assert fake_conn.effect_count("post_gate_send", run.id) == 0
    assert engine.status(run.id) == "awaiting_approval"   # still parked

# --- P4: no duplicate side effects under arbitrary transient failures -----
@given(fail_pattern=st.lists(st.booleans(), min_size=0, max_size=12))
def test_no_duplicate_effects_under_retry(fail_pattern, engine, fake_conn):
    # fake_conn raises ConnectorError(transient) on the True positions,
    # succeeds afterwards (retries bounded by the cap → may park).
    fake_conn.program_transients("do_effect", fail_pattern)
    run = engine.start("wf_one_effect_step", context={})
    engine.run_to_terminal(run.id)                       # succeeded or parked
    assert fake_conn.effect_count("do_effect", run.id) <= 1
    if engine.final_status(run.id) == "succeeded":
        assert fake_conn.effect_count("do_effect", run.id) == 1

# --- P5: only legal transitions are ever persisted (stateful) -------------
class RunLifecycle(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.engine = new_engine()
        self.run = self.engine.start("wf_multi_with_gate", context={})

    @rule()
    def advance(self): self.engine.try_advance(self.run.id)
    @rule(transient=st.booleans())
    def fail(self, transient): self.engine.inject_failure(self.run.id, transient)
    @rule(decision=st.sampled_from(["approve", "reject"]))
    def decide(self, decision): self.engine.try_resolve_current_gate(self.run.id, decision)
    @rule()
    def crash(self): self.engine.simulate_crash(); self.engine.recover_and_resume()

    @invariant()
    def every_transition_is_legal(self):
        for (frm, to) in self.engine.transition_log(self.run.id):
            assert is_legal_transition(frm, to)          # edge in spec.md §5.2
    @invariant()
    def terminal_is_sticky(self):
        if self.engine.is_terminal(self.run.id):
            assert not self.engine.has_later_transition(self.run.id)

TestRunLifecycle = RunLifecycle.TestCase

# --- P6: approval token is single-use -------------------------------------
@given(uses=st.integers(min_value=2, max_value=6))
def test_token_single_use(uses, engine):
    run = engine.start("wf_with_gate", context={})
    engine.run_until_gate(run.id)
    token = engine.issue_token(run.id, "GATE:review")
    results = [engine.resolve_gate(token, "approve", actor="attorney")
               for _ in range(uses)]
    assert sum(1 for r in results if r.accepted) == 1
    assert all(r.reason == "used" for r in results[1:])
    assert engine.gate_decisions(run.id, "GATE:review") == 1

# --- P7: idempotent start -------------------------------------------------
@given(n=st.integers(min_value=2, max_value=8))
def test_idempotent_start(n, engine, fake_conn):
    runs = [engine.start("wf_one_effect_step", context={}, dedupe_key="evt-42")
            for _ in range(n)]
    assert len({r.id for r in runs}) == 1
    engine.run_to_completion(runs[0].id)
    assert fake_conn.effect_count("do_effect", runs[0].id) == 1
```

## Generators / input domains

- **Workflow shapes:** small synthetic workflows — `wf_one_effect_step` (single
  external effect), `wf_multi` (N read/write steps), `wf_with_gate` (one gate +
  post-gate effect), `wf_multi_with_gate` (gates interleaved with effects). Step
  count `st.integers(1..12)`.
- **Failure patterns:** `st.lists(st.booleans())` mapping to transient-fail vs
  succeed per attempt; a separate strategy for fatal errors.
- **Crash points:** `st.integers(0..step_count)` — index after which the process
  is killed.
- **Token corruptions:** `st.sampled_from(["expired","tampered","reused",
  "wrong_role"])`.
- **Approver roles:** `st.sampled_from([authorised_role, unauthorised_role])`.
- **Dedupe keys:** fixed-vs-fresh to exercise idempotent start.
- **Clock:** deterministic, injectable, to drive backoff and TTL expiry without
  real waits.

## Known edge inputs to seed

- Zero-step workflow (degenerate) → must reach `succeeded` immediately, no effect.
- Gate as the **first** step and gate as the **last** step.
- Two consecutive gates (approval of one must not satisfy the next).
- Crash **exactly between** a step's external effect and recording its output
  (the connector idem-key must make re-call a no-op).
- Crash while `awaiting_approval` → run must remain parked, no work redone.
- Transient failures right up to the retry cap, then one more → must `park`,
  never `succeed`, effect count ≤ 1.
- Token presented after TTL boundary by one second → rejected as `expired`.
- Two approvals via different channels arriving concurrently → exactly one wins.
- Duplicate trigger event id replayed many times → one run, one effect.
- Illegal transition attempts (resume a `succeeded`/`rejected`/`cancelled` run).
