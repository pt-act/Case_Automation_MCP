# Property-Based Tests — QC Verification

> Slug: `qc-verification` · Baseline: **Hypothesis** (CONVENTIONS §4). These
> properties assert invariants that must hold for **all** inputs, complementing
> the focused tests in `tasks.md`. They cover the PBT FOCUS: any single `fail`
> blocks; registry totality; determinism; recipient-integrity correctness; and
> privilege never passing a privileged-doc-to-external packet. Pseudocode is
> illustrative, not final code.

## Invariants under test (plain English)

- **P1 — Any single fail blocks.** If *any* check in a report returns `fail`, the
  aggregate is `block`. No combination of `pass`/`warn`/`skipped` can let a
  `fail` through. *(traces QR-10, FR-13/14; spec §5)*
- **P2 — Aggregate monotonic & exhaustive.** With no `fail` and ≥1 `warn` ⇒
  `pass_with_warnings`; with only `pass`/`skipped` ⇒ `pass`. The aggregate is a
  total function of the multiset of verdicts and is order-independent. *(QR-10,
  QR-11)*
- **P3 — Registry totality.** For every check selected to run, the report
  contains exactly one entry that is either a `CheckResult` or an explicit
  `skipped` with a `SkipReason`. No selected check is silently absent or
  duplicated. *(QR-12, FR-16)*
- **P4 — Determinism.** Identical packet + identical selected checks + identical
  config ⇒ identical `QCReport` (per-check verdicts, reasons, and aggregate).
  Re-running and shuffling input collections changes nothing. *(QR-11, NFR-3)*
- **P5 — Recipient-integrity correctness.** `recipient_integrity` returns `pass`
  **iff** every external recipient is a participant of the matter; if any
  recipient is absent from participants, it returns `fail`. *(QR-5)*
- **P6 — Privilege never passes external.** If the packet is (or is ambiguously)
  external-bound **and** contains ≥1 document with `privileged == True`, the
  `privilege` check returns `fail` — never `pass`, never `warn`, never
  `skipped`. *(QR-7, NFR-1; spec §10)*
- **P7 — Fail-closed on error.** A check that raises is recorded as `fail`
  ("check errored"); the aggregate then blocks. Exceptions never produce `pass`.
  *(QN-7)*
- **P8 — Severity policy honoured.** No check emits a verdict outside its
  declared `severity_policy`; in particular `privilege` can only emit
  `pass`/`fail` (never `warn`). *(spec §7)*
- **P9 — Determinism w.r.t. time.** Two runs with the same `packet.now` agree on
  `deadline_sanity`; checks never read the wall clock. *(QN-5)*
- **P10 — Audit is total.** Every `qc.verify` call writes exactly one audit
  record before returning. *(QR-13, NFR-2)*

## Properties (Hypothesis-style pseudocode)

