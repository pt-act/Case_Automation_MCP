# Requirements — Platform Foundation

> Feature slug: `platform-foundation` · Root spec (no dependencies).
> Inherits `_shared/CONVENTIONS.md`; builds on `concept/PRD.md` and
> `concept/PTD.md`. Architecture authority: PTD. Spec-process authority:
> CONVENTIONS.md.

## 1. Context & problem

Every other feature spec in this catalogue (`connector-framework`,
`workflow-orchestration`, the seven workflows) needs the same bedrock:
one normalised domain model, durable persistence, a tamper-evident audit
trail, configuration & secret loading, observability, and a security
baseline. Without a single shared foundation, each feature would re-invent
these primitives, drift apart, and break the confidentiality, auditability,
and documentation guarantees the firm requires (PRD §6, PTD §12).

`platform-foundation` is that bedrock. It owns the canonical Pydantic v2
domain model (PTD §4), the PostgreSQL + SQLAlchemy 2 + Alembic persistence
layer and repository pattern, the append-only hash-chained audit log
(PTD §12), the 12-factor config + secret loader + per-workflow feature flags,
the observability bootstrap (structlog + OpenTelemetry + Prometheus with PII
scrubbing), the encryption / RBAC / service-identity security baseline, and
the documentation + CI discipline that makes "done = code + tests + docs"
enforceable. It implements no connector, no workflow, and no concrete MCP
tool — only the shared substrate those build on.

This is the **Phase 0 — Foundations** work in PRD §12 / PTD §17, and the
client (a US-based immigration law firm) carries heightened confidentiality
obligations: attorney–client privilege end-to-end, immigration PII
(A-numbers, passports, biometrics, status, country-of-origin), SOC 2 path,
GDPR-aware handling, and configurable data residency (CONVENTIONS.md §4, §7).

## 2. In scope

- **Domain model package** — the canonical Pydantic v2 `Contact`, `Matter`,
  `Document`, `Deadline`, `Communication`, `Task` exactly per PTD §4, plus the
  shared supporting enums/value types they reference. Single definition all
  features reuse (CONVENTIONS.md §5).
- **Persistence + migrations** — PostgreSQL via SQLAlchemy 2 (typed,
  `Mapped[...]`), Alembic migrations, the repository pattern, and the
  `src/cam/persistence/` + `src/cam/core/domain/` layout from PTD §15.
- **Audit log service** — append-only, hash-chained Postgres table
  (`actor, action, inputs, outputs, approval, timestamp, run_id`),
  write-before-complete semantics, tamper-evident verification, exportable.
- **Configuration & secrets** — 12-factor settings, a secret-store loader
  abstraction (Vault / cloud KMS / env-injected), per-workflow feature flags,
  and a configurable data-residency region setting.
- **Observability bootstrap** — structlog with `run_id` correlation,
  OpenTelemetry tracing, Prometheus metrics, and PII scrubbing applied to all
  three signals.
- **Encryption utilities** — AES-256-at-rest helpers and column-level OAuth
  token encryption primitives (key sourced from the secret store).
- **Security baseline** — the RBAC model (roles, permissions, scoping) and the
  constrained agent service identity, as reusable building blocks.
- **Repo layout + docs/CI framework** — the `src/cam/...` skeleton (PTD §15),
  schema-derived tool-doc generation discipline, ADR location, and a
  schema-drift CI check.

## 3. Out of scope

- Any **connector adapter** or the connector **ports/`ConnectorError`** taxonomy
  (→ `connector-framework`).
- Any **workflow logic** and the **orchestration engine** / gates / triggers
  (→ `workflow-orchestration`).
- **Concrete MCP tool / resource / prompt definitions.** Only the
  *doc-generation discipline* for them lives here, not the tools themselves
  (→ `connector-framework`, the workflow specs).
- Webhook ingestion, schedulers, the deadline engine, document generation,
  extraction, QC checks — all downstream specs.
- Choice of concrete vendors, LLM provider, or hosting target — those are
  configurable inputs, not decided here (CONVENTIONS.md §4).

## 4. Users / actors

