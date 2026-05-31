# Requirements — Connector Framework

> Inherits `specs/_shared/CONVENTIONS.md` (process, domain model, risk tiers,
> compliance baseline, glossary) and builds on `concept/PTD.md §6` (connector
> layer) and `concept/PRD.md §5.1` (connectivity FRs). Read those first; this
> file does not repeat them.

## 1. Context & problem

The product integrates four categories of external system — case management,
CRM, email, and document store — into one normalised, AI-orchestratable surface
(PRD G1, G6). The firm's actual vendors are **not yet confirmed** (PRD §11,
CONVENTIONS §4), so the system cannot be built against any single vendor's API.

The Connector Framework is the **vendor-agnostic ports-and-adapters layer**
(PTD §6). It defines the abstract interfaces (ports) that every workflow and
engine depends on, the outbound-call discipline (retries, rate limits,
idempotency) every adapter must follow, the inbound webhook ingestion path that
turns external events into orchestrator triggers, the error taxonomy that lets
the orchestrator decide retry-vs-gate-vs-fail, and the contract-test +
documentation discipline that keeps each future adapter honest.

This feature is **foundation, not workflow**: it provides the seams into which
concrete vendor adapters drop later, and the in-memory reference adapters that
let everything above it be built and tested today without any real vendor.

Without it, every workflow would hard-wire a vendor (violating CONVENTIONS §3.10),
retries/idempotency would be re-implemented per integration, and one flaky
external API could take the whole system down (violates NFR-9).

## 2. In scope

- **Connector port protocols** per category: `CaseConnector`, `CRMConnector`,
  `EmailConnector`, `DocStoreConnector` (signatures per PTD §6). Plus the
  `MatterDraft` / `ACL` supporting types referenced by those signatures.
- **`ConnectorError` taxonomy**: `auth`, `rate_limit`, `not_found`, `transient`,
  `fatal`, with mapping guidance (how an adapter classifies a vendor failure)
  and the retry-vs-gate-vs-fail decision contract for the orchestrator.
- **Outbound discipline / middleware**: a shared `httpx` async client wrapper
  with `tenacity`-based exponential backoff + jitter, timeout budgets,
  rate-limit honouring (respect `Retry-After` / vendor headers), and
  idempotency-key generation + forwarding on writes.
- **Inbound webhook ingestion**: the FastAPI sidecar route `/webhooks/{connector}`
  — verify (HMAC / shared secret) → normalise to internal `Event` → deduplicate
  (provider event id → Redis) → enqueue as an orchestrator trigger.
- **The internal `Event` model**: the normalised inbound-event shape that the
  orchestrator consumes.
- **Contract-test harness**: a reusable harness (respx / vcr against recorded
  fixtures) plus a fixture layout, so every future adapter is verified against
  recorded vendor interactions.
- **In-memory reference adapters** per category: deterministic fakes implementing
  each port, for local dev, tests, and as the conformance reference.
- **`CONNECTOR.md` template + documentation requirement**: the required
  per-adapter doc (auth model, scopes, endpoints, webhook events, rate limits,
  quirks); a connector is not "done" without it (PRD FR-18, PTD §16).
- **Graceful degradation**: one connector failing must not cascade; degraded
  connectors are isolated and reported (NFR-9).

## 3. Out of scope

- **Concrete vendor adapters** (Clio, Salesforce, Lawmatics, Microsoft Graph,
  Gmail, SharePoint/OneDrive, NetDocuments, Google Drive, DocuSign). Deferred
  until vendors are confirmed. `ASSUMPTION (confirm): which vendors per category`.
- **Workflow logic** and the **orchestrator internals** — owned by
  `workflow-orchestration`. This feature only *enqueues* triggers to it and
  *exposes* ports it calls.
- **Domain model definitions** (`Contact`, `Matter`, `Document`, `Deadline`,
  `Communication`, `Task`), audit log, config/secrets loading, observability
  primitives, and column-level token encryption — all owned by
  `platform-foundation` and reused here, not redefined.
- **The deadline engine, document generation, extraction, QC** — they consume
  ports; they are separate specs.
- **The web-UI / email-action approval channels** — owned by
  `workflow-orchestration` + their workflow specs; gates are not implemented here.

## 4. Users / actors

