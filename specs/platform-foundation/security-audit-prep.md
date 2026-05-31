# Security Audit Prep — Platform Foundation

> Feature slug: `platform-foundation`. Re-checks this feature's surfaces against
> the inherited baseline in `_shared/CONVENTIONS.md §7` (that section is the
> floor, not the ceiling) and PTD §12. NFR references map to PRD §6. Domain:
> US immigration law firm — heightened PII and attorney–client privilege.

## Sensitive surfaces (data, actions, external calls)

| Surface | Sensitivity | Where it lives |
|---|---|---|
| **OAuth refresh/access tokens** | Critical (system access) | Column-encrypted (`EncryptedStr`) in Postgres; keys from secret store. |
| **Secret store contents** (DB creds, KEK, provider keys) | Critical | Loaded at runtime via `SecretLoader`; never in source, never logged. |
| **Audit log** (`actor, action, inputs, outputs, approval, ts, run_id`) | High (integrity-critical, may reference client matters) | Append-only, hash-chained `audit_log` table; `inputs`/`outputs` PII-scrubbed before storage. |
| **Domain data at rest** (`Matter`, `Contact`, `Document`, etc.) | High (immigration PII, privileged) | Postgres, AES-256 at rest; privilege flag on `Document`. |
| **Encryption keys (DEK/KEK)** | Critical | Envelope encryption; KEK in secret store; DEK wrapped. |
| **Config / environment** | Medium–High (may carry secret-sourced values) | `Settings`; validation errors scrubbed of secret values. |
| **Telemetry** (logs/traces/metrics) | Medium (PII-leak risk) | structlog/OTel/Prometheus with shared PII scrubber. |
| **RBAC matrix & agent service identity** | High (privilege escalation risk) | Declarative matrix; default-deny; minimal agent grant set. |
| **Audit export artefact** | High (bulk sensitive data egress) | RBAC-scoped export; re-verifiable chain. |
| **External calls** | n/a here | **None** — connectors/LLM/object-store are downstream specs. Residency setting only *informs* their egress. |

## Threats & mitigations (map to NFR-1, 2, 4, 6, 8)

| Threat | Mitigation | NFR |
|---|---|---|
| Audit tampering / silent deletion to hide an action | Append-only table (revoked `UPDATE`/`DELETE` for app role) + hash chain + integrity trigger; `verify()` detects mutation/deletion/reorder at first affected link; write-before-complete so no action completes unaudited | NFR-2 |
| Action completes without an audit record | Audit write and action share one unit of work; audit failure rolls back the action (spec §5.2) | NFR-2 |
| Credential/token theft from DB | Column-level AES-256-GCM encryption of OAuth tokens; envelope keys in secret store; full at-rest encryption assumed at deploy | NFR-1 |
| Secret leakage via logs/errors/env dumps | Secrets never logged; validation/error messages scrubbed; no env-dumping commands; rotation-friendly loader | NFR-8 |
| PII leakage into telemetry (incl. A-number, passport, biometrics) | Shared PII scrubber across logs/traces/metrics; no client content in telemetry; high-cardinality PII never a metric label | NFR-1 |
| Privilege escalation by the agent or a role | Default-deny RBAC matrix; agent runs under constrained service identity with an enumerated minimal grant set; PBT proves no unauthorised grant | NFR-1 |
| Cross-region data handling breaching residency/GDPR | Configurable `residency_region` surfaced to downstream storage/telemetry; foundation does not move data cross-region; GDPR-aware handling supported | NFR-6 |
| Weak human-control posture for irreversible actions | This feature defines no external action; it provides the audit `approval` field so downstream gates (default-on, NFR-4) are recorded immutably | NFR-4 |
| Tampering with config to weaken controls | Total config validation, fail-fast; feature flags default-off for unreleased workflows; significant changes recorded as ADRs | NFR-6 / NFR-8 |

## AuthZ & privilege checks

- **Model:** RBAC, default-deny. Roles (ASSUMPTION (confirm)):
  `paralegal`, `attorney`, `intake_coordinator`, `ops_admin`, `agent_service`.
- **Agent identity:** `AGENT_SERVICE_PRINCIPAL` holds a small, explicit grant
  set; any permission outside it is denied (PBT: RBAC soundness +
  agent-constrained).
