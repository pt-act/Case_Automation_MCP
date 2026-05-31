# Spec — Connector Framework

> Inherits `specs/_shared/CONVENTIONS.md` and elaborates `concept/PTD.md §6`.
> Describes the *what* (behaviour, interfaces, data, edge cases); implementation
> steps live in `tasks.md`. Domain types come from `platform-foundation`.

## 1. Summary

The Connector Framework is the ports-and-adapters seam between the workflow/
service core and external vendor systems. It provides: (a) four abstract
**port protocols** (Case, CRM, Email, DocStore) that everything above depends on;
(b) a **`ConnectorError` taxonomy** that turns any vendor failure into a
retry/gate/fail decision; (c) **outbound middleware** (async httpx + tenacity
backoff/jitter, timeouts, rate-limit honouring, idempotency-key forwarding);
(d) **inbound webhook ingestion** (verify → normalise → dedupe → enqueue);
(e) a **contract-test harness** + recorded fixtures; (f) **in-memory reference
adapters** per category; (g) the **`CONNECTOR.md`** documentation requirement;
and (h) **graceful degradation** so one connector failing is isolated. Concrete
vendor adapters are out of scope until vendors are confirmed.

## 2. Scope & out-of-scope

**In scope:** port protocols + supporting types (`MatterDraft`, `ACL`); the
`ConnectorError` taxonomy and mapping guidance; the outbound client middleware
(retry/backoff/jitter, timeout, rate-limit, idempotency); webhook verify/
normalise/dedupe/enqueue and the internal `Event` model; the contract-test
harness and fixture layout; in-memory reference adapters; the `CONNECTOR.md`
template; graceful degradation (circuit/health + isolation); a connector
registry for pluggable registration.

**Out of scope:** concrete vendor adapters (`ASSUMPTION (confirm): vendors`);
workflow logic and orchestrator internals (only trigger-enqueue is touched);
domain model, audit, config/secrets, observability primitives, token encryption
(all from `platform-foundation`); approval-gate channels; the deadline/doc-gen/
extraction/QC engines. See `requirements.md §3`.

## 3. Domain types used / introduced

**Used (from `platform-foundation`, PTD §4):** `Contact`, `Matter`, `Document`,
`Deadline`, `Communication`, `Task`. Connectors map vendor payloads ↔ these; the
core never sees a vendor JSON blob (CONVENTIONS §3.10, §5).

**Introduced by this feature** (proposed in `platform-foundation` for shared
reuse per CONVENTIONS §5 if used elsewhere; otherwise local):

- `MatterDraft` — input to `CaseConnector.create_matter` (the to-be-created
  matter before it has a source id). Fields mirror the writable subset of
  `Matter` (reference, title, status, practice_area, client, responsible,
  opened_at, external_ids); no `id`/`source` (assigned by the vendor).
- `ACL` — access-control descriptor for `DocStoreConnector.move`
  (`principals: list[str]`, `permission: Literal["read","write","none"]`,
  `external: bool` — used by privilege-aware routing downstream).
- `Event` — normalised inbound webhook event (see §4.4).
- `ConnectorError` hierarchy (see §4.2).
- `ConnectorHealth` — per-connector health/circuit state (see §5.4).
- `RetryPolicy`, `RateLimitState`, `IdempotencyRecord` — middleware support
  types (see §4.3).

`ASSUMPTION (confirm): MatterDraft/ACL field sets are derived from the domain
model; exact writable subset is confirmed when the first case/doc vendor is known.`

## 4. Interfaces (MCP tools/resources/prompts, ports, internal APIs)

This feature exposes **no MCP tools/resources/prompts directly** — it sits below
them. It exposes ports (to workflows), an HTTP webhook surface (to vendors), and
internal APIs (middleware, registry, harness).

### 4.1 Port protocols (per PTD §6)

```python
class CaseConnector(Protocol):
    async def get_matter(self, id: str) -> Matter: ...
    async def create_matter(self, data: MatterDraft) -> Matter: ...
    async def list_deadlines(self, matter_id: str) -> list[Deadline]: ...

class CRMConnector(Protocol):
    async def upsert_contact(self, c: Contact) -> Contact: ...
    async def find_contact(self, q: str) -> list[Contact]: ...

class EmailConnector(Protocol):
    async def create_draft(self, c: Communication) -> str: ...          # returns draft id
    async def send(self, draft_id: str, idem_key: str) -> str: ...       # returns provider message id

class DocStoreConnector(Protocol):
    async def put(self, doc: Document, content: bytes) -> Document: ...
    async def get(self, id: str) -> tuple[Document, bytes]: ...
    async def move(self, id: str, folder: str, acl: ACL) -> Document: ...
```