| Actor | Relationship to this feature |
|---|---|
| **Platform / backend engineer** | Primary consumer: imports the domain model, repositories, audit service, config, and observability bootstrap to build every other spec. |
| **The AI agent (service identity)** | Acts under a constrained, RBAC-scoped service identity defined here; all its actions are audited. |
| **Operations / Admin** | Configures secrets, feature flags, residency region; runs migrations; exports the audit log; monitors metrics/traces. |
| **Security / compliance reviewer** | Verifies audit immutability, encryption, RBAC, PII redaction, residency for SOC 2 / GDPR. |
| **Fee-earner / Attorney (indirect)** | Relies on the audit trail and privilege/PII protections this layer guarantees. |

## 5. Functional requirements (trace to PRD FR-xx)

| ID | Requirement | Trace |
|---|---|---|
| **PF-FR-1** | Provide the canonical normalised domain model (`Contact`, `Matter`, `Document`, `Deadline`, `Communication`, `Task`) as a single shared Pydantic v2 package, reused (not forked) by all features. | FR-2 |
| **PF-FR-2** | Persist domain entities durably in PostgreSQL via SQLAlchemy 2 + Alembic, exposed through a repository pattern with round-trip-safe serialisation. | FR-2; NFR-1 |
| **PF-FR-3** | Provide an append-only, hash-chained audit log capturing `actor, action, inputs, outputs, approval, timestamp, run_id`, written *before* an action is considered complete, tamper-evident and exportable. | FR-15; NFR-2 |
| **PF-FR-4** | Provide 12-factor configuration + a secret-store loader abstraction (Vault / KMS / env-injected) with no secrets in source and no secret values logged. | NFR-8 |
| **PF-FR-5** | Provide per-workflow feature flags so workflows roll out wave-by-wave. | FR-18 (rollout discipline); NFR-6 |
| **PF-FR-6** | Provide a configurable data-residency region setting that downstream storage/telemetry decisions read. | NFR-6 |
| **PF-FR-7** | Bootstrap observability: structured logs (structlog, `run_id` correlation), OpenTelemetry traces, Prometheus metrics, all PII-scrubbed. | NFR-7; NFR-1 |
| **PF-FR-8** | Provide AES-256-at-rest utilities and column-level OAuth-token encryption primitives keyed from the secret store. | NFR-1; NFR-8 |
| **PF-FR-9** | Provide an RBAC model (roles, permissions, matter/tool scoping) and a constrained agent service identity, as reusable primitives downstream specs enforce. | NFR-1; FR-17 (tool scoping) |
| **PF-FR-10** | Provide the documentation/CI framework: schema-derived tool-doc generation discipline, ADR location, and a schema-drift CI check; "done = code + tests + docs". | FR-17; FR-18; NFR-10 |
| **PF-FR-11** | Provide the `src/cam/...` repository layout (PTD §15) so all features land code in stable, predictable locations. | FR-16; FR-18 |

## 6. Non-functional requirements (trace to PRD NFR-xx)

| ID | Requirement | Trace |
|---|---|---|
| **PF-NFR-1** | All persisted data encrypted at rest (AES-256, DB + object store); OAuth tokens column-encrypted; TLS assumed in transit at deploy. Least-privilege access enforced via RBAC. | NFR-1 |
| **PF-NFR-2** | 100% of state-changing actions write an immutable audit record before completion; the chain is verifiable and any tampering detectable. | NFR-2 |
| **PF-NFR-3** | Configurable data residency; PII handling documented; SOC 2 control surfaces and GDPR-aware handling supported by the foundation. | NFR-6 |
| **PF-NFR-4** | Structured logs, metrics, and traces are emitted with `run_id` correlation and PII scrubbing; observability is on by default. | NFR-7 |
| **PF-NFR-5** | No credentials in code; central secret store; rotation-friendly; secret values never logged or echoed. | NFR-8 |
| **PF-NFR-6** | Docs are CI-checked against tool/domain schemas; the build fails on schema drift or missing descriptions. | NFR-10 |
| **PF-NFR-7** | Domain-model serialise/deserialise is loss-free (round-trip equal) for all valid instances; config validation is total (every config either validates or fails fast with a precise error). | NFR-1 (data integrity); supports NFR-3 |

## 7. Dependencies (other feature specs, external systems)

- **Feature-spec dependencies:** **none.** This is the root spec; every other
  spec depends on it (CONVENTIONS.md §9).
- **External systems / libraries (baseline stack, CONVENTIONS.md §4 / PTD §3):**
  Python 3.12+, Pydantic v2, PostgreSQL, SQLAlchemy 2, Alembic, structlog,
  OpenTelemetry SDK, Prometheus client, a cryptography library for AES-256, and
  a secret-store backend (Vault / cloud KMS / env injection). `pytest` +
  Hypothesis for tests.
