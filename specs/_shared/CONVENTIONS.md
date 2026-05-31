# Spec Conventions — Case Automation MCP Server

**Status:** Baseline v1
**Audience:** Internal engineering (team of 1–2 developers)
**Applies to:** every feature spec package under `Case_Automation_MCP/specs/`

This is the single source of truth for *how* we write specs and the shared
vocabulary, domain model, stack, and compliance baseline that every feature
spec inherits. Each feature spec references this file instead of repeating it.

> **Source material:** these specs build on `concept/PRD.md` (product
> requirements) and `concept/PTD.md` (technical design). The PRD/PTD are the
> *inspiration and reference*; the per-feature specs here are the
> implementation-ready elaboration. Where this file and the PTD agree, the PTD
> wins on architecture; where they differ, this file wins on spec process.

---

## 1. The four-phase spec process

We use a four-phase, gated process adapted from the "spec-architect" method.
The original method is wrapped in a lot of house metaphor; we keep only the
engineering scaffolding. The four phases and their gates:

| Phase | Output file | Purpose |
|---|---|---|
| **1 — Requirements** | `planning/requirements.md` | What problem, for whom, with what acceptance criteria. The "what & why". |
| **2 — Specification** | `spec.md` | The behavioural contract: scope, interfaces, data, behaviour, edge cases, **out-of-scope**. Still "what", not line-by-line "how". |
| **3 — Tasks** | `tasks.md` | Ordered, dependency-mapped, parallelisable implementation tasks with acceptance criteria and iteration estimates. |
| **4 — Property validation** | `pbt-properties.md`, `security-audit-prep.md` | Property-based test (PBT) definitions and a security review checklist, layered on top of focused tests. |

### Review gates (plain-language; no esoteric terms)

Between phases, a short self-review gate must pass before proceeding:

- **Gate 1 — Scope & alignment review** (after Phase 1): Are the requirements
  scoped, testable, and aligned with the PRD goals (G1–G7) and non-goals? Does
  every requirement have at least one measurable acceptance criterion?
- **Gate 2 — Design completeness review** (after Phase 2): Are all interfaces,
  data shapes, error paths, and edge cases specified? Is out-of-scope explicit?
  Are dependencies on other feature specs named?
- **Gate 3 — Task readiness review** (after Phase 3): Is every task small enough
  to implement and verify independently? Are dependencies and parallelisation
  marked? Does every task carry acceptance criteria?
- **Gate 4 — Validation readiness review** (after Phase 4): Are invariants
  captured as properties? Are the security-sensitive surfaces enumerated with a
  test/verification plan?

If a gate fails, fix the current phase before moving on — do not paper over a
gap in a later phase.

> **Terminology note.** Earlier versions of this method used metaphorical
> vocabulary ("consciousness", "alignment to values", "quantum state",
> "Orion-OS"). **None of that vocabulary is used in these specs.** Gates are
> plain engineering reviews. If you see such terms anywhere, treat them as
> noise to be removed.

---

## 2. File layout per feature spec

```
specs/<feature-slug>/
├── planning/
│   └── requirements.md       # Phase 1
├── spec.md                   # Phase 2
├── tasks.md                  # Phase 3
├── pbt-properties.md         # Phase 4 — property-based tests
└── security-audit-prep.md    # Phase 4 — security review checklist
```

`<feature-slug>` is lowercase-hyphenated and matches the directory name.

---

## 3. Writing rules (apply to every spec)

1. **Spec the "what", defer the "how".** `requirements.md` and `spec.md`
   describe observable behaviour and contracts, not internal code structure.
   `tasks.md` is where implementation steps appear.
2. **Out-of-scope is mandatory.** Every `spec.md` must list what it explicitly
   does *not* cover, to prevent scope creep.
3. **Estimate in iterations, not calendar time.** A task is `XS` (trivial), `S`
   (one focused sitting), `M` (a few sittings), or `L` (should probably be
   split). Never hours/days — we don't know the team's velocity yet.
4. **Acceptance criteria are testable.** Use checklists with concrete,
   verifiable statements ("returns 409 on duplicate idempotency key"), not
   vibes ("works well").