- Signatures are **frozen to PTD §6**; additions go through `platform-foundation`.
- `create_draft` is the **draft-creation port** (FR-6): drafts are `write
  (confirm)`; `send` is `gated (human)` and idempotency-keyed (FR-4, NFR-3).
- Write methods (`create_matter`, `upsert_contact`, `send`, `put`, `move`) accept
  or internally derive an **idempotency key** (see §4.3); reads do not.

### 4.2 `ConnectorError` taxonomy

```python
class ConnectorError(Exception):
    connector: str            # e.g. "case", "crm", or adapter name
    retryable: bool           # convenience derived from class
    detail: str               # redacted, PII-free message
    cause: Exception | None

class AuthError(ConnectorError): ...        # 401/403, expired/invalid token, bad scope
class RateLimitError(ConnectorError):       # 429 / quota
    retry_after: float | None
class NotFoundError(ConnectorError): ...     # 404 / unknown id
class TransientError(ConnectorError): ...    # 5xx, timeout, connection reset, DNS
class FatalError(ConnectorError): ...        # 4xx (non-auth/404), schema/mapping failure, exhausted retries
```

**Orchestrator decision contract** (how the consumer reacts):

| Class | `retryable` | Orchestrator action |
|---|---|---|
| `AuthError` | no | **Gate / park** for human (token refresh / re-auth); never silent-retry |
| `RateLimitError` | yes (delayed) | Retry after `retry_after`/backoff, bounded |
| `NotFoundError` | no | Surface to workflow as a domain "missing"; do not retry |
| `TransientError` | yes | Retry with backoff+jitter, bounded |
| `FatalError` | no | **Park run** for human attention; never silent-fail (esp. deadlines) |

**Mapping guidance (adapter responsibility):** every adapter MUST classify each
vendor failure into exactly one class — the mapping is **total** (PBT in
`pbt-properties.md`). Baseline mapping table (overridable per vendor):

| Vendor signal | Class |
|---|---|
| HTTP 401, 403, invalid/expired token, insufficient scope | `AuthError` |
| HTTP 429, quota exceeded | `RateLimitError` |
| HTTP 404, unknown resource id | `NotFoundError` |
| HTTP 408/425/500/502/503/504, timeout, conn reset, DNS | `TransientError` |
| HTTP 400/409/422 (non-idempotent conflict), payload/schema mismatch, anything unmapped | `FatalError` |

`ASSUMPTION (confirm): 409 handling — when a 409 is a duplicate-due-to-
idempotency replay it is treated as success (return existing resource), not
fatal; per-vendor confirmation needed.`

### 4.3 Outbound middleware (internal API)

A shared async client wrapper all adapters call instead of raw `httpx`:

```python
class OutboundClient:
    async def request(self, method, url, *, idem_key: str | None = None,
                      retry: RetryPolicy = DEFAULT, timeout: float = 10.0,
                      classify: Callable[[Response|Exception], ConnectorError|None] = ...,
                      ) -> httpx.Response: ...
```

Responsibilities:
- **Retry/backoff:** `tenacity` exponential backoff **with jitter**; retries only
  `TransientError` and `RateLimitError`; bounded by `RetryPolicy`
  (`max_attempts`, `base_delay`, `max_delay`, `total_deadline`). On exhaustion →
  `FatalError`. Defaults (`ASSUMPTION (confirm)`): `max_attempts=5`,
  `base_delay=0.5s`, `max_delay=30s`, `total_deadline=120s`, full jitter.
- **Timeouts:** per-call connect/read/write timeouts; total-deadline cap.
- **Rate-limit honouring:** on `RateLimitError` wait `retry_after` (or backoff if
  absent); track `RateLimitState` per connector to pre-empt bursts.
