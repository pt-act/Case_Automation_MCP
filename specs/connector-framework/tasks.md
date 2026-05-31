# Tasks — Connector Framework

> Inherits `specs/_shared/CONVENTIONS.md §10` (status/size/dependency legend).
> Sizes: `XS`/`S`/`M`/`L` (iterations, not time). Cross-spec deps use
> `<slug>#<task-id>`. Definition of done = code + tests + docs (CONVENTIONS §16).

## Overview (task groups + critical path + parallelisation)

Seven groups. Critical path: **G1 (ports + types) → G2 (taxonomy) → G3 (outbound
middleware) → G6 (reference adapters) → G7 (contract harness)**. Webhook
ingestion (G4) and graceful degradation (G5) branch off after G2/G3 and run in
parallel. Documentation (G7) gates merges throughout.

```
G1 ─┬─► G2 ─┬─► G3 ─┬─► G6 ─► G7
    │       │       └─► G4 (webhook)         (parallel after G3)
    │       └─► G5 (degradation)             (parallel after G2/G3)
    └─ uses platform-foundation (domain, audit, config/secrets, redis, obs)
```

Cross-spec dependencies: all groups depend on `platform-foundation` deliverables
(domain model, audit, config/secrets, observability, Redis, token encryption).
G4 enqueue depends on `workflow-orchestration#trigger-sink`
(`ASSUMPTION (confirm)`; mocked until available).

---

## Group 1: Ports & supporting types

- [ ] **1.1** Define the four port `Protocol`s (`CaseConnector`, `CRMConnector`,
  `EmailConnector`, `DocStoreConnector`) with PTD §6 signatures. — **size S**;
  depends on: `platform-foundation#domain-model`; parallel: no.
  - *Acceptance:* protocols import only domain types; `mypy` treats the reference
    adapters as structurally conforming; no vendor import reachable from a port.
- [ ] **1.2** Define supporting types `MatterDraft`, `ACL` as Pydantic v2 models
  (writable subset of `Matter`; ACL principals/permission/external). — **size S**;
  depends on: 1.1; parallel: no.
  - *Acceptance:* `MatterDraft` has no `id`/`source`; `ACL.external: bool` present
    for privilege routing.
- [ ] **1.3** Connector registry (`register_connector` / `get_connector`) for
  pluggable registration. — **size S**; depends on: 1.1; parallel: yes.
  - *Acceptance:* registering an adapter requires no edit to core; `get_connector`
    raises a clear error for an unknown name/category.

**Focused tests:** (a) reference adapter satisfies each Protocol via `mypy`/
runtime `isinstance`-with-`runtime_checkable`; (b) `MatterDraft` rejects `id`;
(c) registry returns registered adapter; (d) registry raises on unknown
name/category.

---

## Group 2: ConnectorError taxonomy + mapping

- [ ] **2.1** Implement `ConnectorError` base + 5 terminal classes with
  `connector`, `retryable`, redacted `detail`, `cause`. — **size S**;
  depends on: 1.1; parallel: no.
  - *Acceptance:* exactly five terminal classes; `retryable` true only for
    `TransientError`/`RateLimitError`; `RateLimitError.retry_after` optional float.
- [ ] **2.2** Baseline mapping helper `classify(response_or_exc) -> ConnectorError`
  implementing the spec §4.2 table, overridable per adapter. — **size M**;
  depends on: 2.1; parallel: no.
  - *Acceptance:* every input maps to exactly one class (totality); 401/403→auth,
    429→ratelimit, 404→notfound, 5xx/timeout→transient, else→fatal; unmapped→fatal.
- [ ] **2.3** Document the orchestrator decision contract (retry/gate/fail per
  class) in the module docstring + `CONNECTOR.md` template. — **size XS**;
  depends on: 2.1; parallel: yes.
  - *Acceptance:* table present and matches spec §4.2.

**Focused tests:** (a) each baseline HTTP status → expected class; (b) timeout/
conn-reset → transient; (c) unmapped/None → fatal; (d) `retryable` flags correct
per class; (e) `detail` contains no body/PII (redaction).

---

## Group 3: Outbound middleware (httpx + tenacity)

- [ ] **3.1** `OutboundClient.request` wrapping async `httpx` with per-call
  timeouts + total-deadline. — **size M**; depends on: 2.2; parallel: no.
  - *Acceptance:* connect/read/write timeouts enforced; total-deadline caps
    cumulative retry time.
