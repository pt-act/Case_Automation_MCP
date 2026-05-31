# Property-Based Tests — Connector Framework

> Inherits `specs/_shared/CONVENTIONS.md §3.7` (PBT for invariants). Baseline
> tool: **Hypothesis** (PTD §3). Each invariant is stated in plain English and
> as Hypothesis-style pseudocode. These layer on top of the focused tests in
> `tasks.md`. Properties run against the **in-memory reference adapters** (real
> vendors unknown) and the middleware/webhook pipeline.

## Invariants under test (plain English)

1. **Write idempotency — single effect.** Repeating a write with the *same*
   idempotency key produces exactly one effect and returns the same result,
   regardless of how many times it is replayed or interleaved. (CR-3, NFR-3;
   PBT FOCUS #1)
2. **Distinct keys — distinct effects.** Two writes with *different* idempotency
   keys produce two effects (no accidental collapsing). (CR-3)
3. **Bounded retry eventually surfaces a fatal.** For any sequence of failures,
   the retry middleware makes a bounded number of attempts within the total
   deadline and, if never successful, raises exactly one `FatalError` (never
   loops forever, never silent-fails). (CR-4, NFR-3, NFR-9; PBT FOCUS #2)
4. **Retry only retries retryable classes.** Non-retryable classes
   (`auth`/`not_found`/`fatal`) are raised on the first occurrence with no retry.
   (CR-5)
5. **Webhook dedup — exactly once.** For any multiset of inbound webhooks, each
   distinct `provider_event_id` is enqueued exactly once; duplicates are ignored.
   (CR-7, NFR-3; PBT FOCUS #3)
6. **Signature gate.** An event is enqueued only if its HMAC signature verifies;
   no invalid-signature event is ever enqueued. (Security; CR-6)
7. **Error-taxonomy mapping totality.** Every possible adapter failure (HTTP
   response or raised exception) maps to exactly one of the five taxonomy classes
   — the mapping is total and disjoint. (CR-5; PBT FOCUS #4)
8. **Payload ↔ domain round-trip (where reversible).** For reference-adapter
   fields that are reversible, `to_domain(to_payload(x)) == x` (and the reverse
   where the vendor projection is lossless). (CR-1; PBT FOCUS #5)
9. **Redaction totality.** For any input payload, no log/trace/metric/audit field
   emitted by a connector call or webhook contains a raw PII value or token.
   (NFR-1, NFR-8)
10. **Connector isolation.** Tripping one connector's circuit `open` never causes
    a call on a *different* healthy connector to fail. (NFR-9, CR-12)

## Properties (Hypothesis-style pseudocode)

```python
from hypothesis import given, strategies as st, settings, assume

# --- 1. Write idempotency: single effect ---------------------------------
@given(draft=communications(), n=st.integers(min_value=1, max_value=20))
def test_idempotent_send_single_effect(draft, n):
    email = ReferenceEmailConnector(); idem = "k-fixed"
    draft_id = run(email.create_draft(draft))
    results = [run(email.send(draft_id, idem)) for _ in range(n)]
    assert len(set(results)) == 1          # same provider id every time
    assert email.sent_count(draft_id) == 1 # exactly one effect

# --- 2. Distinct keys -> distinct effects --------------------------------
@given(draft=communications(), keys=st.lists(st.text(min_size=1), min_size=2,
                                             max_size=8, unique=True))
def test_distinct_keys_distinct_effects(draft, keys):
    email = ReferenceEmailConnector()
    did = run(email.create_draft(draft))
    for k in keys: run(email.send(did, k))
    assert email.sent_count(did) == len(keys)

# --- 3. Bounded retry eventually fatal -----------------------------------
@given(failures=st.lists(st.sampled_from(["transient", "ratelimit"]),
                         min_size=1, max_size=50))
@settings(deadline=None)
def test_retry_bounded_then_fatal(failures):
    client = OutboundClient(policy=RetryPolicy(max_attempts=5, total_deadline=2.0))
    adapter = always_fails(failures)       # never succeeds
    attempts, err = run_counting(client, adapter)
    assert attempts <= 5                    # bounded
    assert isinstance(err, FatalError)      # surfaces exactly one fatal
    assert client.elapsed() <= 2.0 + EPS    # within total deadline

# --- 4. Retry only retryable classes -------------------------------------
@given(cls=st.sampled_from(["auth", "not_found", "fatal"]))
def test_non_retryable_not_retried(cls):
    client = OutboundClient(policy=RetryPolicy(max_attempts=5))
    adapter = fails_with(cls)
    attempts, err = run_counting(client, adapter)
    assert attempts == 1
    assert classify_name(err) == cls

# --- 5. Webhook dedup: exactly once --------------------------------------
@given(events=st.lists(webhook_events(), min_size=1, max_size=40))
def test_webhook_dedup_exactly_once(events):
    sink = FakeTriggerSink(); pipe = WebhookPipeline(sink, redis=FakeRedis())
    for e in events: run(pipe.handle(sign(e)))   # includes duplicates by id
    distinct = {e.provider_event_id for e in events}
    enqueued_ids = [t.event.provider_event_id for t in sink.triggers]
    assert sorted(enqueued_ids) == sorted(distinct)   # one per distinct id
    assert len(enqueued_ids) == len(set(enqueued_ids))

# --- 6. Signature gate ----------------------------------------------------
@given(e=webhook_events(), tamper=st.booleans())
def test_only_verified_events_enqueue(e, tamper):
    sink = FakeTriggerSink(); pipe = WebhookPipeline(sink, redis=FakeRedis())
    body = sign(e) if not tamper else corrupt_signature(sign(e))
    status = run(pipe.handle(body))
    if tamper:
        assert status in (401, 403) and sink.triggers == []
    else:
        assert status == 202 and len(sink.triggers) == 1

# --- 7. Taxonomy mapping totality ----------------------------------------
@given(failure=adapter_failures())   # any HTTP status 100..599 OR any exception type
def test_mapping_total_and_disjoint(failure):
    err = classify(failure)
    classes = {AuthError, RateLimitError, NotFoundError, TransientError, FatalError}
    assert type(err) in classes                 # total: always maps
    assert sum(isinstance(err, c) for c in classes) == 1   # disjoint: exactly one

# --- 8. Payload <-> domain round-trip (reversible fields) ----------------
@given(m=matters())
def test_matter_roundtrip(m):
    a = ReferenceCaseConnector()
    payload = a._to_payload(m)
    back = a._to_domain(payload)
    assert reversible_fields(back) == reversible_fields(m)

# --- 9. Redaction totality -----------------------------------------------
@given(payload=payloads_with_pii(), token=tokens())
def test_no_pii_or_token_in_telemetry(payload, token):
    sink = CapturingTelemetry()
    run(OutboundClient(telemetry=sink).request("POST", URL,
                                                json=payload, secret=token))
    blob = sink.all_emitted_text()
    for secret_value in pii_values(payload) + [token]:
        assert secret_value not in blob

# --- 10. Connector isolation ---------------------------------------------
@given(n=st.integers(min_value=5, max_value=20))
def test_circuit_isolation(n):
    case = ReferenceCaseConnector(force="transient")   # will trip open
    crm  = ReferenceCRMConnector()                     # healthy
    for _ in range(n):
        with suppress(ConnectorError): run(case.get_matter("x"))
    assert health("case").state == "open"
    # healthy connector unaffected
    assert run(crm.find_contact("a")) is not None
```

## Generators / input domains

- `communications()`, `matters()`, `contacts()`, `documents()` — Hypothesis
  builders over the `platform-foundation` domain models (valid + boundary values:
  empty strings where allowed, unicode names, max-length fields, `None` optionals).
- `webhook_events()` — `Event`-shaped payloads with controllable
  `provider_event_id` (draw from a small pool so duplicates occur naturally),
  `type` (mix of known + unknown), and timestamps (fresh + stale).
- `adapter_failures()` — union of: HTTP status codes `100..599`, common
  `httpx`/network exceptions (`TimeoutException`, `ConnectError`, `ReadError`),
  and arbitrary unexpected exceptions (to prove the unmapped→`fatal` rule).
- `payloads_with_pii()` — payloads seeded with immigration-PII-shaped values
  (A-number-like strings, passport numbers, DOB, country-of-origin) + `tokens()`
  for secret strings — to drive redaction (#9).
- `sign(e)` / `corrupt_signature(...)` — produce valid/invalid HMAC bodies with a
  test signing secret.
- `RetryPolicy(...)` drawn with small bounds so the retry property runs fast.

## Known edge inputs to seed

- Idempotency: same key replayed **concurrently** (interleaved awaits); key reused
  across *different* logical operations (must not collapse).
- Retry: all-`rate_limit` with and without `Retry-After`; mixed transient→success
  on the last allowed attempt; `total_deadline` shorter than `max_attempts` would
  allow.
- Webhook: identical `provider_event_id` arriving many times; unknown `type`
  (must 202-drop, not error); stale timestamp (replay window); oversized body.
- Taxonomy: status `429` vs `503` vs `409`-as-idempotent-replay; an exception type
  never seen before (→ `fatal`).
- Round-trip: matter with empty `external_ids`, `None` optionals, unicode/long
  `title`, and a `key_dates` list (reversible subset only).
- Redaction: PII value that also coincidentally appears in a non-PII field; token
  embedded in a URL query (must still be scrubbed).
- Isolation: one connector forced `open` while another is hammered healthy.