- **Idempotency:** for writes, generate a deterministic key when the caller does
  not pass one (`uuid5(namespace, f"{connector}:{operation}:{logical_args}")`),
  forward it to the vendor (header or field per `ASSUMPTION (confirm)`), and keep
  a Redis `IdempotencyRecord` so a repeated in-flight/just-completed write is a
  no-op returning the prior result.
- **Observability + redaction:** emit a span + metric per call; log
  method/host/status/latency/connector — **never** request/response bodies,
  query PII, or tokens (NFR-1, NFR-7, NFR-8).
- **Audit:** state-changing calls write an audit record (foundation) before the
  effect is acknowledged to the caller (NFR-2).

### 4.4 Webhook ingestion (HTTP surface) + `Event` model

FastAPI sidecar route: `POST /webhooks/{connector}`.

```python
class Event(BaseModel):
    id: str                      # internal event id (uuid)
    connector: str               # path param; must be a registered connector
    provider_event_id: str       # vendor's event id (dedup key)
    type: str                    # normalised type, e.g. "matter.status_changed",
                                 # "email.received", "document.signed", "lead.created"
    occurred_at: datetime
    received_at: datetime
    matter_ref: str | None       # external id/ref if resolvable, else None
    payload: dict                # normalised, PII-minimised projection (NOT raw vendor blob)
    raw_ref: str | None          # pointer/hash to stored raw body if retention is enabled
```

Pipeline (per CR-6, CR-7):
1. **Verify** — recompute HMAC-SHA256 (or vendor scheme) over the raw body using
   the per-connector shared secret from the secret store; constant-time compare;
   enforce a timestamp window to bound replay. Fail → `401`/`403`, no enqueue,
   security-event log (no body logged).
2. **Normalise** — map the vendor payload to `Event` via the connector's
   registered normaliser. Unknown/unsupported event type → `202` + drop with a
   metric (not an error; vendors send types we ignore).
3. **Deduplicate** — `SETNX webhook:{connector}:{provider_event_id}` in Redis
   with a TTL (`ASSUMPTION (confirm): 7 days`). If key exists → `200`
   "duplicate ignored", no enqueue.
4. **Enqueue** — push the `Event` to the orchestrator via the `TriggerSink`
   (`ASSUMPTION (confirm): contract owned by workflow-orchestration`). Return
   `202 Accepted`. Enqueue + dedup-set are ordered so a crash cannot enqueue
   without marking processed (see §6).

### 4.5 Connector registry (internal API)

```python
def register_connector(name: str, *, case=None, crm=None, email=None,
                       docstore=None, normaliser=None, secret_ref=None) -> None
def get_connector(name: str, category: Literal["case","crm","email","docstore"])
```

Pluggable registration (CR-8): adding an adapter = registering it; no core change.
The in-memory reference adapters register under the name `"reference"`.

## 5. Behaviour & flows (happy path + state transitions)

### 5.1 Outbound read (happy path)
Workflow → port method (e.g. `get_matter`) → adapter builds request → `OutboundClient.request` (no idem key) → vendor 200 → adapter maps payload → domain model → returns. Span + metric emitted; no audit (read, no side effect).

### 5.2 Outbound write (happy path + idempotent replay)
Workflow → write port (e.g. `send(draft_id, idem_key)`) → middleware checks Redis `IdempotencyRecord`:
- **miss** → forward key to vendor → success → store result in `IdempotencyRecord` (TTL) → write audit → return provider id.
- **hit (completed)** → return stored provider id, **no second vendor call** (single effect).
- **hit (in-flight)** → await/short-poll the in-flight result or return a `TransientError` to retry shortly (caller-bounded).

### 5.3 Inbound webhook (state transitions)
`received → verified → normalised → (deduped? drop : enqueued)`. Each transition emits a metric; failures short-circuit with the status codes in §4.4. Terminal states: `enqueued`, `duplicate_ignored`, `dropped_unknown_type`, `rejected_bad_signature`.

### 5.4 Graceful degradation (circuit/health)
Each connector has a `ConnectorHealth` state machine: `healthy → degraded → open → half_open → healthy`.
- Consecutive `TransientError`/`AuthError` beyond a threshold trips `open` (circuit) — calls fail fast with the last classified error instead of hammering the vendor.
- After a cool-down, `half_open` allows a probe; success → `healthy`, failure → `open`.
- A connector in `open` returns its error **only to callers of that connector**; unrelated connectors and the webhook pipeline for other connectors are unaffected (NFR-9). Health changes emit metrics + a log + (on `open`) an alert signal.
`ASSUMPTION (confirm): circuit thresholds (e.g. 5 failures / 30s window, 60s cool-down).`

