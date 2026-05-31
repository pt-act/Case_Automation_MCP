# Property-Based Tests — Client Intake

> Slug: `client-intake` · Baseline PBT tool: **Hypothesis** (CONVENTIONS §4).
> These properties assert invariants that must hold for *all* inputs; focused
> example tests live in `tasks.md`. Pseudocode is illustrative (real tests use
> in-memory fake CRM/Case/Email ports and a fake extraction/deadline engine, so
> properties are deterministic and side-effect-free).

## Invariants under test (plain English)

1. **Dedupe idempotency.** Processing the *same* lead twice (same idempotency
   key) yields **exactly one** `Contact` and **exactly one** `Matter`, and at
   most one welcome send — regardless of interleaving or retries. (PBT focus;
   NFR-3.)
2. **No welcome send before gate approval.** For any run, `email.send` is never
   invoked while the gate state is not `approved`. A rejected run never sends.
   (PBT focus; NFR-4.)
3. **Opening-task-set completeness.** For any resolved `case_type`, the set of
   created `Task`s equals exactly the configured checklist for that case type —
   no missing, no extra, no duplicates on retry. (PBT focus; FR-8.)
4. **Mapping completeness.** For any lead + case type, every **required** matter
   field is either present in `IntakeFields` **or** surfaced as a blocking
   `IntakeGap`; never silently absent. (PBT focus; FR-8/FR-7.)
5. **Single-contact under ambiguity.** An `ambiguous` dedupe never creates a
   contact and never auto-merges; it always produces a blocking gap. (Supports #1.)
6. **Gate-blocking on blocking gaps.** While any `severity="block"` gap exists,
   the gate cannot transition to `approved`. (Supports #2, #4.)

## Properties (Hypothesis-style pseudocode)

```python
from hypothesis import given, strategies as st, settings

# ---- Property 1: dedupe idempotency -------------------------------------
@given(lead=leads(), n=st.integers(min_value=2, max_value=5))
def test_double_process_yields_single_contact_and_matter(lead, n):
    env = IntakeTestEnv()            # fresh fake CRM/Case/Email + engines
    key = derive_idempotency_key(lead)
    runs = [env.intake_run(lead, idempotency_key=key) for _ in range(n)]
    assert len({r.run_id for r in runs}) == 1            # one run
    assert env.crm.count_contacts_for(lead) == 1         # one contact
    assert env.case.count_matters_for(lead) == 1         # one matter
    assert env.email.send_count(runs[0].run_id) <= 1     # at most one send

# ---- Property 2: no send before gate approval ---------------------------
@given(lead=leads(), decision=st.sampled_from(["approve", "reject", "none"]))
def test_no_send_before_gate(lead, decision):
    env = IntakeTestEnv()
    run = env.intake_run(lead)
    env.advance_until_gate(run)                          # through draft_welcome
    assert env.email.send_count(run.run_id) == 0         # nothing sent at gate
    if decision == "approve" and not run.has_blocking_gaps():
        env.approve(run); assert env.email.send_count(run.run_id) == 1
    else:
        env.apply(run, decision)
        assert env.email.send_count(run.run_id) == 0     # reject/none => never

# ---- Property 3: opening-task-set completeness --------------------------
@given(lead=leads(), case_type=case_types(), retries=st.integers(0, 3))
def test_opening_tasks_match_config_exactly(lead, case_type, retries):
    env = IntakeTestEnv()
    run = env.intake_run(with_case_type(lead, case_type))
    env.advance_through(run, "open_tasks")
    for _ in range(retries):                             # idempotent retries
        env.retry_step(run, "open_tasks")
    expected = set(env.config.opening_tasks(case_type))  # configured checklist
    created = env.case.tasks_for(run.matter_id)
    assert multiset(t.title for t in created) == multiset(expected)  # no extra/missing/dupes

# ---- Property 4: mapping completeness -----------------------------------
@given(lead=leads(), case_type=case_types())
def test_every_required_field_present_or_gap(lead, case_type):
    env = IntakeTestEnv()
    run = env.intake_run(with_case_type(lead, case_type))
    env.advance_through(run, "parse_lead")
    fields = env.fields(run); gaps = {g.field for g in env.gaps(run)}
    for req in env.config.required_fields(case_type):
        assert fields.has(req) or req in gaps            # never silently missing

# ---- Property 5: ambiguous dedupe never creates/merges ------------------
@given(lead=leads())
def test_ambiguous_dedupe_blocks(lead):
    env = IntakeTestEnv(force_dedupe="ambiguous")
    run = env.intake_run(lead)
    env.advance_through(run, "dedupe_contact")
    assert env.crm.count_contacts_for(lead) == 0         # no create
    assert any(g.severity == "block" for g in env.gaps(run))

# ---- Property 6: blocking gaps prevent approval -------------------------
@given(lead=leads_with_blocking_gap())
def test_gate_cannot_approve_with_blocking_gap(lead):
    env = IntakeTestEnv()
    run = env.intake_run(lead)
    env.advance_until_gate(run)
    result = env.try_approve(run)
    assert result.rejected_reason == "blocking_gaps_present"
    assert env.email.send_count(run.run_id) == 0
```

## Generators / input domains

- `leads()` — `LeadPayload`s across `source_channel ∈ {email, form, manual,
  api}`; bodies/fields with varied presence of bio, `country_of_origin`,
  `a_number` (valid + absent), `case_type` (present/absent/unknown), and
  `prior_filings`. Include leads that map to the same identity (to exercise
  dedupe) and to distinct identities.
- `case_types()` — the configured enumeration (family-based, employment-based,
  asylum, naturalization, removal-defense, other/uncategorised) —
  **ASSUMPTION (confirm)** per `requirements.md §8`.
- `leads_with_blocking_gap()` — leads guaranteed to miss ≥1 required field for
  their case type.
- `a_numbers()` — valid 9-digit A-numbers + malformed values (to test format
  validation), plus `None`.
- Fake ports return deterministic ids; `IntakeTestEnv` exposes counters
  (`count_contacts_for`, `count_matters_for`, `send_count`, `tasks_for`) and step
  controls (`advance_through`, `retry_step`, `approve`, `reject`).

## Known edge inputs to seed

- Same lead submitted twice with identical idempotency key (Property 1 core).
- Same identity arriving via two channels (email + form) with different
  `provider_event_id` (webhook-dedupe + run-key interplay).
- Returning client: existing contact, brand-new matter (must reuse contact, create
  matter — independent dedupe).
- Lead with no resolvable `case_type` → "other/uncategorised" + default checklist.
- Case type whose configured checklist is **empty** (created task set must be
  empty, not error).
- Case type with **no** deadline rules (empty `key_dates`, no error).
- Retry storms on `open_tasks` / `create_contact` / `create_matter` /
  `send_welcome` (idempotency under repetition).
- Gate approved-with-edits (re-draft then exactly-one send).
- All-required-fields-missing lead (max gaps; gate must stay un-approvable).
- Malformed A-number alongside otherwise valid bio (format validation isolates).
