# PTD — Case Automation MCP Server (Technical Design)

**Status:** Draft v0.1 (conceptual)
**Companion doc:** `PRD.md` (product requirements)
**Last updated:** 2026-05-29

This document specifies *how* the product in `PRD.md` is built: architecture,
the MCP surface, connectors, the workflow engine, the deadline engine, document
generation, verification/QC, security, observability, and the documentation
discipline that keeps it all legible.

---

## 1. Design principles

1. **Vendor-agnostic core.** Workflows speak a normalised domain model; vendors
   live only behind adapters. Swapping Clio→MyCase touches one connector.
2. **Safe by default.** External/irreversible actions are *drafted*, not *done*,
   until a human-in-the-loop gate clears them. Reversibility is a design input.
3. **Idempotent everything.** Every state-changing action carries an idempotency
   key; retries can never double-send or double-file.
4. **Audit is not optional.** Every action writes an immutable audit record
   before it is considered complete.
5. **Two faces, one core.** The same core library is exposed both **interactively**
   (MCP server, agent-driven) and **autonomously** (scheduler + webhook sidecar).
6. **Documentation is part of "done".** Tool schemas, connector contracts, and
   workflow specs are checked in and CI-verified.

---

## 2. System architecture

```
                    ┌───────────────────────────────────────────────┐
   MCP client       │                 MCP SERVER (Python)            │
 (Claude / agent) ──┤  FastMCP: tools · resources · prompts          │
                    │        │                                       │
 Webhooks (3rd-     │        ▼                                       │
 party events) ───► │  ┌──────────────┐   ┌───────────────────────┐ │
   FastAPI sidecar  │  │ Workflow     │   │  Core services         │ │
                    │  │ Orchestrator │◄─►│  - Document generation │ │
 Scheduler ───────► │  │ (state mach.)│   │  - Deadline engine     │ │
 (APScheduler/      │  └──────┬───────┘   │  - Verification / QC   │ │
  Celery beat)      │         │           │  - Notification        │ │
                    │         ▼           │  - Extraction (OCR/LLM)│ │
                    │  ┌──────────────┐   └───────────────────────┘ │
                    │  │ Connector    │                             │
                    │  │ layer (ports)│  Case · CRM · Email · Docs   │
                    │  └──────┬───────┘                             │
                    └─────────┼───────────────────────────────────-─┘
                              ▼
        ┌──────────┬───────────┬───────────┬──────────────┐
        │ Case mgmt│   CRM     │  Email     │  Document     │  (external APIs)
        │ (Clio…)  │(Salesforce│ (Graph /   │  store        │
        │          │  /HubSpot)│  Gmail)    │ (NetDocs/SP)  │
        └──────────┴───────────┴───────────┴──────────────┘

   Persistence:  Postgres (state, audit, jobs)  ·  Object store (documents)
                 Redis (queue / locks / idempotency)  ·  Secret store (vault/KMS)
```

### Component responsibilities
- **MCP server (FastMCP):** publishes the tool/resource/prompt surface to the
  agent. Stateless request handlers that call into the core.
- **Sidecar (FastAPI + scheduler):** receives webhooks and fires time-based
  triggers; both enqueue workflow runs through the *same* orchestrator.
- **Workflow orchestrator:** runs multi-step workflows as durable, resumable
  state machines with explicit gates.
- **Connector layer:** ports-and-adapters; one adapter per external system.
- **Core services:** the reusable engines (docs, deadlines, QC, extraction,
  notifications) shared by all workflows.

---