```python
from hypothesis import given, strategies as st, settings

# --- P1: any single fail blocks ------------------------------------------
@given(results=verdict_lists())          # lists of CheckResult, ≥1 element
def test_any_fail_blocks(results):
    agg = aggregate(results)
    if any(r.verdict == "fail" for r in results):
        assert agg == "block"
    # contrapositive: a non-block aggregate implies no fail
    if agg != "block":
        assert all(r.verdict != "fail" for r in results)

# --- P2: aggregate is a total, order-independent function -----------------
@given(results=verdict_lists())
def test_aggregate_total_and_order_independent(results):
    import random
    shuffled = results[:]; random.shuffle(shuffled)
    assert aggregate(results) == aggregate(shuffled)
    has_fail = any(r.verdict == "fail" for r in results)
    has_warn = any(r.verdict == "warn" for r in results)
    expected = "block" if has_fail else ("pass_with_warnings" if has_warn else "pass")
    assert aggregate(results) == expected

# --- P3: registry totality ------------------------------------------------
@given(packet=packets(), selected=check_id_subsets())
def test_registry_totality(packet, selected):
    report = registry.run(packet, selected, cfg=default_cfg)
    accounted = {r.check_id for r in report.results} | set(report.checks_skipped)
    applicable = {c.id for c in registry.applicable(packet.kind)}
    expected = (set(selected) & applicable) if selected else applicable
    assert accounted == expected                  # nothing missing
    ids = [r.check_id for r in report.results] + list(report.checks_skipped)
    assert len(ids) == len(set(ids))              # nothing duplicated

# --- P4: determinism ------------------------------------------------------
@given(packet=packets(), selected=check_id_subsets())
@settings(max_examples=200)
def test_determinism(packet, selected):
    r1 = registry.run(packet, selected, cfg=default_cfg)
    r2 = registry.run(deepcopy_shuffled(packet), selected, cfg=default_cfg)
    assert canonical(r1) == canonical(r2)         # verdicts + reasons + aggregate

# --- P5: recipient-integrity correctness ----------------------------------
@given(packet=packets_with_recipients())
def test_recipient_integrity_iff(packet):
    res = run_single("recipient_integrity", packet)
    participants = {c.id for c in packet.matter.client_and_participants()}
    externals = [r for r in packet.intended_recipients if is_external(r)]
    all_in = all(r.id in participants for r in externals)
    assert (res.verdict == "pass") == (all_in and len(externals) > 0 or not externals)
    if any(r.id not in participants for r in externals):
        assert res.verdict == "fail"

# --- P6: privilege never passes a privileged-doc-to-external packet -------
@given(packet=packets_with_privilege())
def test_privilege_never_passes_external(packet):
    res = run_single("privilege", packet)
    external = packet.external_bound or any_recipient_external(packet)
    has_privileged = any(d.privileged for d in packet.documents)
    if external and has_privileged:
        assert res.verdict == "fail"              # never pass/warn/skipped
    assert res.verdict in {"pass", "fail"}        # privilege never warns/skips

# --- P7: fail-closed on error ---------------------------------------------
@given(packet=packets())
def test_errored_check_fails_closed(packet):
    report = registry.run(packet, ["always_raises"], cfg=default_cfg)
    errored = [r for r in report.results if r.check_id == "always_raises"]
    assert errored and errored[0].verdict == "fail"
    assert report.aggregate == "block"

# --- P8: severity policy honoured -----------------------------------------
@given(packet=packets(), selected=check_id_subsets())
def test_severity_policy(packet, selected):
    report = registry.run(packet, selected, cfg=default_cfg)
    for r in report.results:
        policy = registry.get(r.check_id).severity_policy
        assert r.verdict in policy.allowed_verdicts | {"skipped"}
    priv = [r for r in report.results if r.check_id == "privilege"]
    assert all(r.verdict in {"pass", "fail"} for r in priv)

# --- P9: time determinism -------------------------------------------------
@given(packet=packets_with_deadlines())
def test_deadline_uses_injected_now(packet):
    r1 = run_single("deadline_sanity", packet)
    r2 = run_single("deadline_sanity", packet)        # same packet.now
    assert r1.verdict == r2.verdict
    # mutating wall clock must not change the verdict (checks read packet.now)
    with frozen_wall_clock_shift(days=10000):
        assert run_single("deadline_sanity", packet).verdict == r1.verdict

# --- P10: audit totality --------------------------------------------------
@given(packet=packets())
def test_audit_written_once(packet):
    with capture_audit() as audit:
        qc_verify(packet)
    records = [a for a in audit if a.action == "qc.verify" and a.run_id == packet.run_id]
    assert len(records) == 1
    assert no_raw_pii(records[0])                     # redaction holds
```

## Generators / input domains

- **`verdict_lists()`** — non-empty lists of `CheckResult` with `verdict` drawn
  from {`pass`,`warn`,`fail`,`skipped`} (biased to include `fail`/`warn`/all-
  skipped edge mixes).
- **`packets()`** — `VerificationPacket` with: matters carrying 0–N participants;
  0–25 documents (mix of `privileged` True/False); 0–N communications; 0–N
  intended recipients (internal + external domains); 0–N deadlines around
  `packet.now`; 0–N extracted fields with `confidence ∈ [0,1]`; template bindings
  with a mix of resolved/unresolved required + optional vars; attachment refs
  present/absent/wrong-checksum; `external_bound` True/False/None; a fixed
  injected `now`.
- **`packets_with_recipients()`** — focuses recipient sets: some ⊆ participants,
  some with an outsider; includes empty recipient set on a send-kind packet.
- **`packets_with_privilege()`** — guarantees coverage of {external × privileged},
  {internal × privileged}, {ambiguous external_bound × privileged}, and the
  no-privileged baseline.
- **`packets_with_deadlines()`** — deadlines at/just-before/just-after
  `packet.now`, within tight window, beyond horizon.
- **`check_id_subsets()`** — subsets of registered check ids, including empty
  (⇒ all-applicable), the full set, and ids not applicable to the kind.
- **`canonical(report)`** — normaliser dropping timing/duration fields so
  determinism compares verdicts/reasons/aggregate only.

## Known edge inputs to seed

- Empty packet (no docs, no comms, no recipients) → expect all-skipped, flagged.
- Single `fail` among 20 `pass` → aggregate `block` (P1).
- All `skipped` → aggregate `pass` but report marks all-skipped (caller policy).
- Privileged doc + `external_bound = None` + one external-domain recipient →
  privilege `fail` (ambiguity resolves strict).
- Privileged doc + provably internal recipients only → privilege `pass`.
- Recipient set identical to participants but with different `role` → recipient
  integrity `warn`, not `fail`.
- Consistency: same name differing only by trailing whitespace/case → `warn`
  after normalisation, not `fail`.
- Extraction field exactly at `fail_floor` and exactly at `warn_threshold`
  (boundary inclusivity).
- Deadline `due_at == packet.now` (boundary: not past-due).
- A check whose `run` raises `TimeoutError` / arbitrary exception → `fail`.
- Duplicate check-id registration → startup error (separate non-PBT assertion).
