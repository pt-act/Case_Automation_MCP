# Security Audit Prep — Connector Framework

> Inherits the security/compliance baseline in `specs/_shared/CONVENTIONS.md §7`
> and `concept/PTD.md §12`. This file re-checks *this feature's* surfaces against
> that baseline (the floor, not the ceiling). NFR references trace to
> `concept/PRD.md §6`.

## Sensitive surfaces (data, actions, external calls)

| # | Surface | Why sensitive |
|---|---|---|
| S1 | **Inbound webhook endpoint** `/webhooks/{connector}` | Public ingress; attacker-controlled bodies; signature is the only trust gate; replay/forgery risk. |
| S2 | **Outbound vendor API calls** (httpx) | Carry client PII to third parties; bear OAuth tokens; SSRF/egress-control concerns. |
| S3 | **OAuth tokens & webhook signing secrets** | Long-lived credentials; least-privilege scopes; must be encrypted, never logged (NFR-8). |
| S4 | **Logs / traces / metrics** from connector calls | Risk of leaking payload PII or tokens into telemetry (NFR-1). |
| S5 | **Redis coordination data** (idempotency, dedup, circuit, rate-limit) | Holds keys derived from request content; integrity affects no-double-send guarantee. |
| S6 | **Audit records** for state-changing calls | Must capture actor/action/inputs/outputs without storing raw PII in the clear beyond policy. |
| S7 | **`Event.payload` projection & optional raw-body retention** | Could persist immigration PII; residency-sensitive (NFR-6). |
| S8 | **Connector registry / adapter loading** | A malicious/misconfigured adapter could exfiltrate or bypass redaction. |
| S9 | **`ACL.external` flag on `move`** | Mis-set value could enable privileged docs reaching external recipients (downstream privilege breach). |

## Threats & mitigations (map to NFR-1,2,4,6,8)

| Threat | Surface | Mitigation | NFR |
|---|---|---|---|
| Forged/spoofed webhook triggers a workflow | S1 | HMAC-SHA256 verify with constant-time compare; per-connector secret from store; reject unsigned/invalid → 401/403, no enqueue | NFR-1 |
| Webhook replay → duplicate workflow run | S1, S5 | Timestamp replay-window check + `provider_event_id` dedup (exactly-once enqueue) | NFR-3*, NFR-1 |
| Token theft via logs/exceptions | S3, S4 | Tokens read at runtime from encrypted store; redaction scrubber; PBT redaction-totality; no env-dumping | NFR-8, NFR-1 |
| PII leakage into telemetry/audit | S4, S6, S7 | Field-level redaction; `Event.payload` is a PII-minimised projection; audit stores references/hashes, not raw values where avoidable | NFR-1, NFR-2 |
| Over-broad OAuth scope → excess data access | S2, S3 | Least-privilege scopes per connector, documented in `CONNECTOR.md`; service identity constrained | NFR-1 |
| SSRF / egress to attacker host | S2 | Outbound targets restricted to configured per-connector base URLs (allow-list); no user-controlled URLs | NFR-1 |
| Double-send / double-file on retry | S2, S5 | Idempotency keys forwarded + Redis guard; fail-closed when Redis down | NFR-3* |
| Unapproved external `send` executed | S2 | `send` is `gated (human)`; this feature executes only already-approved sends and keeps them idempotent; gate enforced by orchestrator | NFR-4 |
| One connector compromise/outage cascades | S8 | Circuit/health isolation per connector; failures contained | NFR-9* |
| Raw webhook body persisted in wrong region | S7 | Raw retention is opt-in + region-configurable; default off `ASSUMPTION (confirm)` | NFR-6 |
| Tampered Redis dedup/idempotency state | S5 | Redis access controlled (network + auth); keys namespaced; fail-closed on anomaly | NFR-3*, NFR-1 |
| Privileged doc routed externally via bad ACL | S9 | `ACL.external` carried faithfully; enforcement asserted in `document-routing`/`qc-verification` (cross-spec) | NFR-1 |

\* NFR-3/NFR-9 are reliability NFRs included here because they are
security-adjacent (no-double-effect, blast-radius containment).

## AuthZ & privilege checks