## 3. Technology stack

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.12+** | Required; rich ecosystem for docs/LLM/integrations |
| MCP | **`mcp` SDK / FastMCP** | Official protocol impl; decorator-based tools |
| HTTP / webhooks | **FastAPI + Uvicorn** | Async, typed, OpenAPI for the sidecar |
| Models / validation | **Pydantic v2** | Typed domain model = tool schemas for free |
| HTTP client | **httpx** (+ **tenacity**) | Async calls, retries, timeouts |
| DB / migrations | **PostgreSQL + SQLAlchemy 2 + Alembic** | Durable state & audit |
| Queue / scheduling | **Redis + Celery** (or **APScheduler** for lean start) | Async jobs, beat for deadlines |
| Cache / locks / idempotency | **Redis** | Distributed locks, idempotency keys |
| Document generation | **docxtpl / python-docx + Jinja2**, **WeasyPrint** or **LibreOffice headless** for PDF | Template fill → DOCX/PDF |
| Extraction | **pdfplumber / PyMuPDF** + OCR (**Tesseract**) + LLM structuring | Text + structured fields |
| Object storage | **S3-compatible** (or firm doc store) | Generated artefacts |
| Secrets | **Vault / cloud KMS / env-injected** | No creds in code |
| Observability | **structlog + OpenTelemetry + Prometheus** | Logs, traces, metrics |
| Tests | **pytest + respx/vcr** | Unit + connector contract tests |
| Packaging | **uv / poetry**, **ruff**, **mypy** | Reproducible, linted, typed |

> No secret values ever live in source. Connectors read tokens from the secret
> store at runtime; OAuth refresh tokens are stored encrypted in Postgres.

---

## 4. Domain model (the normalisation layer)

Pydantic models that every workflow speaks; connectors map to/from these.

```python
class Contact(BaseModel):
    id: str; source: str            # source = which system owns it
    name: str; email: EmailStr | None
    phone: str | None; role: str | None
    external_ids: dict[str, str]    # {"crm": "...", "case": "..."}

class Matter(BaseModel):            # a case / file / engagement
    id: str; source: str
    reference: str; title: str
    status: str; practice_area: str | None
    client: Contact; responsible: str | None
    opened_at: datetime; key_dates: list["Deadline"]
    external_ids: dict[str, str]

class Document(BaseModel):
    id: str; matter_id: str
    name: str; mime_type: str; uri: str
    classification: str | None      # e.g. "engagement_letter", "court_filing"
    version: int; privileged: bool
    checksum: str; created_at: datetime

class Deadline(BaseModel):
    id: str; matter_id: str
    name: str; due_at: datetime
    rule_id: str | None             # which rule computed it
    status: Literal["pending","reminded","done","missed"]
    escalation_level: int

class Communication(BaseModel):     # email / message, in or out
    id: str; matter_id: str | None
    direction: Literal["in","out"]
    channel: str; subject: str | None
    body: str; status: str          # draft|pending_approval|sent
    participants: list[Contact]

class Task(BaseModel):
    id: str; matter_id: str
    title: str; assignee: str | None
    due_at: datetime | None; status: str
```

Mapping is the connector's job; the core never sees a Clio JSON blob.

---

## 5. MCP surface

### 5.1 Tools (actions the agent can invoke)
Each tool = typed input/output (Pydantic) → self-describing schema for the
client. Tools are thin: validate → call core → audit → return.

| Tool | Purpose | Risk tier / gate |
|---|---|---|
| `matter.get` / `matter.search` | Read matters | read |
| `matter.create` | Open a matter | write (confirm) |
| `contact.upsert` | Create/update CRM contact | write (confirm) |
| `document.generate` | Template → DOCX/PDF, store | write (confirm) |
| `document.route` | Classify + file + permission | write (confirm) |
| `document.extract` | Structured fields from a file | read |
| `email.draft` | Compose email as **draft** | draft only |
| `email.send` | Send a previously-approved draft | **gated (human)** |
| `deadline.compute` | Apply rule set → dates | read |
| `deadline.schedule` | Persist + arm reminders | write |
| `form.prefill` | Pre-populate a known form | write (confirm) |
| `intake.run` | Lead → matter + contact + tasks | composite (confirm) |
| `qc.verify` | Run verification checks | read (gate input) |
| `workflow.run` / `workflow.status` | Start/inspect a workflow | varies |

**Risk tiers:** `read` (no side effects) · `write (confirm)` (reversible, single
confirmation) · `gated (human)` (external/irreversible — explicit approval
required, default-on per NFR-4).

