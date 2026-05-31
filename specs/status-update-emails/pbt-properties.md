# Property-Based Tests — Status Update Emails

> Feature slug: `status-update-emails` · Traces: FR-10, FR-6; NFR-2, NFR-3, NFR-4.
> PBT for invariants that must hold for **all** inputs (CONVENTIONS §3.7); focused
> tests for specific scenarios live in `tasks.md`. Baseline tool: **Hypothesis**
> (PTD §3 stack). Pseudocode is illustrative — it drives a fake/in-memory Email
> port, QC check, and orchestrator so properties exercise this feature's logic,
> not the engine's.

## Invariants under test (plain English)

- **P1 — One send per status change (idempotency).** No matter how many times the
  same status change is delivered (duplicate webhook, webhook+sweep overlap,
  retried run, crash-and-retry), at most **one** email is ever sent for a given
  `change_id`. (PBT FOCUS; NFR-3.)
- **P2 — Recipient ∈ matter participants (recipient integrity).** Every recipient
  of every sent email is a member of that matter's participant set; an address
  outside the participants can never be sent to. (PBT FOCUS; NFR-1.)
- **P3 — No send without recorded approval.** An `email.send` only ever occurs for
  a run that has a persisted approval record; rejected or un-approved runs never
  send. (PBT FOCUS; NFR-4.)
- **P4 — Draft always precedes send.** For any run that sends, a draft was created
  (and persisted) strictly before the send; there is no send without a prior
  draft. (PBT FOCUS.)
- **P5 — Skipped/blocked never send.** A run whose `to_status` is not allow-listed
  (`skipped`) or whose recipient-integrity QC result is `fail` (`blocked`) never
  produces a send.
- **P6 — Audit completeness & ordering.** Every send is preceded in the run's audit
  trail by `draft_created`, a `qc_result`, and an `approval_decision`, in that
  order, and `change_id` is only marked `delivered` after the `email_sent` audit
  record. (NFR-2.)

## Properties (Hypothesis-style pseudocode)

```python
from hypothesis import given, strategies as st, settings

# --- P1: one send per status change -------------------------------------------
@given(events=duplicate_event_streams())   # 1..N deliveries of the SAME change
@settings(max_examples=300)
def test_one_send_per_status_change(events):
    h = harness()                            # fresh fake email port + orchestrator
    for ev in events:                        # replay duplicates / interleave sweep
        h.deliver(ev)
    h.approve_all_pending()                  # approve every gate that opens
    change_ids = {e.change_id for e in events}
    for cid in change_ids:
        assert h.email_port.sends_for(cid) <= 1          # never a second send
        assert h.runs_for(cid) <= 1                       # one run per change

# --- P2: recipient in matter participants -------------------------------------
@given(matter=matters(), delta=deltas())
def test_recipient_in_participants(matter, delta):
    h = harness(matters=[matter])
    h.run_to_send(delta, approve=True)
    for sent in h.email_port.sent:
        participant_addrs = {c.email for c in matter.participants if c.email}
        assert set(sent.recipients) <= participant_addrs   # no outsider, ever

# --- P3: no send without recorded approval ------------------------------------
@given(delta=deltas(), decision=st.sampled_from(["approve", "reject", "none"]))
def test_no_send_without_approval(delta, decision):
    h = harness()
    h.run_until_gate(delta)
    if decision == "approve":
        h.approve()
    elif decision == "reject":
        h.reject()
    # "none": leave parked at the gate
    sent = h.email_port.sent
    if decision != "approve":
        assert sent == []                                  # nothing sent
    for s in sent:                                         # if sent, approval exists
        assert h.audit.has(s.run_id, "approval_decision", outcome="approved")

# --- P4: draft always precedes send -------------------------------------------
@given(delta=deltas())
def test_draft_precedes_send(delta):
    h = harness()
    h.run_to_send(delta, approve=True)
    for run in h.runs:
        if h.audit.has(run.id, "email_sent"):
            assert h.audit.index(run.id, "draft_created") is not None
            assert h.audit.index(run.id, "draft_created") \
                 < h.audit.index(run.id, "email_sent")     # strictly before
            assert run.draft_comm_id is not None

# --- P5: skipped / blocked never send -----------------------------------------
@given(delta=deltas(), qc=st.sampled_from(["pass", "warn", "fail"]),
       allow=st.booleans())
def test_skipped_or_blocked_never_send(delta, qc, allow):
    h = harness(allow_listed=allow, qc_result=qc)
    h.run(delta, approve=True)                  # try to push all the way through
    if not allow or qc == "fail":
        assert h.email_port.sent == []          # skipped or blocked => no send

# --- P6: audit completeness & ordering ----------------------------------------
@given(delta=deltas())
def test_audit_order_and_delivered_flag(delta):
    h = harness()
    h.run_to_send(delta, approve=True)
    for run in h.runs:
        if h.audit.has(run.id, "email_sent"):
            order = h.audit.sequence(run.id)
            assert index(order, "draft_created") \
                 < index(order, "qc_result") \
                 < index(order, "approval_decision") \
                 < index(order, "email_sent")
            # delivered flag set only AFTER the send audit record
            assert h.delivered_marked_at(run.delta.change_id) \
                 >= h.audit.timestamp(run.id, "email_sent")
```

## Generators / input domains

- `matters()` — `Matter` with 1..5 participants; client may have a present or
  missing email; participant emails unique; `status` drawn from a small status
  vocabulary; `practice_area` includes immigration values.
- `deltas()` — `MatterStatusDelta` with `from_status != to_status`; `source ∈
  {webhook, sweep, manual}`; `change_id` derived deterministically from the same
  inputs (so equal inputs ⇒ equal id); `to_status` may or may not be allow-listed.
- `duplicate_event_streams()` — lists of 1..N events that all encode the **same**
  status change (same `change_id`) but with varied `source`, arrival order, and
  injected retries; plus occasional unrelated changes to ensure isolation.
- `recipients` — drawn from inside **and** deliberately outside the matter's
  participant set, to attack P2.
- Decisions — `approve | edit-then-approve | reject | none` for the gate.
- QC results — `pass | warn | fail` injected via a fake `qc-verification` port.

## Known edge inputs to seed

- Same change delivered twice via webhook (identical provider event id).
- Webhook then sweep for the same change (overlap → must still be one send).
- Status flap `A→B→A`: revert produces a distinct `change_id` (decide suppression
  via `ASSUMPTION (confirm)`); property asserts no *duplicate* send for the same
  id, not cross-id behaviour.
- Crash injected **after** `email.send` but **before** `delivered` is marked →
  retry must not double-send (idem_key + delivered flag).
- Recipient address that is a near-duplicate of a participant (case/whitespace) —
  must still be judged outside the set unless normalised-equal.
- Matter with a missing client email → run blocks as a gap, never sends.
- `to_status` not in allow-list → run `skipped`, never sends.
- QC `fail` with an otherwise-valid approval attempt → must remain `blocked`.
- Two concurrent approvals on one gate → single resolution, single send.