## 6. Edge cases & error handling (incl. ConnectorError handling)

- **Retry exhaustion:** bounded attempts/total-deadline reached → raise
  `FatalError` (orchestrator parks run; never silent-fail).
- **Rate limit without `Retry-After`:** fall back to exponential backoff+jitter;
  do not treat as fatal until budget exhausted.
- **Auth/token expiry mid-flight:** classify `AuthError`, do **not** retry blindly;
  surface for re-auth/refresh (park). Token refresh itself is least-privilege and
  uses encrypted tokens from foundation.
- **Idempotency-key collision across distinct operations:** keys are namespaced
  by `connector:operation:logical_args`; identical logical writes share a key
  (desired), distinct writes never collide.
- **Vendor returns 409 on idempotent replay:** treat as success and fetch/return
  the existing resource (`ASSUMPTION (confirm)` per vendor) rather than `FatalError`.
- **Webhook with bad/missing signature:** `401`/`403`, no enqueue, security log,
  metric; never log the body.
- **Webhook replay / at-least-once delivery:** dedup by `provider_event_id`
  (exactly-once enqueue). **Crash-safety:** mark-processed (Redis SETNX) is
  committed **before** acknowledging; enqueue is idempotent (orchestrator
  trigger keyed by `Event.id`), so a crash between set and enqueue is recovered
  by a reconcile sweep that re-enqueues un-acked events.
  `ASSUMPTION (confirm): reconcile sweep cadence.`
- **Unknown connector in path:** `404`. **Unknown event type:** `202` + drop
  metric (not an error).
- **Malformed/oversized webhook body:** reject `400`/`413` before normalisation;
  size cap configurable (`ASSUMPTION (confirm): default 1 MB`).
- **Redis unavailable (dedup/idempotency backend down):** fail **closed** for
  writes (refuse rather than risk double-effect) and for webhook dedup (return
  `503` so the provider retries) — preserves NFR-3 over availability.
- **Partial mapping failure (vendor payload missing a required field):**
  `FatalError` with a redacted, field-name-only message; never echo the value.
- **One connector down:** isolated via circuit (§5.4); other connectors and
  workflows continue (NFR-9).

## 7. Risk tiers & gates for each action

Ports themselves are plumbing; risk tier is a property of the *operation*:

| Operation | Risk tier | Notes |
|---|---|---|
| `get_matter`, `list_deadlines`, `find_contact`, `get` (doc) | `read` | No gate, no audit-state-change. |
| `create_matter`, `upsert_contact`, `put` (doc), `move` (doc), `create_draft` | `write (confirm)` | Reversible/draft; idempotency-keyed; audited. |
| `send` (email) | `gated (human)` | External/irreversible; the gate is enforced by `workflow-orchestration`, not here — this feature only executes a `send` that arrives **already approved**, and enforces idempotency so an approved send fires once (NFR-4, NFR-3). |
| Webhook ingestion | n/a (inbound) | No outbound side effect; produces a trigger only. Verification is the security gate. |

This feature does **not** implement approval UI/channels; it honours the tier by
keeping `send` idempotent and audited and by leaving gating to the orchestrator.

## 8. Data & persistence

- **Redis:** `IdempotencyRecord` (key → result + status + TTL), webhook dedup
  keys (`webhook:{connector}:{provider_event_id}`, TTL), `RateLimitState`,
  circuit/`ConnectorHealth` state, un-acked-event set for the reconcile sweep.
  TTLs: `ASSUMPTION (confirm): idempotency 24h, webhook dedup 7d`.
- **Postgres (via `platform-foundation`):** audit records for state-changing
  calls and accepted events; optional raw-webhook-body retention table (hashed/
  referenced by `Event.raw_ref`) if residency policy permits
  (`ASSUMPTION (confirm): whether to retain raw bodies, and where`).
- **Secret store:** per-connector OAuth tokens (encrypted at column level by
  foundation) and webhook signing secrets; read at runtime, never logged.