### 5.2 Resources (read-only context the agent can pull)
- `matter://{id}` — full matter view (contacts, docs, deadlines, comms).
- `template://{name}` — document template + required variables.
- `deadline-rules://{jurisdiction}` — encoded rule sets.
- `calendar://upcoming` — deadlines/tasks due in window.

### 5.3 Prompts (reusable, versioned prompt templates)
- `intake_interview` — structured client-intake questioning.
- `status_update_email` — on-brand status update from matter delta.
- `qc_checklist` — verification reasoning scaffold.
- `extraction_schema` — guide LLM extraction into the domain model.

---

## 6. Connector layer (ports & adapters)

A `Connector` protocol per category defines the **port**; each vendor is an
**adapter**. Workflows depend on the port, never the adapter.

```python
class CaseConnector(Protocol):
    async def get_matter(self, id: str) -> Matter: ...
    async def create_matter(self, data: MatterDraft) -> Matter: ...
    async def list_deadlines(self, matter_id: str) -> list[Deadline]: ...

class CRMConnector(Protocol):
    async def upsert_contact(self, c: Contact) -> Contact: ...
    async def find_contact(self, q: str) -> list[Contact]: ...

class EmailConnector(Protocol):
    async def create_draft(self, c: Communication) -> str: ...
    async def send(self, draft_id: str, idem_key: str) -> str: ...

class DocStoreConnector(Protocol):
    async def put(self, doc: Document, content: bytes) -> Document: ...
    async def get(self, id: str) -> tuple[Document, bytes]: ...
    async def move(self, id: str, folder: str, acl: ACL) -> Document: ...
```

**Adapter requirements (every connector MUST):**
- Map vendor payloads ↔ domain model.
- Use `httpx` + `tenacity` (exponential backoff, jitter), honour rate limits.
- Be idempotent on writes (pass/forward idempotency keys).
- Surface a typed `ConnectorError` taxonomy (auth, rate-limit, not-found,
  transient, fatal) so the orchestrator can decide retry vs gate vs fail.
- Ship **contract tests** against recorded fixtures (respx/vcr).
- Ship a `CONNECTOR.md` (auth model, scopes, endpoints, quirks, webhook events).

**v1 candidate adapters** (confirm in PRD §11): Case = Clio; CRM = Salesforce or
Lawmatics; Email = Microsoft Graph or Gmail API; Docs = SharePoint/OneDrive,
NetDocuments, or Google Drive. DocuSign as a document/signature event source.

### Webhook ingestion
The FastAPI sidecar exposes `/webhooks/{connector}`. Each inbound event is:
1. **Verified** (HMAC signature / shared secret per provider).
2. **Normalised** into an internal `Event`.
3. **Deduplicated** (provider event id → Redis).
4. **Enqueued** to the orchestrator as a trigger.

---

## 7. Workflow orchestration engine

Workflows are **durable state machines**, not scripts — so a multi-step process
survives restarts, can pause at a human gate for hours/days, and resumes cleanly.

```python
@workflow("intake")
class IntakeWorkflow:
    steps = [
        "parse_lead",        # extract fields from email/form (Extraction svc)
        "dedupe_contact",    # CRM lookup
        "create_contact",    # write (confirm)
        "create_matter",     # write (confirm)
        "compute_deadlines", # deadline engine
        "open_tasks",        # checklist
        "draft_welcome",     # email.draft
        "GATE:human_review", # pause for approval
        "send_welcome",      # email.send (post-gate)
    ]
```

- **Persistence:** each run = a row + step states in Postgres; inputs/outputs
  stored for audit & resume.
- **Gates:** a `GATE:*` step parks the run in `awaiting_approval`; an approval
  (via MCP tool, UI, or email action) resumes it. Gates are configurable per
  workflow and per risk tier (NFR-4).
