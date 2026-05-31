# Spec — Platform Foundation

> Feature slug: `platform-foundation` · Root spec. Inherits
> `_shared/CONVENTIONS.md`; architecture from `concept/PTD.md` (esp. §4, §12,
> §13, §15, §16). This document is the behavioural contract ("what"), not
> line-by-line code; implementation steps live in `tasks.md`.

## 1. Summary

The foundation library (`src/cam/`) all other features import. It provides:
(a) the canonical Pydantic v2 domain model; (b) PostgreSQL persistence with
SQLAlchemy 2 + Alembic and a repository layer; (c) an append-only,
hash-chained, exportable audit log with write-before-complete semantics;
(d) 12-factor configuration, a secret-store loader, per-workflow feature flags,
and a residency setting; (e) an observability bootstrap (structlog +
OpenTelemetry + Prometheus, PII-scrubbed, `run_id`-correlated); (f) encryption
utilities (AES-256 at rest, column-level OAuth token encryption); (g) an RBAC
model and constrained agent service identity; and (h) the docs/CI discipline
(schema-derived tool docs, ADRs, schema-drift check, "done = code+tests+docs").
It defines **no** connector, workflow, orchestration engine, or concrete MCP
tool.

## 2. Scope & out-of-scope

**In scope:** items (a)–(h) above and the `src/cam/...` repo layout (PTD §15).
See `planning/requirements.md` §2 for the itemised list.

**Out of scope (explicit):**

- Connector adapters, connector ports, `ConnectorError` taxonomy, webhook
  ingestion → `connector-framework`.
- Workflow definitions, the durable state-machine orchestrator, gates,
  idempotency keys, retry/compensation, triggers → `workflow-orchestration`.
- Concrete MCP tools/resources/prompts — only the *doc-generation discipline*
  for them is here.
- Deadline engine, document generation, extraction, routing, QC, status emails,
  intake — downstream specs.
- Object-store client implementation and Redis usage — referenced by the
  encryption/residency settings but implemented where first needed downstream.
  This spec defines the at-rest encryption contract those must honour.

## 3. Domain types used / introduced

Introduces the canonical definitions of the six shared types **exactly per
PTD §4** (CONVENTIONS.md §5 — defined here once, reused everywhere):

- `Contact`, `Matter`, `Document`, `Deadline`, `Communication`, `Task`.

Field shapes follow PTD §4 verbatim (e.g. `Deadline.status` is
`Literal["pending","reminded","done","missed"]`; `Communication.direction` is
`Literal["in","out"]`; `Matter.key_dates: list[Deadline]`;
`*.external_ids: dict[str,str]`). This spec additionally introduces the small
**supporting/value types** these reference and that are genuinely shared:

- `AuditRecord` — the audit-log entry (see §8).
- `Role`, `Permission`, `Principal` — RBAC primitives (see §10).
- `ResidencyRegion` (enum), `FeatureFlag` (key + state) — config primitives.
- An `ACL` value type **placeholder** is referenced by PTD §6 but **owned by**
  `connector-framework` / `document-routing`; the foundation only reserves the
  name and does not define routing semantics.

New shared types are *proposed here and reused*, never forked per feature
(CONVENTIONS.md §5). Immigration specifics (case types, form IDs, deadline
rules) remain **config/rule data**, not core fields (CONVENTIONS.md §5, §8).

## 4. Interfaces (MCP tools/resources/prompts, ports, internal APIs)

This feature exposes **no MCP tools** (out of scope). It exposes **internal
Python interfaces** that downstream specs consume:

### 4.1 Domain package — `cam.core.domain`
- The six Pydantic models + supporting value types. Importable as the single
  source of truth. Each model carries docstrings used by the doc generator.

### 4.2 Persistence — `cam.persistence`
- SQLAlchemy 2 typed ORM models mirroring the domain types.
- `Repository[T]` protocol: `get(id)`, `list(filter)`, `add(entity)`,
  `update(entity)`, `delete(id)` returning/accepting domain models (ORM⇄domain
  mapping is internal). Concrete repos: `ContactRepository`, `MatterRepository`,
  `DocumentRepository`, `DeadlineRepository`, `CommunicationRepository`,
  `TaskRepository`, `AuditRepository` (append-only; see §8).
- A `UnitOfWork`/session-scope context manager providing transactional grouping
  so an action + its audit write commit atomically (supports
  write-before-complete; see §5.2).