- **No new domain tables** beyond audit/optional-raw; this feature is mostly
  stateless plus Redis coordination.

## 9. Observability (logs/metrics/traces for this feature)

- **Logs (structlog, PII-scrubbed):** outbound `connector, operation, host,
  http_status, latency_ms, attempt, idem_key_hash`; webhook `connector,
  provider_event_id_hash, event_type, decision`. Never bodies/PII/tokens.
- **Metrics (Prometheus):** `connector_request_total{connector,operation,result}`,
  `connector_request_latency_seconds`, `connector_retry_total`,
  `connector_circuit_state{connector}`, `webhook_received_total{connector,decision}`,
  `webhook_verify_fail_total{connector}`, `idempotency_replay_total`.
- **Traces (OpenTelemetry):** one span per outbound call (child of the workflow
  step span via `run_id`) and per webhook pipeline; span attributes are
  PII-free.
- **Alerts:** circuit `open`, webhook verify-failure spike, idempotency-backend
  (Redis) unavailable, reconcile-sweep backlog.

## 10. Security & privilege considerations

- **HMAC webhook verification** with constant-time compare + replay window
  (NFR-1). Per-connector secrets from the secret store.
- **Least-privilege OAuth scopes** per connector; documented in `CONNECTOR.md`;
  the agent/service identity holds only what each adapter needs (NFR-1, §7
  baseline).
- **Token encryption** reused from `platform-foundation` (column-level); tokens
  never in code/logs (NFR-8).
- **No payload PII in logs/traces/metrics** — redaction enforced and PBT-tested.
  Heightened for immigration PII (A-numbers, passports, biometrics, status,
  country-of-origin) per CONVENTIONS §7.
- **Privilege-aware boundary:** `ACL.external` on `move` lets downstream routing
  prevent privileged docs reaching external recipients; this feature carries the
  flag faithfully but enforcement lives in `document-routing`/`qc-verification`.
- **Audit-before-ack** for state changes (NFR-2). See `security-audit-prep.md`.

## 11. Dependencies & integration points

- **`platform-foundation`#domain, #audit, #config-secrets, #observability,
  #token-encryption, #redis** — consumed directly.
- **`workflow-orchestration`** — consumes enqueued triggers (`TriggerSink`),
  reads `ConnectorError` class to choose retry/gate/fail. Coupling is the trigger
  contract + error taxonomy only.
- **All workflow specs** (`client-intake`, `status-update-emails`,
  `document-generation`, `document-routing`, `deadline-engine`) depend on this
  feature's ports.
- **External vendors** — reached via adapters (out of scope here).
- **Integration point for new adapters:** implement a port + normaliser, register
  via the registry, pass the contract-test harness, ship `CONNECTOR.md`.

## 12. Test strategy (focused tests + pointer to PBT)

- **Focused (pytest + respx/vcr):** per-port reference-adapter behaviour;
  taxonomy mapping for each baseline HTTP status; retry-only-transient;
  rate-limit honouring of `Retry-After`; webhook valid/invalid signature; webhook
  dedup; unknown-type drop; circuit open/half-open/closed transitions; redaction
  (no PII/token in logs); audit-before-ack; CI doc-presence check.
- **Contract-test harness:** the conformance suite every adapter (incl. the
  reference adapters) must pass — recorded fixtures under
  `tests/connectors/fixtures/<connector>/`.
- **PBT (see `pbt-properties.md`):** write idempotency (single effect), bounded
  retry eventually surfaces fatal, webhook dedup exactly-once, taxonomy mapping
  totality, payload↔domain round-trip where reversible.

## 13. Open questions

1. `ASSUMPTION (confirm):` which vendor per category ships first (PRD §11.1)?
2. `ASSUMPTION (confirm):` per-vendor webhook signature scheme + event-id location?
3. `ASSUMPTION (confirm):` per-vendor idempotency mechanism (header vs field vs none)?
4. `ASSUMPTION (confirm):` `TriggerSink` contract owned by `workflow-orchestration`?
5. `ASSUMPTION (confirm):` retain raw webhook bodies? if so, where (residency)?
6. `ASSUMPTION (confirm):` retry/backoff + circuit + TTL default tuning per vendor SLA?
7. `ASSUMPTION (confirm):` is e-signature (DocuSign) a webhook source or its own port for v1?