5. **Keep components implementable.** If a single described component would
   exceed roughly 400 lines of code, note that it should be split, and how.
6. **Focused tests per task group: 2–8.** Each task group declares the focused
   (example-based) tests that prove it. If a group needs more than ~10, it's
   doing too much — split it.
7. **PBT for invariants, focused tests for examples.** Use property-based tests
   for things that must hold for *all* inputs (idempotency, round-trips, access
   control, hash-chain integrity). Use focused tests for specific scenarios.
8. **Name cross-feature dependencies explicitly** by feature slug (see §9).
9. **Flag every assumption.** Anything not yet confirmed by the client is
   written as `ASSUMPTION (confirm):` so it is visible and correctable. Do not
   silently bake in unconfirmed decisions.
10. **No vendor names hard-wired into behaviour.** Specs target the normalised
    domain model and connector ports (§5, §6 of PTD), never a specific vendor's
    payload. Vendor specifics live only in future connector adapters.

---

## 4. Confirmed project decisions (as of this baseline)

These are settled and should be treated as fixed inputs across all specs:

- **Domain:** US-based immigration law firm. Jurisdiction = **United States**
  (federal immigration: USCIS, EOIR/immigration courts, DOS/consular, CBP/ICE
  touchpoints). Specific case types, forms, and deadline rule sets are **not yet
  confirmed** → design them as **configurable, data-driven rule sets**, not
  hard-coded logic. (See §8.)
- **Build stack:** the **PTD §3 stack is the baseline** and specs are written
  against it (Python 3.12+, FastMCP, FastAPI, Pydantic v2, PostgreSQL +
  SQLAlchemy 2 + Alembic, Redis, Celery *or* APScheduler, httpx + tenacity,
  docxtpl/python-docx + Jinja2 + WeasyPrint/LibreOffice, pdfplumber/PyMuPDF +
  Tesseract, S3-compatible object store, structlog + OpenTelemetry + Prometheus,
  pytest + respx/vcr, Hypothesis for PBT).
- **Client's existing systems (case mgmt, CRM, email, doc store):** **unknown →
  vendor-agnostic.** Specs define the connector *ports*; specific adapters are
  out of scope until vendors are confirmed.
- **Compliance posture:** SOC 2 path + GDPR-aware + **configurable data
  residency**, with **attorney–client privilege protected** end to end. (See §7.)
- **LLM provider:** **not yet chosen → provider-agnostic.** Any LLM use
  (extraction structuring, drafting) goes through a swappable client interface;
  privacy/residency of inference is a configurable deployment decision.
- **Deployment target:** firm infrastructure **or** cloud → **containerised and
  host-agnostic** (12-factor, per-environment secrets, feature flags per
  workflow).
- **Human approval gates** are surfaced via **all three** channels: MCP tool
  callback, a lightweight web UI, and an email action. Specs must not assume a
  single channel; the approval mechanism is an abstraction with three adapters.

---

## 5. Shared domain model

All specs speak the normalised Pydantic v2 domain model from **PTD §4**:
`Contact`, `Matter`, `Document`, `Deadline`, `Communication`, `Task`. Do not
redefine these per feature; reference them. If a feature needs a new shared
type, it is proposed in `platform-foundation` and reused, not forked.

Immigration-relevant fields are accommodated via the existing model
(`Matter.practice_area`, `Matter.key_dates`, `external_ids`, `Document.classification`)
plus configurable enumerations — **not** by hard-coding immigration categories
into the core types. Examples of immigration specifics that live in *config /
rule data*, not core code: case type (family-based, employment-based, asylum,
naturalization, removal defense, etc.), form identifiers (e.g. I-130, I-485,
N-400, G-28 — `ASSUMPTION (confirm)` which matter first), and deadline rules.

---

## 6. Risk tiers & approval gates (shared vocabulary)

From PTD §5.1. Every action a spec introduces must declare its risk tier:

- **`read`** — no side effects. No gate.
- **`write (confirm)`** — reversible, single-confirmation write (e.g. create
  matter, upsert contact, generate+store a draft document).