- Alembic migration env wired to the ORM metadata.

### 4.3 Audit — `cam.core.audit`
- `AuditService.record(actor, action, inputs, outputs, approval, run_id) ->
  AuditRecord` — computes the chain hash, appends, returns the stored record.
- `AuditService.verify(range?) -> VerifyResult` — recomputes and validates the
  chain; reports the first broken link if any.
- `AuditService.export(range?, format="jsonl") -> bytes|path` — portable export
  whose chain re-verifies.

### 4.4 Config & secrets — `cam.config`
- `Settings` (Pydantic-Settings) — typed 12-factor settings; validates on load;
  fails fast with precise, secret-free errors.
- `SecretLoader` protocol with backends `EnvSecretLoader` (v1 default),
  `VaultSecretLoader`, `KMSSecretLoader` (interface-compatible; latter two are
  stubs to be completed when hosting is confirmed). `get_secret(name)` returns
  the value; values are never logged.
- `FeatureFlags.is_enabled(workflow_key)` — per-workflow flags.
- `settings.residency_region: ResidencyRegion` — configurable region.

### 4.5 Observability — `cam.obs`
- `configure_observability(settings)` — one call wiring structlog,
  OpenTelemetry, and Prometheus with the PII scrubber installed.
- `bind_run_id(run_id)` / context-local correlation helpers.
- `pii_scrub(value)` / a structlog processor + OTel span processor + a metric
  label sanitiser sharing one redaction ruleset.

### 4.6 Security — `cam.security`
- `crypto`: `encrypt(plaintext, key) -> bytes`, `decrypt(ciphertext, key) ->
  bytes` (AES-256-GCM, envelope-wrapped DEK); `EncryptedStr` SQLAlchemy type
  for column-level OAuth-token encryption.
- `rbac`: `Role`, `Permission`, `Principal`, and
  `authorize(principal, permission, resource?) -> bool` evaluated against a
  declarative permission matrix.
- `identity`: `AGENT_SERVICE_PRINCIPAL` — the constrained service identity the
  agent runs under, with an explicitly enumerated (and minimal) grant set.

### 4.7 Docs/CI — `cam.docs` + CI
- A generator that renders tool/domain documentation from Pydantic schemas +
  docstrings (the *discipline*; concrete tools are downstream).
- A schema-drift checker invoked in CI: regenerates docs and fails if they
  differ from the checked-in versions or if a schema lacks a description.
- ADR directory convention at `docs/adr/`.

## 5. Behaviour & flows (happy path + state transitions)

### 5.1 Startup / bootstrap
1. `Settings` loads and validates the environment (12-factor). Missing/invalid
   required values → fail fast, process does not start, error names the setting
   but never prints secret values.
2. `SecretLoader` is selected by config and connectivity verified lazily.
3. `configure_observability(settings)` installs logging/tracing/metrics with the
   PII scrubber. From here every log carries `run_id` when one is bound.
4. DB engine + session factory created; Alembic verifies migrations are at head
   (or the process refuses to serve, per ops policy — ASSUMPTION (confirm)).

### 5.2 Write-before-complete (audit invariant)
For any state-changing action a downstream feature performs:
1. The action and its `AuditService.record(...)` write happen in the **same
   transaction / unit of work**.
2. The action is only "complete" once the audit record is durably committed
   (NFR-2). If the audit write fails, the action rolls back.
3. The audit hash is computed as `H(prev_hash || canonical(record_without_hash))`
   and stored on the row; the row's id is monotonic/ordered to define chain
   order.

### 5.3 Audit verification & export
- `verify()` walks records in order, recomputing each hash from the prior
  stored hash + record content; returns `ok=True` or the index/id of the first
  mismatch. Used by tests, ops, and pre-audit checks.
- `export()` emits records (and their stored hashes) in order; re-importing or
  re-hashing the export reproduces a valid chain.

### 5.4 Encryption round-trip (OAuth tokens & at-rest)
- A token written through `EncryptedStr` is encrypted with the active DEK before
  `INSERT`/`UPDATE` and decrypted on load. Key rotation re-wraps the DEK via the
  KEK without rewriting historical ciphertext eagerly (envelope encryption).

### 5.5 RBAC evaluation
- `authorize(principal, permission, resource?)` returns `True` **only** if the
  permission is explicitly granted to one of the principal's roles (default
  deny). The `agent_service` principal's grants are a fixed minimal subset; any
  permission outside it is denied.