- **Idempotency:** each step's external effect keyed on `(run_id, step)`.
- **Retry/compensation:** transient errors retry with backoff; fatal errors
  park the run for human attention (never silent-fail, esp. deadlines).
- **Triggers:** webhook event, schedule, or explicit `workflow.run` from the
  agent.

---

## 8. Deadline engine (the liability-critical piece)

Treated as a first-class, independently-monitored subsystem (PRD G3, Risk row 2).

- **Rule sets** are declarative and versioned (per jurisdiction/practice area):
  ```yaml
  rule: statute_of_limitations_personal_injury
  trigger: incident_date
  offset: { years: 2 }
  adjust: next_business_day
  reminders: [ -90d, -30d, -7d, -1d ]
  escalation: { after_due: notify_supervisor }
  ```
- **Computation** uses business-day calendars + holiday tables; never naive date
  math. All computed dates are auditable back to the rule + inputs.
- **Reminders** fire via the scheduler at each offset; **escalation** raises level
  and widens recipients on slippage.
- **Safety:** redundant scheduling, dead-man's-switch monitoring (alert if the
  scheduler itself stops), and reconciliation against the case system of record.

---

## 9. Document generation pipeline

```
template (DOCX/HTML, Jinja2 vars)
   + matter/contact data (domain model)
        │  ── form.prefill / document.generate
        ▼
   render (docxtpl / Jinja2) ──► DOCX
        │                          └─► WeasyPrint / LibreOffice ─► PDF
        ▼
   QC.verify (all vars resolved? names/dates consistent?)
        ▼
   store (object store / doc connector) ─► Document(version, checksum)
        ▼
   route (document.route) ─► correct matter/folder + ACL
```

- Templates declare their required variables; missing data surfaces as **gaps**
  for human completion rather than silent blanks (FR-9).
- Outputs are versioned and checksummed; nothing overwrites silently.

---

## 10. Extraction service

- **Text layer:** `pdfplumber`/PyMuPDF for native PDFs; **Tesseract OCR** fallback
  for scans/images.
- **Structuring:** LLM (via the MCP client or a structuring call) maps raw text →
  domain model using the `extraction_schema` prompt, returning **per-field
  confidence**.
- **Gate:** low-confidence fields are flagged for human verification before they
  enter a system of record (feeds QC, NFR/risk).

---

## 11. Verification & QC framework

A composable check registry run by `qc.verify` and as workflow gate inputs:

| Check | Example |
|---|---|
| Completeness | all required template vars resolved; required matter fields present |
| Consistency | client name/date-of-birth/matter ref identical across all docs in a packet |
| Recipient integrity | email recipient ∈ matter participants; right client |
| Attachment integrity | referenced attachments present & correct version |
| Privilege | privileged docs not routed to external recipients |
| Deadline sanity | computed dates within plausible bounds; no past-due on create |
| Extraction confidence | flagged fields below threshold |

Each check returns `pass | warn | fail` + reason. **Any `fail` blocks the gate.**
Results are attached to the workflow run and audit log.

---

## 12. Security, compliance & audit

- **Encryption:** TLS in transit; AES-256 at rest (DB + object store). OAuth
  tokens encrypted at the column level.
- **Secrets:** central store (Vault/KMS); runtime injection; rotation-friendly;
  **never logged**, never in source (also: never run env-dumping commands).
- **AuthZ:** RBAC — tools and matters scoped to roles; connectors hold least
  privilege OAuth scopes. The agent acts under a constrained service identity.
- **Audit log:** append-only Postgres table (hash-chained) — `actor, action,
  inputs, outputs, approval, timestamp, run_id`. Exportable for review.
- **Privilege/confidentiality:** privilege-aware routing checks (QC §11); PII
  redaction in logs/traces; no client content in telemetry.
- **Human-in-the-loop:** default-on gates for external/irreversible actions; risk
  tiers configurable.
- **Compliance posture:** designed toward SOC2; data-residency configurable;
  HIPAA path if required (confirm in PRD §11).

---

## 13. Observability & operations