| Actor | Interaction with this feature |
|---|---|
| **Workflow orchestrator** | Calls ports for reads/writes; receives webhook-derived triggers; reads `ConnectorError` class to decide retry/gate/fail. |
| **The AI agent** (via MCP tools) | Indirect — MCP tools call workflows/services which call ports. Never touches a vendor payload. |
| **External systems** (case/CRM/email/docs vendors) | Receive outbound API calls; emit inbound webhooks. |
| **Operations / Admin** | Onboards a new connector; reads `CONNECTOR.md`; watches connector health/degradation. |
| **Connector developer** (internal, 1–2 devs) | Implements a new adapter against a port + contract-test harness; writes `CONNECTOR.md`. |

## 5. Functional requirements (trace to PRD FR-xx)

- **CR-1** Provide one connector **port protocol per category** (Case, CRM,
  Email, DocStore) with the signatures in PTD §6; workflows depend on ports
  only. *(FR-1, FR-16)*
- **CR-2** Provide a **draft-creation port** on `EmailConnector`
  (`create_draft`) and a `send` port keyed by an idempotency key, so
  email-drafting workflows depend on a port, not a vendor. *(FR-6, FR-4)*
- **CR-3** All outbound write calls are **idempotent**: an idempotency key is
  generated/accepted and forwarded to the vendor; a repeated write with the same
  key produces a single effect. *(FR-4, NFR-3)*
- **CR-4** All outbound calls use **retry with exponential backoff + jitter**,
  bounded attempts, per-call timeouts, and **honour rate limits**
  (`Retry-After` / vendor headers). *(FR-4)*
- **CR-5** Adapters surface a **typed `ConnectorError` taxonomy** so the
  orchestrator can decide retry vs gate vs fail. Mapping from vendor failure to
  taxonomy class is **total** (every error maps to exactly one class). *(FR-4,
  NFR-9)*
- **CR-6** **Inbound webhook ingestion** at `/webhooks/{connector}`: verify
  signature → normalise to `Event` → deduplicate by provider event id → enqueue
  as orchestrator trigger. *(FR-3)*
- **CR-7** Webhook ingestion is **deduplicated**: the same provider event id is
  processed exactly once even under provider retries/at-least-once delivery.
  *(FR-3, NFR-3)*
- **CR-8** A new connector or workflow can be added **without modifying core**:
  register an adapter that satisfies a port; nothing above changes. *(FR-16)*
- **CR-9** Ship **in-memory reference adapters** per category that satisfy the
  ports and pass the contract-test harness, for local dev and testing. *(FR-1
  enabler, FR-16)*
- **CR-10** Ship a **contract-test harness** (respx/vcr + recorded fixtures) that
  any adapter must pass to be considered conformant. *(FR-16, NFR-3)*
- **CR-11** Every adapter ships a **`CONNECTOR.md`** (auth, scopes, endpoints,
  webhook events, rate limits, quirks); enforced as a merge requirement. *(FR-18)*
- **CR-12** **Graceful degradation**: a failing connector is isolated (circuit /
  health state) and reported; other connectors and workflows keep operating.
  *(FR-4, NFR-9)*

## 6. Non-functional requirements (trace to PRD NFR-xx)

- **NFR-3 (Reliability):** idempotent writes; safe bounded retries; no
  duplicate sends/filings; webhook exactly-once processing. *(CR-3, CR-4, CR-7)*
- **NFR-9 (Graceful degradation):** one connector down ≠ whole system down;
  failures are isolated and observable. *(CR-5, CR-12)*
- **NFR-1 (Confidentiality / privilege):** OAuth tokens read from the secret
  store and used with least-privilege scopes; no payload PII in logs/traces.
- **NFR-2 (Auditability):** every outbound state-changing call and every
  accepted inbound event writes through the `platform-foundation` audit log
  before the effect is considered complete.
- **NFR-7 (Observability):** structured logs, metrics, and traces per connector
  call and per webhook (PII-scrubbed), correlated by `run_id` where available.
- **NFR-8 (Secret hygiene):** no credentials in code; tokens from the central
  store; rotation-friendly; never logged.
- **NFR-5 (Latency):** outbound read calls target < 2s typical; retry budgets
  bounded so a slow vendor cannot stall a request indefinitely (long work is
  async via the orchestrator).
- **NFR-6 (Residency):** connector egress endpoints and webhook ingress are
  region-configurable; no behaviour hard-codes a region.

## 7. Dependencies (other feature specs, external systems)

- **`platform-foundation`** (hard dependency): domain model (`Contact`,
  `Matter`, `Document`, `Deadline`, `Communication`, `Task`), audit log, config
  & secret loading, observability (structlog/OTel/Prometheus), column-level
  token encryption, Redis access. This feature reuses all of these.