- [ ] **3.2** Retry/backoff via `tenacity`: exponential + full jitter, bounded
  `RetryPolicy`; retry only transient/ratelimit; exhaustion → `FatalError`. —
  **size M**; depends on: 3.1; parallel: no.
  - *Acceptance:* `auth`/`not_found`/`fatal` never retried; attempts bounded by
    `max_attempts` and `total_deadline`; exhaustion raises `FatalError`.
- [ ] **3.3** Rate-limit honouring: respect `Retry-After`, track `RateLimitState`
  per connector; fall back to backoff when header absent. — **size M**;
  depends on: 3.2; parallel: no.
  - *Acceptance:* a 429 with `Retry-After: N` waits ≥N before retry; absent header
    → backoff; never treated as fatal until budget exhausted.
- [ ] **3.4** Idempotency: generate `uuid5` key when absent, forward to vendor,
  Redis `IdempotencyRecord` guard (miss→call+store, hit→return prior). — **size L
  → split** into 3.4a key generation/forwarding and 3.4b Redis guard + in-flight
  handling. depends on: 3.1, `platform-foundation#redis`; parallel: no.
  - *Acceptance:* repeated write same key → single vendor call + same result;
    different key → second effect; Redis-down → fail closed (no double-effect).
- [ ] **3.5** Observability + redaction hook + audit-before-ack for state
  changes. — **size M**; depends on: 3.1, `platform-foundation#audit`,
  `platform-foundation#observability`; parallel: yes.
  - *Acceptance:* span+metric per call; no body/PII/token logged; state-changing
    call writes audit before returning success.

**Focused tests:** (a) transient retried then succeeds; (b) retry exhaustion →
fatal; (c) `Retry-After` honoured; (d) idempotent replay → one effect; (e)
different key → two effects; (f) Redis-down write fails closed; (g) audit written
before ack; (h) log scrubber drops token/PII fields.

---

## Group 4: Webhook ingestion (verify → normalise → dedupe → enqueue)

- [ ] **4.1** FastAPI route `POST /webhooks/{connector}` in the sidecar; raw-body
  capture; size cap; unknown-connector → 404. — **size S**; depends on: 1.3;
  parallel: yes.
  - *Acceptance:* raw body available for HMAC; >cap → 413; unknown connector → 404.
- [ ] **4.2** HMAC-SHA256 verification (constant-time compare) + replay-window
  timestamp check; secret from secret store. — **size M**; depends on: 4.1,
  `platform-foundation#config-secrets`; parallel: no.
  - *Acceptance:* valid sig → proceed; invalid/missing → 401/403 + security log +
    metric, no enqueue; stale timestamp → reject.
- [ ] **4.3** Normalisation: per-connector normaliser → `Event` model; unknown
  type → 202 drop + metric. — **size M**; depends on: 4.2; parallel: no.
  - *Acceptance:* known type → well-formed `Event`; unknown type → 202 dropped, no
    enqueue; `Event.payload` is PII-minimised projection, not raw blob.
- [ ] **4.4** Dedup via Redis `SETNX webhook:{connector}:{provider_event_id}` +
  TTL; duplicate → 200 ignored. — **size S**; depends on: 4.3,
  `platform-foundation#redis`; parallel: no.
  - *Acceptance:* same provider event id twice → exactly one pass-through; second
    → 200 "duplicate ignored", no enqueue.
- [ ] **4.5** Enqueue to `TriggerSink`; mark-processed before ack; reconcile sweep
  for crash recovery. — **size M**; depends on: 4.4,
  `workflow-orchestration#trigger-sink` (`ASSUMPTION (confirm)`, mock until ready);
  parallel: no.
  - *Acceptance:* verified+new event enqueues exactly one trigger; crash between
    set and enqueue is re-enqueued by sweep; Redis-down → 503 (provider retries).

**Focused tests:** (a) valid signature passes; (b) invalid/missing → 401/403, no
enqueue; (c) stale timestamp rejected; (d) known type → `Event`; (e) unknown type
→ 202 drop; (f) duplicate event id → single enqueue; (g) enqueue calls
`TriggerSink` once; (h) Redis-down → 503.

---

## Group 5: Graceful degradation (circuit + health)

- [ ] **5.1** `ConnectorHealth` state machine (healthy→degraded→open→half_open→
  healthy) with configurable thresholds/cool-down. — **size M**; depends on: 2.1,
  `platform-foundation#redis`; parallel: yes.
  - *Acceptance:* threshold breaches trip `open`; cool-down → `half_open`; probe
    success → `healthy`, failure → `open`.
- [ ] **5.2** Wire circuit into `OutboundClient`: `open` fails fast with last
  classified error; isolation per connector. — **size M**; depends on: 5.1, 3.1;
  parallel: no.
  - *Acceptance:* `open` connector fails fast (no vendor call); a connector's open
    circuit does not raise from a different connector's calls or webhook pipeline.
