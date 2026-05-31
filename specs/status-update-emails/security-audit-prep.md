# Security Audit Prep — Status Update Emails

> Feature slug: `status-update-emails` · Traces: FR-10, FR-6; NFR-1, NFR-2, NFR-4,
> NFR-6, NFR-8. Re-checks this feature's surfaces against the CONVENTIONS §7
> baseline (the floor, not the ceiling). Compliance posture: SOC 2 path +
> GDPR-aware + configurable residency + attorney–client privilege preserved
> (CONVENTIONS §4).

## Sensitive surfaces (data, actions, external calls)

| Surface | Why sensitive | Classification |
|---|---|---|
| **Email body/subject** (status update content) | Contains immigration PII (case status, A-numbers, country-of-origin, status/EAD dates) addressed to a specific client | Confidential / privileged-adjacent |
| **Recipient set** | Mis-resolution leaks one client's update to another | Confidential — highest blast radius |
| **`MatterStatusDelta` / matter status** | Reveals case posture (e.g. RFE, denial, removal) | Confidential |
| **`email.send` action** | External, irreversible disclosure | `gated (human)` |
| **`email.draft` action** | Creates content (reversible, internal) | `write (confirm)` |
| **Approval decision** | Authorises external disclosure | Audited, RBAC-gated |
| **Webhook ingress** | Untrusted inbound; could inject/replay status events | Verify + dedupe |
| **Scheduled sweep** | Reads matters broadly via Case port | Least-privilege read |
| **LLM draft call** | Sends matter context to an inference provider | Provider-agnostic; residency-configurable |
| **Audit records** | Contain actor/inputs/outputs of disclosures | Append-only, hash-chained |

## Threats & mitigations (map to NFR-1, 2, 4, 6, 8)

| Threat | Mitigation | NFR |
|---|---|---|
| **Cross-client leak** — update sent to the wrong client | Recipients resolved **only** from matter participants; recipient-integrity QC (`qc-verification`) re-verifies recipient ∈ participants **before** the gate; `fail` blocks send (property P2) | NFR-1 |
| **Unauthorised / auto send** — send without human sign-off | `email.send` is `gated (human)`, default-on; reachable only post-approval; agent runs under constrained service identity and cannot self-approve (property P3) | NFR-4 |
| **Duplicate disclosure** — same change emailed twice | `change_id`-keyed idempotency + `idem_key` forwarded to Email adapter; `delivered` flag set post-audit; replay/retry are no-ops (property P1) | NFR-3, NFR-2 |
| **Forged / replayed webhook** triggers spurious update | CF webhook ingest verifies HMAC/secret, normalises, and dedupes provider event id before a run starts | NFR-1 |
| **PII leakage into logs/telemetry** | No subject/body in logs/metrics/traces; only ids/statuses/counts; structlog PII-scrub; OTel spans carry no client content | NFR-1, NFR-6 |
| **PII to inference provider / wrong region** | Provider-agnostic LLM client; residency a configurable deployment decision; minimise context passed to the `status_update_email` prompt | NFR-6 |
| **Privilege break** — privileged internal note sent externally | Feature sends client-facing content only; privilege-aware checks (QC / `document-routing`) prevent privileged material in outbound mail | NFR-1 |
| **Tampered audit trail** | Append-only, hash-chained audit; record written **before** step completion; exportable for review | NFR-2 |
| **Credential exposure** | Email creds live in the connector secret store (Vault/KMS), never surfaced to this workflow, never logged; no env-dumping | NFR-8 |
| **Privilege escalation at the gate** | Only RBAC-permitted approver roles can resolve the gate; approval records actor identity | NFR-4 |

## AuthZ & privilege checks

- **RBAC on approval:** only roles authorised to release client communications may
  approve the gate; enforced by `workflow-orchestration` gate + `platform-foundation`
  RBAC. The AI agent identity is **not** in the approver role set.
- **Least privilege:** the Case-port read used by the sweep holds read-only scope;
  the Email port holds draft+send scope only; no broader access requested.
- **Recipient authorisation:** recipient set is a closed derivation from matter
  participants; no free-text recipient entry path exists in this feature.
- **Privilege preservation:** outbound content is client-facing by definition;
  privileged documents are never attached/routed by this workflow (deferred to
  privilege-aware checks in `qc-verification` / `document-routing`).

## Audit log coverage

Append-only, hash-chained records (CONVENTIONS §7), each written before the step
is considered complete:

- `trigger_received` (source = webhook|sweep|manual) and `skipped` (reason).
- `draft_created` — `draft_comm_id`, prompt version.
- `qc_result` — recipient-integrity `pass|warn|fail` + reason.
- `approval_decision` — actor, decision (approved/rejected/edited), timestamp,
  channel (MCP/UI/email).
- `email_sent` — `message_id`, `idem_key`, recipients (ids, not content).
- `blocked` terminal reason (QC fail / missing recipient / ConnectorError).
- `delivered` flag for `change_id` set **after** `email_sent` (ordering asserted
  by property P6). Export shows draft → QC → approval → send order.

## PII handling & residency

- **Data minimisation:** only the delta context the draft needs is passed to the
  prompt/LLM; A-numbers and similar identifiers are excluded from logs/metrics/traces
  entirely.
- **At rest:** email bodies/drafts and run state encrypted at rest (AES-256);
  OAuth tokens (in the connector layer) column-encrypted.
- **In transit:** TLS for all port/LLM/webhook calls.
- **Residency:** draft storage, run state, and inference region honour the
  configured residency setting (NFR-6); `ASSUMPTION (confirm)` the firm's region.
- **GDPR-aware:** sent communications are linked to the matter for data-subject
  access/erasure handling via `platform-foundation`; no separate shadow copy is
  retained by this feature.
- **Retention:** drafts that are never approved follow the platform retention
  policy; `ASSUMPTION (confirm)` the retention window for un-sent drafts.

## Pre-audit checklist

- [ ] Recipient set is provably a subset of matter participants for every send
      (property P2 green).
- [ ] No `email.send` path exists that bypasses the approval gate (property P3
      green); agent identity excluded from approver roles.
- [ ] One-send-per-`change_id` holds under duplicate/replay/crash (property P1
      green); `idem_key` forwarded to the Email adapter.
- [ ] Draft strictly precedes send in every sending run (property P4 green).
- [ ] Skipped (non-allow-listed) and `blocked` (QC fail / missing recipient) runs
      never send (property P5 green).
- [ ] Audit records present, ordered, hash-chained, and written before step
      completion; `delivered` set only post-send-audit (property P6 green).
- [ ] No subject/body/PII appears in any log, metric, or trace (scrub assertion
      green).
- [ ] LLM inference provider and region match the configured residency posture.
- [ ] Email credentials sourced from the secret store only; absent from code,
      logs, and config dumps.
- [ ] Webhook ingress signature-verified and deduped before any run starts.
- [ ] All `ASSUMPTION (confirm)` items (trigger allow-list, recipient role policy,
      revert suppression, retention window, residency region) resolved or
      explicitly accepted before go-live.
