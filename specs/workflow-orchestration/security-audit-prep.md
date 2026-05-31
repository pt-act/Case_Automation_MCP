# Security Audit Prep — Workflow Orchestration

> Phase 4 security review checklist (process: `specs/_shared/CONVENTIONS.md`).
> Re-checks this feature's surfaces against the inherited security/compliance
> floor (CONVENTIONS §7; PTD §12). This section is the floor, not the ceiling.

## Sensitive surfaces (data, actions, external calls)

| # | Surface | Why sensitive |
|---|---|---|
| S1 | **Approval tokens** (issue/verify/consume) | Unforgeable authority to resume a `gated (human)` action (e.g. external send/file). Forgery or reuse = unauthorised irreversible action. |
| S2 | **Gate resolution** (`resolve_gate`, all 3 channels) | The single control point deciding whether a post-gate action runs. AuthZ + audit must be airtight. |
| S3 | **`workflow.run`** | Can start runs that contain gated steps; must not bypass gates or run under elevated identity. |
| S4 | **`workflow.status`** | Returns step inputs/outputs that may hold immigration PII (A-numbers, passports, status, country-of-origin) — must be RBAC-scoped + redacted. |
| S5 | **Step inputs/outputs at rest** (JSONB) | Persisted context/outputs may carry privileged or PII content; encryption + redaction required. |
| S6 | **Trigger intake** (webhook `Event`, schedule, agent) | A spoofed/duplicated trigger could start unintended runs or replay effects. |
| S7 | **Token-signing key** (secret store) | Compromise allows forging any approval. Rotation + no-logging required. |
| S8 | **Resume / recovery path** | Must not let a crash+resume escalate privilege or skip a gate. |
| S9 | **Email-action links** | Tokens travel in email; link interception or forwarding is a realistic threat. |
| S10 | **Audit log writes** | Must be append-only/hash-chained and precede acknowledgement; a gap hides unauthorised actions. |

## Threats & mitigations (map to NFR-1, 2, 4, 6, 8)

| Threat | NFR | Mitigation |
|---|---|---|
| **Forged approval token** authorises a gated action | NFR-4, NFR-2 | HMAC-SHA256 sign + key id; constant-time verify; token bound to `(gate_request_id, run_id, step)`; store only the hash. |
| **Token replay / reuse** resolves a gate twice | NFR-4, NFR-3 | Single-use: atomic `mark used` (DB unique + `used_at`); second use → `used`/409, audited. |
| **Expired token** used to resume after the decision window | NFR-4 | `expires_at` enforced; expired → rejected, run stays parked; re-issue required. |
| **Unauthorised approver** (wrong role / privilege escalation) | NFR-4, NFR-1 | `required_role` checked against RBAC (`platform-foundation`); 403 + audit on mismatch; agent's MCP-callback identity cannot approve gates it isn't authorised for. |
| **Gate bypass** — post-gate action runs without approval | NFR-4 | Engine refuses to execute a `gated (human)` post-gate step without a recorded `ApprovalDecision(approve)`; covered by PBT P3. |
| **Privilege escalation via resume** | NFR-1, NFR-4 | Resumed run executes under the **original run service identity**, never the approver's; recovery never widens scope; verified by test. |
| **Spoofed / replayed trigger** starts or duplicates runs | NFR-3, NFR-2 | Edge signature verification is upstream (`connector-framework`); engine adds idempotent start via `dedupe_key` (one run per event). |
| **Duplicate external effect** under retry/crash | NFR-3 | Idempotency key `(run_id, step)` + connector idem-key forwarding; exactly-once effect (PBT P1, P4). |
| **PII leakage** into logs/traces/status output | NFR-1, NFR-6 | PII redaction in structlog/OTel; `workflow.status` RBAC-scoped + redacted; no client content in telemetry. |
| **Data-at-rest exposure** of step payloads | NFR-1, NFR-6 | AES-256 at rest; column-level handling for sensitive fields; configurable residency for the run store. |
| **Signing-key leakage** | NFR-8 | Key from central secret store; never in source; never logged; rotation via key id without invalidating in-flight tokens. |
| **Email link interception/forwarding** | NFR-4, NFR-1 | Short TTL; single-use; `ASSUMPTION (confirm)` whether the link also requires an authenticated session before the decision is accepted. |
| **Audit tampering / missing record** | NFR-2 | Hash-chained append-only log; audit written *before* the action is acknowledged; export for review. |
| **Residency violation** (run data in wrong region) | NFR-6 | Run/step store deployed in the configured region; no cross-region copy of step payloads. |