- **`workflow-orchestration`** (consumer + light coupling): receives enqueued
  triggers from webhook ingestion and reads `ConnectorError` classes. This
  feature depends only on a **trigger-enqueue interface** it can call, not on
  orchestrator internals. `ASSUMPTION (confirm): the enqueue interface shape is
  owned by workflow-orchestration; pending that, this spec defines a minimal
  `TriggerSink` port it calls.`
- **External systems:** the four vendor categories (unknown vendors) reached via
  HTTPS APIs and emitting webhooks. Concrete adapters are out of scope.
- **Infra:** Redis (idempotency keys, webhook dedup, circuit state), the secret
  store (tokens), Postgres (audit, via foundation).

## 8. Assumptions & open questions

- `ASSUMPTION (confirm):` **Which vendor per category** (Case/CRM/Email/Docs)
  ships first — drives the first concrete adapters, their auth flows, and webhook
  formats. All four are unknown today (PRD §11.1).
- `ASSUMPTION (confirm):` **Webhook signature scheme per provider.** Baseline
  assumption: HMAC-SHA256 over the raw request body with a per-connector shared
  secret, signature in a provider-specific header, with a timestamp to bound
  replay. Real schemes vary per vendor and are confirmed at adapter time.
- `ASSUMPTION (confirm):` **Provider event-id location** for dedup differs per
  vendor; the reference uses a configurable extractor per connector.
- `ASSUMPTION (confirm):` **Idempotency mechanism per vendor.** Some vendors
  accept an `Idempotency-Key` header; others dedupe on a client-supplied
  reference field; others not at all (then we guard with a Redis-side
  request-fingerprint lock). Baseline: forward an `Idempotency-Key` header and
  also keep a Redis guard.
- `ASSUMPTION (confirm):` **Rate-limit signalling per vendor** (`Retry-After`,
  `X-RateLimit-*`, or 429 only). Baseline honours `Retry-After`, falls back to
  backoff.
- `ASSUMPTION (confirm):` **OAuth scopes per vendor** — least-privilege scope
  list is confirmed per adapter and documented in its `CONNECTOR.md`.
- `ASSUMPTION (confirm):` **Trigger-enqueue contract** with
  `workflow-orchestration` (queue name, payload schema). Until confirmed, a
  minimal `TriggerSink` port is used and backed by Redis.
- `ASSUMPTION (confirm):` **DocuSign (or other e-signature) is modelled as a
  webhook event source** feeding `Event`, not a fifth port category, for v1.
- `ASSUMPTION (confirm):` **Retry/backoff budget defaults** (max attempts, base
  delay, max delay, total deadline). Baseline below in `spec.md §6`; tune per
  vendor SLA.

## 9. Acceptance criteria (testable checklist)

- [ ] Each of the four ports is defined as a `typing.Protocol` with exactly the
  PTD §6 signatures; a workflow can be written importing only the port, with no
  vendor import reachable.
- [ ] `ConnectorError` is a typed hierarchy with exactly five terminal classes
  (`auth`, `rate_limit`, `not_found`, `transient`, `fatal`); a documented
  mapping table classifies common HTTP statuses and exception types into them.
- [ ] The retry middleware retries only `transient` (and `rate_limit` after its
  delay), never `auth`/`not_found`/`fatal`; attempts are bounded; on exhaustion
  it raises a `fatal` (or surfaces the last error reclassified) — verified by test.
- [ ] A repeated write through any reference adapter with the **same idempotency
  key** yields exactly one effect (one created/sent object); a different key
  yields a second effect — verified by test.
- [ ] `POST /webhooks/{connector}` with a **valid** signature returns 2xx,
  normalises to an `Event`, and enqueues exactly one trigger; with an **invalid**
  signature returns 401/403 and enqueues nothing.
- [ ] Posting the **same provider event id twice** results in exactly one
  enqueued trigger (dedup), and the second returns a 200 "duplicate ignored"
  without side effects.
- [ ] In-memory reference adapters for all four categories pass the contract-test
  harness.
- [ ] Killing/forcing-failure of one connector leaves the other three usable and
  emits a degradation signal (metric/log); a single connector's circuit opening
  does not raise from unrelated connectors — verified by test.
- [ ] No connector log line, trace attribute, or audit `inputs/outputs` field
  contains raw payload PII or a secret/token — verified by a redaction test.
- [ ] CI fails if an adapter directory lacks a `CONNECTOR.md` with all required
  sections.
- [ ] Every state-changing outbound call writes an audit record (via
  `platform-foundation`) before returning success.