- [ ] **5.3** Health metrics + alert signal on `open`. — **size XS**; depends on:
  5.1, `platform-foundation#observability`; parallel: yes.
  - *Acceptance:* `connector_circuit_state` metric reflects transitions; `open`
    emits an alert signal.

**Focused tests:** (a) N failures trip open; (b) open → fail-fast; (c) cool-down →
half-open probe; (d) probe success closes; (e) connector isolation (one open,
others healthy); (f) circuit-state metric emitted.

---

## Group 6: In-memory reference adapters (per category)

- [ ] **6.1** `ReferenceCaseConnector`, `ReferenceCRMConnector`,
  `ReferenceEmailConnector`, `ReferenceDocStoreConnector` — deterministic
  in-memory stores implementing all port methods with idempotency. — **size L →
  split** per category (6.1a–6.1d). depends on: 1.1, 1.2, 3.4; parallel: yes
  across categories.
  - *Acceptance:* every port method implemented; writes honour idempotency keys;
    deterministic outputs for fixed inputs; registered under name `"reference"`.
- [ ] **6.2** Fault-injection knobs on reference adapters (force transient/auth/
  ratelimit/fatal; force rate-limit header) for testing degradation + retries. —
  **size S**; depends on: 6.1; parallel: yes.
  - *Acceptance:* each error class injectable; used by G3/G5 tests.

**Focused tests:** (a) each category round-trips a domain object; (b) email
`create_draft`→`send` idempotent; (c) docstore `put`→`get` checksum stable; (d)
case `create_matter` returns assigned id; (e) injected transient triggers retry;
(f) injected fatal parks.

---

## Group 7: Contract-test harness + CONNECTOR.md + degradation docs

- [ ] **7.1** Contract-test harness (respx/vcr) + fixture layout
  `tests/connectors/fixtures/<connector>/`; a `run_contract(adapter)` conformance
  suite. — **size M**; depends on: 1.1, 6.1; parallel: no.
  - *Acceptance:* reference adapters pass; a deliberately non-conforming stub
    fails; fixtures are vendor-recorded interactions (no live calls in CI).
- [ ] **7.2** `CONNECTOR.md` template (auth, scopes, endpoints, webhook events,
  rate limits, quirks) + CI check that each adapter dir has a complete one. —
  **size S**; depends on: none (doc); parallel: yes.
  - *Acceptance:* CI fails when an adapter lacks `CONNECTOR.md` or a required
    section; template committed.
- [ ] **7.3** Feature README/usage notes: how to add an adapter (port →
  normaliser → register → contract test → `CONNECTOR.md`). — **size XS**;
  depends on: 7.1, 7.2; parallel: yes.
  - *Acceptance:* a developer can follow it to add a no-op adapter end-to-end.

**Focused tests:** (a) harness passes reference adapters; (b) harness fails a
broken stub; (c) CI doc-check fails on missing `CONNECTOR.md`; (d) CI doc-check
fails on a missing required section.

---

## Dependency graph (intra-spec + cross-spec)

```
platform-foundation#{domain, audit, config-secrets, observability, redis, token-encryption}
        │
        ▼
G1 ──► G2 ──► G3 ──► G6 ──► G7
 │      │      ├──► G4 ──► workflow-orchestration#trigger-sink (ASSUMPTION: mock)
 │      │      └──► (G3.5 audit/obs) ── platform-foundation#{audit, observability}
 │      └──► G5 (degradation) ──► G3 (wire-in)
 └──► G1.3 registry feeds G4.1, G6.1
```

Intra-spec critical path: 1.1 → 2.1 → 2.2 → 3.1 → 3.2 → 3.4 → 6.1 → 7.1.
Parallelisable: 1.3, 3.5, G4 (after 3.x), G5 (after 2.1/3.1), 6.1a–d, 7.2.

## Definition of done (code + tests + docs)

A task/group is **done** only when:
- **Code:** implemented against the ports; no vendor names hard-wired
  (CONVENTIONS §3.10); components under ~400 LOC or split (CONVENTIONS §3.5).
- **Tests:** the group's focused tests pass; relevant PBT properties
  (`pbt-properties.md`) pass; reference adapters pass the contract harness.
- **Docs:** module docstrings current; `CONNECTOR.md` template + CI check in
  place; any `ASSUMPTION (confirm)` is recorded in `spec.md §13`; the
  security-sensitive surfaces are reflected in `security-audit-prep.md`.
- **Audit + redaction:** state-changing paths write audit before ack; no PII/
  token in logs/traces/metrics (verified by test).