## AuthZ & privilege checks

- **Who may start a run:** `workflow.run` callers are authenticated; the agent
  runs under a constrained service identity (PTD §12). Starting a run never
  elevates privilege and never bypasses an inner gate.
- **Who may approve a gate:** only an actor holding the gate's configured
  `required_role` (RBAC). Default if unconfigured: a designated approver role,
  **never** "any authenticated user" (ASSUMPTION confirm, requirements §8).
- **N-of-M quorum:** when configured, all required approvals must be recorded
  before resume; v1 default 1-of-1.
- **Channel parity:** all three channels (MCP / web / email) funnel through one
  `resolve_gate` authority, so authz is enforced identically regardless of
  channel — no weaker path.
- **Run execution identity:** steps execute under the run's original service
  identity with least-privilege connector scopes; the approver's identity
  authorises the *gate*, not the *action's privileges*.
- **`workflow.status` visibility:** scoped so a viewer sees only runs/fields
  their role permits; PII redacted for unprivileged viewers.

## Audit log coverage

Every one of these writes a hash-chained audit record (`actor, action, inputs,
outputs, approval, timestamp, run_id`) **before** the change is acknowledged:

- `run.created / started / succeeded / parked / rejected / cancelled`
- `step.started / succeeded / failed / retried / compensated`
- `gate.requested / approved / rejected / expired`
- `token.issued / token.used / token.rejected` (with reason: expired/tampered/
  reused/unauthorised)
- `transition.rejected` (illegal-transition attempts)
- `trigger.received` (event/schedule/agent, with dedupe outcome)

Each approval/rejection record includes the **channel** used. No client content
or token plaintext is written to the audit log — only identifiers, hashes, and
decision metadata.

## PII handling & residency

- **PII in scope:** immigration-specific identifiers may appear in step
  inputs/outputs and run context — A-numbers, passport/visa numbers, biometrics
  references, immigration status, country-of-origin, dates of birth.
- **At rest:** AES-256; sensitive run/step payload fields handled per the
  platform encryption baseline; OAuth/token material column-encrypted.
- **In transit:** TLS everywhere, including the web-UI and email-action endpoints.
- **In logs/telemetry:** redacted; correlation by `run_id` only; never the
  payload. No env-dumping commands; secrets never logged.
- **Residency:** the run/step/audit store and any payload copies live in the
  configured region; no cross-region replication of step payloads (NFR-6,
  GDPR-aware, configurable per CONVENTIONS §4).
- **Privilege:** attorney–client privileged content in step payloads is treated
  as confidential; `workflow.status` will not surface privileged fields to roles
  lacking access; routing of privileged content is governed downstream by
  `qc-verification` / `document-routing`, not weakened here.

## Pre-audit checklist

- [ ] Token signing uses a secret-store key (key id embedded); rotation tested;
      key never appears in source, logs, or telemetry.
- [ ] Token verification is constant-time; tampered/expired/reused tokens are
      rejected and audited (PBT P6 + focused tests).
- [ ] No `gated (human)` post-gate action can run without a recorded
      `ApprovalDecision(approve)` (PBT P3).
- [ ] Approver authz enforced identically across MCP / web / email channels;
      unauthorised approvals 403 + audited.
- [ ] Resume/recovery never escalates privilege and never skips a gate; run
      executes under the original service identity.
- [ ] Idempotent start + step idempotency verified — no duplicate effects under
      retry/crash/duplicate trigger (PBT P1, P4, P7).
- [ ] Only legal state transitions persisted; illegal attempts rejected +
      audited (PBT P5).
- [ ] `workflow.status` output RBAC-scoped and PII-redacted for the viewer role.
- [ ] Step payloads encrypted at rest; no PII/secret/token plaintext in logs,
      traces, or the audit log.
- [ ] Run/step/audit store deployed in the configured residency region.
- [ ] Audit record precedes acknowledgement for every state-changing action;
      hash-chain integrity verifiable on export.
- [ ] All `ASSUMPTION (confirm)` items in `requirements.md` §8 resolved or still
      explicitly flagged before go-live (TTL, retry cap, approver-role default,
      quorum, signing algorithm, email-link auth model).