- **Scoping primitives:** matter/tool-scoped permission checks provided for
  downstream enforcement; the foundation supplies `authorize(...)`, not the tool
  wiring.
- **Sensitive operations gated by role:** audit **export** and (where exposed)
  config/secret administration are restricted to `ops_admin`/compliance roles
  (ASSUMPTION (confirm) exact matrix).
- **Privilege protection:** `Document.privileged` originates here; privilege-
  aware *routing* is downstream but the flag + redaction substrate it relies on
  are foundational.

## Audit log coverage

- **What is recorded:** every state-changing action across the system records
  `actor, action, inputs, outputs, approval, timestamp, run_id` (PTD §12). The
  foundation guarantees the *mechanism* and the write-before-complete contract;
  each downstream feature is responsible for calling it on its writes.
- **Integrity:** hash chain (`record_hash = H(prev_hash || canonical(record))`),
  genesis sentinel, monotonic ordering; `verify()` covers mutate/delete/reorder/
  insert (PBT). DB-level immutability as defence-in-depth.
- **Confidentiality:** `inputs`/`outputs` PII-scrubbed before storage — no raw
  client content or secrets land in the audit table.
- **Exportability:** RBAC-scoped export to a portable format (JSON Lines) whose
  chain re-verifies — supports SOC 2 evidence and review (PRD FR-15).
- **Coverage check for audit:** a CI/test asserts that repository write paths are
  reachable only through the audited unit-of-work (no "back-door" write that
  skips audit).

## PII handling & residency

- **Immigration PII in scope of redaction:** A-number, passport/visa numbers,
  SSN/ITIN, date-of-birth, biometric references, immigration status,
  country-of-origin, plus generic emails/phones (configurable list,
  ASSUMPTION (confirm) the exact patterns with the firm).
- **Where redaction applies:** logs, traces, metric labels, and audit
  `inputs`/`outputs` — one shared ruleset so coverage cannot diverge.
- **No client content in telemetry** (PTD §12, NFR-1); privilege never broken by
  the foundation.
- **Residency:** `settings.residency_region` (default US, configurable) is read
  by downstream storage/telemetry to keep data in-region; GDPR data-subject
  handling supported by the data model + audit/export. The foundation itself
  performs no cross-region transfer.
- **At rest:** AES-256 (DB + object store at deploy); OAuth tokens column-
  encrypted; envelope key management for rotation.

## Pre-audit checklist

- [ ] Hash-chain `verify()` passes on production-sized data; tamper tests
  (mutate/delete/reorder/insert) all detected at the first affected link.
- [ ] `UPDATE`/`DELETE` on `audit_log` rejected at the DB level for the app
  role; attempt is observable.
- [ ] No state-changing path can commit without a paired audit record
  (write-before-complete enforced and tested).
- [ ] OAuth tokens confirmed ciphertext at rest; `decrypt(encrypt(x,k),k)==x`
  property green; wrong-key decryption fails closed.
- [ ] Key management: KEK in secret store, DEK wrapped; rotation re-wraps
  without exposing plaintext keys; old ciphertext still decrypts post-rotation.
- [ ] Secret scan: no secret values in source, logs, traces, metric labels, or
  error messages (redaction property green); no env-dumping in code or CI.
- [ ] RBAC: default-deny verified; agent service identity proven constrained to
  its minimal grant set; export/admin ops restricted to ops/compliance roles.
- [ ] PII redaction covers the confirmed immigration pattern set across all
  three telemetry signals and audit `inputs`/`outputs`.
- [ ] `residency_region` set per environment; downstream storage/telemetry
  documented to honour it; cross-region transfer not performed by the
  foundation.
- [ ] Config validation is total and fails fast; feature flags default-off for
  unreleased workflows.
- [ ] Schema-drift CI green; ADRs recorded for encryption scheme, hash
  canonicalisation, and RBAC matrix; "done = code + tests + docs" satisfied.
- [ ] Open items confirmed or explicitly deferred: secret-store backend, audit
  DB separation, RBAC matrix, canonical-serialisation choice, residency
  granularity, migration-not-at-head startup policy (spec §13).