- **Least-privilege OAuth scopes** per connector; the exact scope list is a
  required `CONNECTOR.md` section and reviewed per adapter
  (`ASSUMPTION (confirm): scopes per vendor`).
- **Service identity:** the agent/server calls vendors under a constrained
  service identity (PTD §12); connectors never elevate scope at runtime.
- **Webhook authZ** = signature verification (no anonymous trust); unknown
  connector path → 404 (no information leak about registered connectors beyond
  existence).
- **No tool-level privilege bypass:** this feature exposes no MCP tools; it cannot
  be invoked directly by the agent to escalate. Risk-tier enforcement (gates)
  lives above it.
- **ACL fidelity:** `ACL.external`/permission values pass through unmodified so
  downstream privilege checks can rely on them.

## Audit log coverage

- **Every state-changing outbound call** (`create_matter`, `upsert_contact`,
  `send`, `put`, `move`, `create_draft`) writes a `platform-foundation` audit
  record (`actor, action, inputs, outputs, approval?, timestamp, run_id`)
  **before** the effect is acknowledged (NFR-2).
- **Every accepted inbound event** that enqueues a trigger writes an audit/event
  record (connector, `provider_event_id` hash, type, decision).
- **Rejections** (bad signature, dedup drop, unknown type) emit a security/audit
  log line (no body) for forensics.
- Audit `inputs/outputs` are redacted/projected — **no raw PII or token** stored
  in the clear beyond policy; sensitive values referenced by hash where feasible.
- Reads are **not** audited as state changes (no side effect) but are traced.

## PII handling & residency

- **Immigration-heightened PII** (A-numbers, passports, biometrics, immigration
  status, country-of-origin) is treated as confidential at every surface
  (CONVENTIONS §7). It must **never** appear in logs, traces, metrics, or
  unredacted audit fields — enforced by the redaction scrubber and PBT
  redaction-totality property.
- **`Event.payload`** is a deliberately **minimised projection** of the vendor
  payload — only the fields the orchestrator needs to route the trigger — not the
  raw blob.
- **Raw webhook body retention** is **off by default**; if enabled it is stored
  encrypted at rest in a **region-configurable** location, referenced by
  `Event.raw_ref`, with a retention TTL (`ASSUMPTION (confirm): retention policy +
  region`). (NFR-6)
- **In transit:** TLS to all vendors and on the webhook ingress; reject plaintext.
- **Data residency:** outbound egress endpoints and ingress are
  region-configurable; no region hard-coded (NFR-6).
- **Cross-border:** sending client PII to a vendor is a residency decision per
  connector; documented in `CONNECTOR.md` and gated by deployment config
  (`ASSUMPTION (confirm): per-vendor data-region`).

## Pre-audit checklist

- [ ] Webhook HMAC verification uses constant-time compare and a replay window;
  invalid/missing signatures are rejected with no enqueue and no body logged.
- [ ] All vendor secrets/tokens load from the central encrypted store at runtime;
  none present in source, config files, logs, or test fixtures.
- [ ] Redaction scrubber active on all connector logs/traces/metrics; PBT
  redaction-totality property passes; spot-check of captured telemetry shows no
  PII/token.
- [ ] Least-privilege OAuth scopes documented per connector in `CONNECTOR.md` and
  reviewed.
- [ ] Outbound egress restricted to per-connector allow-listed base URLs
  (no SSRF).
- [ ] Idempotency guard + webhook dedup verified to prevent double-effect /
  double-enqueue; Redis-down behaviour is fail-closed for writes and webhook
  dedup.
- [ ] Every state-changing call writes an audit record before ack; audit fields
  carry no raw PII/token; chain integrity is the foundation's responsibility and
  is exercised by an integration test.
- [ ] Circuit/health isolation verified: one connector `open` does not affect
  others (NFR-9).
- [ ] Raw-body retention is off unless explicitly enabled; when enabled, region +
  TTL + encryption confirmed.
- [ ] `ACL.external`/permission values pass through unmodified (fidelity test).
- [ ] CI fails on any adapter missing a complete `CONNECTOR.md` (incl. scopes &
  data-region sections).
- [ ] All `ASSUMPTION (confirm)` security items (signature scheme, scopes,
  residency/retention, idempotency mechanism) are tracked and resolved before the
  relevant concrete adapter ships.