### 5.6 Feature-flag gating (rollout)
- `FeatureFlags.is_enabled("intake")` etc. lets ops enable workflows wave-by-wave
  without redeploy; default state is **off** for unreleased workflows
  (ASSUMPTION (confirm)).

## 6. Edge cases & error handling

- **Audit chain at genesis:** first record uses a fixed zero/`null` previous-hash
  sentinel; `verify()` treats it specially.
- **Audit write failure:** surfaces as a transaction abort; the wrapping action
  must roll back — never "succeed silently without an audit record" (NFR-2,
  PTD §12).
- **Mutation/deletion attempt on audit table:** rejected at DB level
  (revoked `UPDATE`/`DELETE`, or trigger that raises). The rejection is itself
  log-worthy (without leaking content).
- **Decrypt with wrong/rotated key:** GCM authentication fails → typed
  `DecryptionError`; never returns garbage plaintext.
- **Missing secret:** `get_secret` raises a typed `SecretNotFound` naming the
  key (not the value); startup-critical secrets fail fast.
- **Malformed config:** Pydantic-Settings raises a validation error listing the
  offending fields; the message is scrubbed of any value that came from a secret
  source.
- **PII in logs/traces/metrics:** the shared scrubber redacts before emission;
  a metric label that would carry high-cardinality PII is dropped/bucketed.
- **Serialization of optional/`None` fields:** round-trip must preserve `None`
  vs absent distinctions per Pydantic v2 config (documented model config).
- **Connector errors:** **not handled here** — the `ConnectorError` taxonomy is
  owned by `connector-framework`. This spec only ensures audit/observability
  hooks exist for callers to use.
- **Clock/timestamp:** audit timestamps are UTC, server-sourced; ordering relies
  on the monotonic row sequence, not wall-clock, to avoid skew ambiguity.

## 7. Risk tiers & gates for each action

This feature introduces **no externally-facing actions**; its operations are
internal library calls. Risk-tier classification (CONVENTIONS.md §6):

| Operation | Tier | Notes |
|---|---|---|
| Read domain entity via repository | `read` | No side effects. |
| Write domain entity via repository | `write (confirm)` | Always paired with an audit record; gate (if any) is enforced by the *calling* workflow, not here. |
| Append audit record | internal, non-gated | Mandatory side effect of any write; never user-initiated alone. |
| Encrypt/decrypt, RBAC check, config/secret read | `read`-equivalent | Pure/internal; no external effect. |
| Export audit log | `read` | Export is a read; access to it is RBAC-scoped (ops/compliance roles). |

No `gated (human)` actions are defined here — those arrive with
external/irreversible operations in downstream specs. The **gate mechanism
abstraction itself** lives in `workflow-orchestration`; this spec only provides
the audit `approval` field that records a gate outcome.

## 8. Data & persistence

- **Engine:** PostgreSQL; SQLAlchemy 2 typed ORM; Alembic migrations under
  `src/cam/persistence/` (PTD §15). One migration head; reversible migrations.
- **Domain tables:** `contacts`, `matters`, `documents`, `deadlines`,
  `communications`, `tasks` mirroring PTD §4 fields, with `external_ids` as
  JSONB and appropriate FKs (`document.matter_id`, `deadline.matter_id`, etc.).
- **Audit table `audit_log`** (append-only):
  `id` (monotonic PK / sequence), `actor`, `action`, `inputs` (JSONB, scrubbed),
  `outputs` (JSONB, scrubbed), `approval` (JSONB/nullable — records gate
  outcome), `timestamp` (UTC), `run_id`, `prev_hash`, `record_hash`.
  `UPDATE`/`DELETE` privileges revoked for the application role; integrity
  enforced by trigger as defence-in-depth.
- **Encryption at rest:** AES-256 for sensitive columns (OAuth tokens via
  `EncryptedStr`); full-disk / tablespace encryption assumed at deploy for the
  rest (NFR-1). `inputs`/`outputs` in the audit log are PII-scrubbed before
  storage (no raw client content; PTD §12).
- **Residency:** `settings.residency_region` is recorded/available so downstream
  storage picks compliant regions; the foundation does not itself move data
  cross-region.
- **Repository pattern:** domain ⇄ ORM mapping is internal; callers only see
  domain models, preserving the serialise/deserialise round-trip guarantee.