- **`gated (human)`** — external/irreversible (send email, file to a system of
  record, anything client-facing). **Requires an explicit human approval**,
  default-on (NFR-4). Approval arrives via any of the three channels (§4).

A **gate** parks a workflow run in `awaiting_approval` until approved/rejected.
Gates are configurable per workflow and per risk tier.

---

## 7. Security, compliance & audit baseline (inherited by all specs)

- **Encryption:** TLS in transit; AES-256 at rest (DB + object store); OAuth
  tokens encrypted at column level.
- **Secrets:** central store (Vault/KMS/env-injected); never in source, never
  logged. No env-dumping commands.
- **AuthZ:** RBAC; tools and matters scoped to roles; connectors hold
  least-privilege scopes; the agent runs under a constrained service identity.
- **Audit log:** append-only, **hash-chained** Postgres table — `actor, action,
  inputs, outputs, approval, timestamp, run_id`. Every state-changing action
  writes an audit record *before* it is considered complete. Exportable.
- **Privilege/confidentiality:** privilege-aware routing (no privileged doc to
  external recipients); PII redaction in logs/traces; no client content in
  telemetry. This is heightened for immigration PII (A-numbers, passports,
  biometrics, immigration status, country-of-origin).
- **Compliance posture:** SOC 2 controls path; GDPR-aware data subject handling;
  **configurable data residency**; attorney–client privilege preserved.

Every spec's `security-audit-prep.md` re-checks its own surfaces against this
baseline; this section is the floor, not the ceiling.

---

## 8. Immigration domain handling (US)

- Treat jurisdiction = US federal immigration as the **first and only encoded
  jurisdiction** for v1, but build the deadline/rule machinery as
  **declarative, versioned, data-driven rule sets** (PTD §8) so additional
  rule sets are config, not code.
- **Do not hard-code** specific case types, form numbers, or deadline offsets
  into core logic. Express them as rule data + enumerations, each marked
  `ASSUMPTION (confirm)` until the firm confirms its priority case types.
- Illustrative (non-binding) examples a spec may *reference* but must flag as
  unconfirmed: RFE/NOID response windows, biometrics appointment windows,
  master/individual hearing dates (EOIR), priority-date/visa-bulletin tracking,
  status/EAD/work-authorization expirations, filing windows. Mark all as
  `ASSUMPTION (confirm)`.

---

## 9. Feature spec catalogue & dependencies

Ten feature specs, in two groups. Cross-references use these slugs.

**Platform / foundation (built first):**
1. `platform-foundation` — domain model, persistence, audit log, config &
   secrets, observability, security baseline, docs/CI discipline.
2. `connector-framework` — vendor-agnostic ports & adapters, webhook ingestion,
   retries/idempotency, `ConnectorError` taxonomy, contract-test harness.
3. `workflow-orchestration` — durable resumable state-machine engine, gates,
   idempotency keys, retry/compensation, triggers.

**Workflows (built on the foundation):**
4. `client-intake` — lead → CRM contact + matter shell + opening tasks + draft
   welcome (Wave 1).
5. `deadline-engine` — declarative rule sets, business-day calc, reminders,
   escalation, reconciliation (Wave 1, liability-critical).
6. `status-update-emails` — status change → drafted update → approval → send/log
   (Wave 1).
7. `document-generation` — template → DOCX/PDF, versioned, gaps surfaced, QC,
   store (Wave 2).
8. `document-routing` — classify, name, file, permission docs; privilege-aware
   (Wave 2).
9. `data-extraction` — text + OCR + LLM structuring with per-field confidence
   (Wave 2).
10. `qc-verification` — composable check registry feeding gates (Wave 3).

**Dependency summary** (A → B means A depends on B):