- **Structured logging** (structlog), correlation id = `run_id`, PII-scrubbed.
- **Tracing** (OpenTelemetry) spans per workflow step & connector call.
- **Metrics** (Prometheus): workflow success/failure, gate dwell time, connector
  latency/error rate, deadline reminders fired, QC fail rate.
- **Alerting:** scheduler liveness (dead-man's-switch), connector auth expiry,
  parked-run backlog, any missed-deadline event → page.
- **Runbooks** per failure class shipped in `docs/runbooks/`.

---

## 14. Deployment topology

- **Two processes, one image:**
  1. `mcp-server` — the MCP endpoint (stdio for local agents; HTTP/SSE for remote).
  2. `sidecar` — FastAPI webhooks + Celery worker + beat scheduler.
- **Backing services:** Postgres, Redis, object store, secret store.
- **Hosting:** containerised; deploy to firm infra, a cloud, or a Zo
  Service/host (confirm in PRD §11). On Zo, the sidecar maps cleanly to a
  `register_user_service` HTTP service; Postgres/Redis as process-mode services.
- **Config:** 12-factor; per-environment secrets; feature flags per workflow to
  roll out wave-by-wave.

---

## 15. Repository layout

```
case_automation_mcp/
├── pyproject.toml
├── PRD.md  PTD.md  README.md
├── src/cam/
│   ├── mcp_server/        # FastMCP tools, resources, prompts
│   ├── sidecar/           # FastAPI webhooks + scheduler/worker
│   ├── core/
│   │   ├── domain/        # Pydantic domain model
│   │   ├── workflows/     # state-machine workflow defs
│   │   ├── services/      # docs, deadlines, qc, extraction, notify
│   │   ├── audit/         # audit log
│   │   └── orchestrator/  # run engine, gates, idempotency
│   ├── connectors/
│   │   ├── base.py        # ports (Protocols) + ConnectorError taxonomy
│   │   ├── case_clio/     # + CONNECTOR.md, contract tests
│   │   ├── crm_*/  email_*/  docs_*/
│   ├── persistence/       # SQLAlchemy models, Alembic migrations
│   └── config/            # settings, secret loading, feature flags
├── tests/                 # unit + connector contract tests
└── docs/
    ├── connectors/        # generated/maintained connector docs
    ├── workflows/         # one spec per workflow
    └── runbooks/
```

---

## 16. Documentation discipline (PRD G7 / FR-18)

- **Tool docs** are derived from Pydantic schemas + docstrings; a CI check fails
  the build if a tool lacks a description or its schema drifts from docs.
- **Every connector** ships `CONNECTOR.md` (auth, scopes, endpoints, webhook
  events, rate limits, quirks) — required for merge.
- **Every workflow** ships a `docs/workflows/<name>.md` spec (trigger, steps,
  gates, side effects, idempotency keys, failure handling).
- **ADRs** (`docs/adr/`) record significant technical decisions.
- "Done" = code + tests + docs. No exceptions; this is how the system stays
  legible as it expands across waves.

---

## 17. Build sequence (maps to PRD §12 roadmap)

| Phase | Build |
|---|---|
| **0 Foundations** | domain model, MCP scaffold, Postgres+audit, secret store, one read-only connector per category, docs/CI framework |
| **1 Wave-1** | orchestrator + gates, deadline engine, intake workflow, status-update drafts |
| **2 Wave-2** | write-path connectors, doc generation, routing, extraction service |
| **3 Wave-3** | full QC suite, reconciliation, connector SDK + onboarding, hardening, compliance path |

---

## 18. Open technical questions

1. Concrete vendor adapters for v1 (drives auth flows & webhook formats).
2. Hosting target (firm cloud vs Zo vs other) — finalises §14.
3. Celery vs APScheduler for v1 (scale vs simplicity).
4. Which LLM client drives the MCP server, and where extraction inference runs
   (privacy/residency implications).
5. Approval-gate UX: MCP tool callback, lightweight web UI, or email-action?
6. Jurisdiction rule sets to encode first in the deadline engine.
