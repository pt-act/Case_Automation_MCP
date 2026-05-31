# Property-Based Tests — Platform Foundation

> Feature slug: `platform-foundation`. Hypothesis is the baseline PBT tool
> (CONVENTIONS.md §4, §11). These properties assert invariants that must hold
> for *all* generated inputs; focused/example tests (see `tasks.md`) cover
> specific scenarios. Pseudocode is illustrative, not final code.

## Invariants under test (plain English)

1. **Audit hash-chain integrity.** Appending records preserves a verifiable
   chain: for any sequence of appends, `verify()` passes. (PBT FOCUS;
   FR-15 / NFR-2.)
2. **Audit tamper-detection.** Any mutation, deletion, re-ordering, or
   insertion in the stored chain makes `verify()` fail at the first affected
   link. (NFR-2.)
3. **Encryption round-trip.** For every plaintext `x` and key `k`,
   `decrypt(encrypt(x, k), k) == x`; a wrong key never yields valid plaintext;
   non-empty plaintext never equals its ciphertext. (PBT FOCUS; NFR-1.)
4. **RBAC soundness (default-deny).** `authorize` grants a (principal,
   permission) pair **iff** the matrix explicitly grants it; no role — and in
   particular the agent service identity — is ever granted a permission it must
   not have. (PBT FOCUS; NFR-1.)
5. **Config-validation totality.** Every generated environment either validates
   into a `Settings` object or raises a precise validation error; it never
   loads into a partially-valid/half-initialised state, and error text never
   contains a secret value. (PBT FOCUS; NFR-8.)
6. **Domain serialise/deserialise round-trip.** For every valid instance `m` of
   each domain model, `Model.model_validate(m.model_dump(mode="python")) == m`
   (and likewise for `mode="json"` via the matching loader). (PBT FOCUS; FR-2.)
7. **PII/secret redaction (supporting).** For any record containing a value from
   the PII/secret pattern set, the rendered log/trace/metric output contains no
   such value. (NFR-1 / NFR-8.)

## Properties (Hypothesis-style pseudocode)

```python
from hypothesis import given, strategies as st, assume

# 1 — audit chain integrity
@given(records=st.lists(audit_payloads(), min_size=0, max_size=50))
def test_audit_chain_verifies(records):
    svc = fresh_audit_service()
    for r in records:
        svc.record(**r)
    assert svc.verify().ok

# 2 — tamper detection (mutate / delete / reorder)
@given(records=st.lists(audit_payloads(), min_size=1, max_size=30),
       op=st.sampled_from(["mutate", "delete", "reorder", "insert"]))
def test_audit_tamper_detected(records, op):
    svc = fresh_audit_service()
    for r in records:
        svc.record(**r)
    i = corrupt_store(svc.store, op)          # returns index of first change
    result = svc.verify()
    assert not result.ok and result.first_bad_index == i

# 3 — encryption round-trip + wrong-key + non-identity
@given(x=st.binary(max_size=4096), k=keys(), k2=keys())
def test_encrypt_roundtrip(x, k, k2):
    assume(k != k2)
    ct = encrypt(x, k)
    assert decrypt(ct, k) == x
    if x:
        assert ct != x
    with pytest.raises(DecryptionError):
        decrypt(ct, k2)

# 4 — RBAC soundness (matrix is ground truth; default deny)
@given(principal=principals(), perm=permissions(), resource=resources_or_none())
def test_rbac_matches_matrix(principal, perm, resource):
    allowed = authorize(principal, perm, resource)
    assert allowed == matrix_grants(principal, perm, resource)

@given(perm=permissions())
def test_agent_identity_constrained(perm):
    allowed = authorize(AGENT_SERVICE_PRINCIPAL, perm)
    assert allowed == (perm in AGENT_MINIMAL_GRANTS)   # never more

# 5 — config validation totality (no half-loaded state, no secret leak)
@given(env=env_dicts())
def test_config_total(env):
    try:
        s = Settings(_env=env)
    except ConfigValidationError as e:
        assert no_secret_values_in(str(e), env)        # scrubbed message
    else:
        assert s.is_fully_initialised()                # all required present

# 6 — domain round-trip (python + json modes)
@given(m=domain_instances())            # one strategy per model, unioned
def test_domain_roundtrip(m):
    assert type(m).model_validate(m.model_dump(mode="python")) == m
    assert type(m).model_validate_json(m.model_dump_json()) == m

# 7 — redaction (supporting)
@given(payload=payloads_with_pii_and_secrets())
def test_redaction(payload):
    out = render_signals(payload)        # log line + span attrs + metric labels
    assert contains_no_sensitive(out, payload.sensitive_values)
```

## Generators / input domains

- **`audit_payloads()`** — dicts of `actor` (role/principal strings),
  `action` (verb-noun), `inputs`/`outputs` (nested JSON-able dicts incl.
  unicode, empty, deeply-nested), `approval` (None | approved | rejected),
  `run_id` (uuid-like). Timestamps assigned by the service, not generated.
- **`keys()`** — 32-byte keys (AES-256); include distinct DEK/KEK pairs.
- **`principals()`** — composed of role subsets drawn from
  `{paralegal, attorney, intake_coordinator, ops_admin, agent_service}`
  (ASSUMPTION (confirm)); include empty-role and multi-role principals.
- **`permissions()`** — the enumerated permission set (read/write/export/etc.),
  including permissions intentionally absent from every role.
- **`resources_or_none()`** — matter-scoped resource ids and `None` (global).
- **`env_dicts()`** — environment maps: complete-valid, missing-required,
  wrong-type, extra-unknown, and ones whose values originate from secret keys
  (to test scrubbing in error messages).
- **`domain_instances()`** — a strategy per model (`Contact`, `Matter`,
  `Document`, `Deadline`, `Communication`, `Task`) honouring PTD §4 types:
  `Literal` enums sampled from their members, `EmailStr`/optionals sometimes
  `None`, `external_ids` arbitrary `dict[str,str]`, `Matter.key_dates` a list of
  generated `Deadline`s, UTC datetimes.
- **`payloads_with_pii_and_secrets()`** — records embedding A-numbers,
  passport/visa numbers, SSN/ITIN, DOB, biometric references, emails, phones,
  and fake secret tokens, in keys, values, and nested positions.

## Known edge inputs to seed

- **Audit:** empty chain (0 records); single genesis record; identical
  consecutive payloads (hash must still differ via prev-hash linkage);
  very large `inputs`/`outputs`; unicode and `None` fields; reorder of two
  adjacent records; deletion of the genesis record.
- **Encryption:** empty bytes; 1 byte; exactly block-sized and block-1 sizes;
  max-size blob; identical plaintext under two keys (ciphertexts must differ
  given distinct nonces); rotated key (old DEK still decrypts old ciphertext).
- **RBAC:** principal with no roles; principal with every role; a permission
  granted to no role; `agent_service` attempting an `ops_admin`-only export;
  resource-scoped grant vs global request.
- **Config:** all-required-present; each single required key missing in turn;
  a required int given as non-numeric; a secret-sourced value present in an
  error path (assert scrubbed); unknown extra keys ignored or rejected per
  policy.
- **Domain:** `Matter` with empty `key_dates` and with many; `Document` with
  `privileged=True`; `Communication` in each `direction` and each `status`;
  `Deadline` in each `Literal` status incl. `missed`; `Contact` with `email=None`.
- **Redaction:** PII appearing as a dict key (not just value); PII inside a
  deeply nested structure; a secret embedded mid-string; a value that is *only*
  partially sensitive (assert minimal-but-sufficient redaction).