- **Downstream consumers (informational):** `connector-framework`,
  `workflow-orchestration`, and all seven workflow specs import this package.

## 8. Assumptions & open questions

- **ASSUMPTION (confirm):** The Python package root is `src/cam/` (PTD §15) and
  the distribution is named `cam` (Case Automation MCP). Rename is cheap if the
  firm prefers another name.
- **ASSUMPTION (confirm):** A single PostgreSQL instance backs state + audit +
  jobs at v1 (PTD §2). Audit may move to a separate database/schema later for
  stronger separation of duties; the repository abstraction keeps that swap
  local.
- **ASSUMPTION (confirm):** Secret-store backend for v1 is **env-injected**
  secrets (12-factor) behind the loader abstraction, with Vault/KMS adapters
  added when the hosting target is finalised (PRD §11 Q3; PTD §18 Q2). The
  loader interface is identical across backends.
- **ASSUMPTION (confirm):** The hash chain uses SHA-256 over a canonical
  serialisation of each record plus the prior record's hash. Genesis record
  uses a fixed zero/`null` previous-hash sentinel.
- **ASSUMPTION (confirm):** AES-256-GCM (authenticated encryption) is the
  at-rest cipher for application-layer column encryption; the data-encryption
  key is wrapped by a key-encryption key from the secret store (envelope
  encryption), enabling rotation without re-encrypting historical data eagerly.
- **ASSUMPTION (confirm):** Initial RBAC roles are `paralegal`,
  `attorney`, `intake_coordinator`, `ops_admin`, and `agent_service`
  (mapping PRD §4 personas + the non-human agent). Exact permission matrix is
  confirmed with the firm before any tool enforces it.
- **ASSUMPTION (confirm):** Default data-residency region is **US**
  (immigration domain, CONVENTIONS.md §4); the setting is configurable and read
  by downstream storage/telemetry.
- **ASSUMPTION (confirm):** PII redaction targets a configurable field/pattern
  list including immigration identifiers (A-number, passport/visa numbers,
  SSN/ITIN, date-of-birth, biometric references) plus generic emails/phones.
  The exact pattern set is reviewed by the firm.
- **Open question (PTD §18 Q4):** where LLM inference runs has residency
  implications; the residency config here must be expressive enough to flag
  inference egress, even though the LLM client itself is out of scope.

## 9. Acceptance criteria (testable checklist)

- [ ] The six domain models import from a single module; no feature redefines
  them; `mypy` passes in strict mode for the package.
- [ ] Every domain model round-trips: `Model.model_validate(m.model_dump())`
  equals `m` for all valid instances (proven by PBT, see `pbt-properties.md`).
- [ ] Alembic `upgrade head` then `downgrade base` runs clean on an empty DB;
  repositories CRUD each entity and re-read equal values.
- [ ] Appending N audit records yields a chain whose verification passes; any
  single-byte mutation, deletion, or re-order of a stored record makes
  verification **fail** at the first affected link (PBT).
- [ ] An attempted `UPDATE`/`DELETE` on the audit table is rejected (DB-level
  constraint/trigger) and the attempt is itself observable.
- [ ] `encrypt`/`decrypt` round-trips for all inputs and keys
  (`decrypt(encrypt(x,k),k) == x`); ciphertext never equals plaintext for
  non-empty input; wrong key fails authentication (PBT).
- [ ] Config load with a complete valid environment succeeds; any missing or
  malformed required setting fails fast with a precise, secret-free error
  (PBT: validation totality).
- [ ] No secret value appears in any log line, trace attribute, metric label,
  or error message — verified by a redaction test over crafted secret-bearing
  inputs.
- [ ] An RBAC check denies every (role, permission) pair not explicitly granted
  by the matrix; the `agent_service` identity is denied any permission outside
  its constrained grant set (PBT: RBAC soundness).
- [ ] Logs carry `run_id`; a trace spans an audited action; a Prometheus
  counter increments — all with PII scrubbed.
- [ ] The schema-drift CI check fails the build when a domain/tool schema
  changes without its generated doc being regenerated, and passes when in sync.
- [ ] The audit log exports to a portable file (e.g. JSON Lines) whose chain
  re-verifies after export.