## 9. Observability (logs/metrics/traces for this feature)

- **Logs (structlog):** JSON, `run_id`-correlated, PII-scrubbed. Foundation-level
  events: config loaded, migrations at head, secret-loader backend selected
  (name only), audit append, audit verify result.
- **Traces (OpenTelemetry):** spans around DB transactions, audit append/verify,
  encrypt/decrypt, secret fetch (attributes scrubbed; never the secret).
- **Metrics (Prometheus):** `cam_audit_records_total`,
  `cam_audit_verify_failures_total`, `cam_db_tx_duration_seconds`,
  `cam_secret_fetch_total{backend}`, `cam_config_load_failures_total`,
  `cam_pii_redactions_total`. Labels are low-cardinality and PII-free.
- The PII scrubber is shared across all three signals (single ruleset) so a
  field redacted in logs is redacted in traces and metric labels too.

## 10. Security & privilege considerations

- **Encryption:** AES-256-GCM application-layer column encryption with envelope
  (DEK wrapped by KEK from secret store); TLS in transit assumed at deploy
  (NFR-1, PTD §12).
- **Secrets:** central loader; never in source, never logged; rotation-friendly;
  no env-dumping (NFR-8, CONVENTIONS.md §7).
- **RBAC:** default-deny matrix; roles per §8 of requirements; the agent runs as
  a **constrained service identity** with an explicit minimal grant set
  (PTD §12). Matter/tool scoping primitives provided for downstream enforcement.
- **Audit immutability:** append-only + hash chain + revoked mutation privileges
  → tamper-evident (NFR-2).
- **PII / privilege:** immigration PII (A-number, passport/visa, SSN/ITIN, DOB,
  biometrics, status, country-of-origin) is in the redaction ruleset; no client
  content in telemetry; privilege-aware routing itself is downstream but the
  `Document.privileged` flag and redaction substrate originate here.
- **Residency:** configurable region; GDPR-aware handling and SOC 2 control
  surfaces supported (NFR-6).

## 11. Dependencies & integration points

- **Upstream feature deps:** none (root spec).
- **Consumed by:** every other spec. Key integration points they rely on:
  - `connector-framework` uses `EncryptedStr` for OAuth tokens, the audit
    service for write actions, and the domain models for mapping.
  - `workflow-orchestration` uses the audit `approval` field, `run_id`
    correlation, repositories, and feature flags.
  - All workflows import `cam.core.domain` and emit audit records via the
    write-before-complete contract.
- **External:** PostgreSQL, secret-store backend, OTel collector / Prometheus
  scrape target at deploy (configurable, host-agnostic — CONVENTIONS.md §4).

## 12. Test strategy (focused tests + pointer to PBT)

- **Focused tests (pytest):** migration up/down on a throwaway DB; repository
  CRUD round-trips; audit append + verify happy path; rejected audit mutation;
  config load success + a few precise validation failures; secret-not-found;
  redaction over crafted secret/PII inputs; RBAC allow/deny examples; schema-drift
  CI check pass+fail cases.
- **Property-based tests (Hypothesis):** see `pbt-properties.md` — audit
  hash-chain integrity & tamper-detection, encryption round-trip, RBAC
  soundness, config-validation totality, and domain-model serialise/deserialise
  round-trip.
- **Done = code + tests + docs** (CONVENTIONS.md §3.6, PTD §16): each task group
  ships focused tests, and foundation-level docs (this spec, ADRs, generated
  schema docs) stay in sync via the schema-drift check.

## 13. Open questions

1. Final secret-store backend (env vs Vault vs cloud KMS) — tied to hosting
   (PRD §11 Q3, PTD §18 Q2). Loader interface is stable regardless.
2. Whether audit lives in a separate DB/schema for stronger separation of duties
   (deferrable behind the repository abstraction).
3. Exact RBAC permission matrix and the agent service identity's minimal grant
   set — needs firm sign-off before any tool enforces it.
4. Canonical-serialisation choice for hashing (e.g. sorted-key JSON vs a
   length-prefixed encoding) — must be fixed and version-tagged before go-live.
5. Residency granularity required (country vs region vs single-tenant) and
   whether LLM inference egress must be constrained by the same setting
   (PTD §18 Q4).
6. Startup policy when migrations are not at head (refuse-to-serve vs warn).