- `connector-framework` → `platform-foundation`
- `workflow-orchestration` → `platform-foundation`, `connector-framework`
- `data-extraction` → `platform-foundation`
- `qc-verification` → `platform-foundation`, `workflow-orchestration`
- `deadline-engine` → `platform-foundation`, `workflow-orchestration`, `connector-framework`
- `client-intake` → `platform-foundation`, `connector-framework`, `workflow-orchestration`, `data-extraction`, `deadline-engine`
- `status-update-emails` → `platform-foundation`, `connector-framework`, `workflow-orchestration`, `qc-verification`
- `document-generation` → `platform-foundation`, `connector-framework`, `qc-verification`
- `document-routing` → `platform-foundation`, `connector-framework`, `qc-verification`, `data-extraction`

---

## 10. Status & estimation legend (used in `tasks.md`)

- **Task status:** `todo` · `in_progress` · `blocked` · `done`
- **Size:** `XS` · `S` · `M` · `L` (L should usually be split; see §3.5)
- **Dependency notation:** `depends on: <task-id>` (intra-spec) or
  `depends on: <feature-slug>#<task-id>` (cross-spec)
- **Parallelisable:** mark `parallel: yes` when a task shares no dependency with
  its siblings and can be picked up concurrently.

---

## 11. File templates

Each feature spec follows these skeletons. Keep headings stable so specs are
scannable and diffable.

### `planning/requirements.md`
```
# Requirements — <Feature Name>
## 1. Context & problem
## 2. In scope
## 3. Out of scope
## 4. Users / actors
## 5. Functional requirements (trace to PRD FR-xx)
## 6. Non-functional requirements (trace to PRD NFR-xx)
## 7. Dependencies (other feature specs, external systems)
## 8. Assumptions & open questions  (each ASSUMPTION (confirm): ...)
## 9. Acceptance criteria (testable checklist)
```

### `spec.md`
```
# Spec — <Feature Name>
## 1. Summary
## 2. Scope & out-of-scope
## 3. Domain types used / introduced
## 4. Interfaces (MCP tools/resources/prompts, ports, internal APIs)
## 5. Behaviour & flows (happy path + state transitions)
## 6. Edge cases & error handling (incl. ConnectorError handling)
## 7. Risk tiers & gates for each action
## 8. Data & persistence
## 9. Observability (logs/metrics/traces for this feature)
## 10. Security & privilege considerations
## 11. Dependencies & integration points
## 12. Test strategy (focused tests + pointer to PBT)
## 13. Open questions
```

### `tasks.md`
```
# Tasks — <Feature Name>
## Overview (task groups + critical path + parallelisation)
## Group 1: <name>
  - [ ] 1.1 <task> — size, depends on, parallel?, acceptance criteria, focused tests (2–8)
## Group N: ...
## Dependency graph (intra-spec + cross-spec)
## Definition of done (code + tests + docs)
```

### `pbt-properties.md`
```
# Property-Based Tests — <Feature Name>
## Invariants under test (plain English)
## Properties (Hypothesis-style pseudocode)
## Generators / input domains
## Known edge inputs to seed
```

### `security-audit-prep.md`
```
# Security Audit Prep — <Feature Name>
## Sensitive surfaces (data, actions, external calls)
## Threats & mitigations (map to NFR-1,2,4,6,8)
## AuthZ & privilege checks
## Audit log coverage
## PII handling & residency
## Pre-audit checklist
```

---

## 12. Glossary

- **MCP** — Model Context Protocol; the interface this server exposes to an AI
  agent via **tools** (callable actions), **resources** (read-only context),
  and **prompts** (reusable templates).
- **Matter** — a case/file/engagement (here: an immigration case).
- **Connector / port / adapter** — a port is the abstract interface for a
  category (Case/CRM/Email/DocStore); an adapter is a concrete vendor
  implementation. Workflows depend on ports only.
- **Gate** — a human-approval checkpoint that parks a workflow run until cleared.
- **Idempotency key** — a value that makes a repeated action a no-op, preventing
  double-send/double-file.
- **Focused test** — example/scenario-based test (pytest).
- **PBT (property-based test)** — a test asserting an invariant holds for *all*
  generated inputs (Hypothesis).
- **Risk tier** — `read` / `write (confirm)` / `gated (human)` (see §6).
- **Wave** — PRD rollout grouping (Wave 1/2/3) sequencing which workflows ship.
