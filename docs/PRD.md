# PRD — Case Automation MCP Server

**Status:** Draft v0.1 (conceptual)
**Owner:** RA
**Last updated:** 2026-05-29
**Companion doc:** `file PTD.md` (technical design)

> **Domain assumption (flagged for correction):** This PRD is written for a
****legal / professional-services** practice — the language of *matters, client
> intake, statutes of limitation, document routing* drove the design. The
> integration layer is deliberately vendor-agnostic, so the same product
> generalises to any caseworked, deadline-driven, document-heavy practice
> (immigration, accounting, claims, compliance, agency ops). If your domain
> differs, the use cases and connectors shift but the architecture holds.

---

## 1. Summary

A **Model Context Protocol (MCP) server**, written in Python, that turns the
firm's fragmented operational stack — case/matter management, CRM, email, and
document systems — into a single, AI-orchestratable surface. It lets an LLM
agent (and scheduled/automated triggers) **read state, take action, and run
multi-step workflows** across systems that today require manual copy-paste
between tabs.

It is **not** a chatbot bolted onto one tool. It is the *connective tissue and
workflow engine* underneath the AI: a governed set of tools, resources, and
prompts that make safe, auditable automation possible.

### The one-sentence pitch

> Give the firm's AI a reliable, permissioned, audited set of hands that can
> draft, file, extract, route, remind, and verify across every system — so
> people do judgement work, not clerical work.

---

## 2. Problem

Operational staff and fee-earners lose hours per day to **swivel-chair work**:

| Pain | Today | Cost |
| --- | --- | --- |
| Client intake | Re-keyed from email/forms into CRM *and* case system | Slow onboarding, transcription errors |
| Document drafting | Manual template fill from scattered data | Hours/matter, version chaos |
| Status updates | Hand-written, inconsistent, forgotten | Client anxiety, complaints |
| Deadlines | Tracked in heads, spreadsheets, calendars | **Missed deadlines = liability** |
| Document routing | Email attachments, ad-hoc folders | Lost files, privilege risk |
| Data extraction | Read PDF → type into system | Tedious, error-prone |
| Quality control | Ad-hoc, person-dependent | Inconsistent output reaching clients |

Root cause: the systems don't talk, and the glue is human attention. Existing
point integrations (Zapier-style) are brittle, hard to govern, and can't reason.

---

## 3. Goals & non-goals

### Goals

- **G1** Single MCP surface over case mgmt, CRM, email, and document systems.
- **G2** Reliable operational workflows: doc generation, email drafting, data
  extraction, client intake.
- **G3** Deadline-reminder engine that calculates and tracks date-driven
  obligations, with escalation.
- **G4** Form pre-population, status-update emails, and document routing as
  first-class, repeatable workflows.
- **G5** Built-in **verification / QC** gates before anything client-facing or
  externally filed leaves the building.
- **G6** Extensible connector model for third-party tools via API + webhooks.
- **G7** Self-documenting: every workflow, tool, and connector carries clear,
  maintained technical documentation.

### Non-goals (v1)

- Not a replacement for the case/CRM systems of record.
- No autonomous **filing to courts/regulators** without human sign-off.
- No legal advice generation; the AI assists, licensed humans decide.
- Not a general customer-facing chatbot.
- No bulk data migration tooling (separate effort).

---

## 4. Users & personas

| Persona | Role | What they want |
| --- | --- | --- |
| **Paralegal / Case Manager** | Runs the day-to-day | Stop re-keying; never miss a deadline; faster drafts |
| **Fee-earner / Attorney** | Owns the matter & risk | Trustworthy output, audit trail, control gates |
| **Intake Coordinator** | Front door | Turn a lead/email into a structured matter in minutes |
| **Operations / Admin** | Keeps the lights on | Connectors that don't break; visibility; easy onboarding of new tools |
| **The AI Agent** | Non-human actor | Clear, safe tools with predictable contracts |

---

## 5. Core capabilities (functional requirements)

Grouped by the MCP primitive that exposes them. (Tool contracts in `PTD.md §5`.)

### 5.1 Connectivity (G1, G6)

- **FR-1** Connectors for ≥1 system in each category at v1: case management,
  CRM, email, document store. Pluggable adapter interface for the rest.
- **FR-2** Normalised domain model (Matter, Contact, Document, Task, Deadline,
  Communication) so workflows are written once, not per-vendor.
- **FR-3** Inbound **webhook ingestion** (new lead, email received, doc signed,
  status change) that can trigger workflows.
- **FR-4** Outbound API calls with retry, rate-limit handling, and idempotency.

### 5.2 Operational workflows (G2)

- **FR-5 Document generation** — fill templates from matter/contact data, output
  DOCX/PDF, version and store back to the document system.
- **FR-6 Email drafting** — compose context-aware emails (intake confirmation,
  status update, request-for-info) as **drafts for human review** by default.
- **FR-7 Data extraction** — pull structured fields from PDFs, forms, and
  inbound email into the domain model (with confidence scores).
- **FR-8 Client intake** — guided/automated capture from lead → structured
  matter + CRM contact + opening tasks.

### 5.3 Signature workflows (G3, G4)

- **FR-9 Form pre-population** — given a matter, pre-fill known fields on
  standard forms; surface gaps for human completion.
- **FR-10 Status-update emails** — detect status change → draft tailored update
  → route for approval → send/log.
- **FR-11 Document routing** — classify, name, file, and permission documents to
  the right matter/folder/recipient automatically.
- **FR-12 Deadline reminders** — compute deadlines from triggers + rule sets
  (e.g. "X days from filing"), track, remind, and escalate on slippage.

### 5.4 Verification & quality control (G5)

- **FR-13** Pre-send/pre-file **QC checks**: required fields present, names/dates
  consistent across documents, template variables fully resolved, attachments
  present, recipient matches matter.
- **FR-14** **Human-in-the-loop gates** on all external/client-facing and
  irreversible actions; configurable per workflow and per risk tier.
- **FR-15** **Audit trail** for every action: who/what/when, inputs, outputs,
  approvals — exportable.

### 5.5 Extensibility & documentation (G6, G7)

- **FR-16** New connector or workflow can be added without touching core.
- **FR-17** Every tool self-describes (schema + description) to the MCP client.
- **FR-18** Living technical docs generated/checked in alongside code; a
  connector or workflow is "done" only when documented.

---

## 6. Non-functional requirements

| \# | Requirement | Target |
| --- | --- | --- |
| NFR-1 | **Confidentiality / privilege** | All data encrypted in transit & at rest; least-privilege access; privilege never broken by routing |
| NFR-2 | **Auditability** | 100% of state-changing actions logged immutably |
| NFR-3 | **Reliability** | Workflows idempotent; safe retries; no duplicate sends/filings |
| NFR-4 | **Human control** | No irreversible external action without an explicit approval gate (default-on) |
| NFR-5 | **Latency** | Read tools &lt; 2s typical; long jobs run async with status |
| NFR-6 | **Data residency / compliance** | Configurable region; support SOC2 path; PII handling documented |
| NFR-7 | **Observability** | Structured logs, metrics, traces per workflow run |
| NFR-8 | **Secret hygiene** | No credentials in code; central secret store; rotation-friendly |
| NFR-9 | **Graceful degradation** | One connector down ≠ whole system down |
| NFR-10 | **Documentation freshness** | Docs CI-checked against tool schemas |

---

## 7. High-impact use cases (prioritised)

Sequenced by **impact × feasibility** — deliberately starting narrow, expanding
as trust and connector coverage grow (G6, and the "expand over time" ask).

### Wave 1 — Prove value, low blast radius

1. **Intake → structured matter** (FR-8, FR-7): inbound lead email/form becomes a
   CRM contact + matter shell + opening task list. *Read-heavy, human confirms.*
2. **Status-update email drafts** (FR-10, FR-6): status change → drafted, on-brand
   update awaiting one-click approval. *High frequency, high client-satisfaction.*
3. **Deadline calculation & reminders** (FR-12): the liability killer. Compute and
   never-forget date obligations. *Pure win, mostly internal.*

### Wave 2 — Drafting & routing

4. **Document generation from templates** (FR-5, FR-9): engagement letters,
   standard filings, routine correspondence.
5. **Document routing & filing** (FR-11): auto-classify and file inbound docs to
   the right matter with correct permissions.
6. **Data extraction from PDFs/forms** (FR-7): IDs, court documents, intake forms.

### Wave 3 — Verification & scale

7. **QC verification gates** (FR-13–15): consistency and completeness checks across
   the whole drafting/sending pipeline.
8. **Cross-system reconciliation**: detect drift between CRM and case system.
9. **New-connector onramp** (FR-16): add the next third-party tool in days.

> Rationale for ordering: Wave 1 items are mostly **read + draft** (reversible,
> human-confirmed), which builds trust and audit history before we hand the
> system write/send/file authority in later waves.

---

## 8. Success metrics

| Metric | Baseline (manual) | Target |
| --- | --- | --- |
| Intake → matter created | — | &lt; 10 min, &lt; 5% correction rate |
| Time per routine document | hrs | minutes; &gt; 70% reduction |
| Missed/late deadlines | track | → **0** critical misses |
| Status updates sent on time | track | &gt; 95% within SLA |
| Documents mis-filed | track | near-zero |
| QC defects reaching client | track | &gt; 80% reduction |
| Connector onboarding time | — | new connector in &lt; 1 week |
| Staff hours reclaimed | — | quantified per workflow per month |

---

## 9. Risks & mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| AI sends wrong/incorrect external comms | High (client/legal) | Default human-in-loop gates; QC checks; drafts-not-sends |
| Missed deadline due to engine bug | Critical (malpractice) | Redundant reminders, escalation, monitoring, never silent-fail |
| Connector API change breaks workflow | Med | Adapter isolation, contract tests, alerting, graceful degradation |
| Confidentiality / privilege breach | Critical | Encryption, RBAC, audit, privilege-aware routing, no data in logs |
| Over-automation erodes oversight | Med | Risk-tiered gates; everything auditable & reversible where possible |
| Vendor lock-in | Med | Vendor-agnostic domain model; connectors swappable |
| Scope creep across infinite workflows | Med | Wave-based roadmap; impact×feasibility gating |

---

## 10. Dependencies & assumptions

- Firm provides API access / credentials for in-scope systems (case mgmt, CRM,
  email, document store).
- A system of record exists per category (we integrate, we don't replace).
- An MCP-capable client (e.g. Claude, or a custom agent) drives the server; the
  server is also operable headless via its scheduler/webhook sidecar.
- Legal/compliance sign-off on data handling before go-live.

---

## 11. Open questions

1. Which exact vendors are in scope first (Clio? Salesforce? Microsoft 365 vs
   Google Workspace? NetDocuments/iManage/SharePoint)? — drives connector v1.
2. Required compliance posture (SOC2? HIPAA? jurisdictional residency)?
3. Hosting: on the firm's infra, Zo, or cloud? (affects PTD deployment section.)
4. Which deadline rule sets / jurisdictions must be encoded first?
5. Risk tiers: which actions are auto-allowed vs always-gated?

---

## 12. Roadmap (indicative)

- **Phase 0 — Foundations:** core domain model, MCP scaffold, secret store,
  audit log, one connector per category (read-only), docs framework.
- **Phase 1 — Wave 1 workflows:** intake, status drafts, deadline engine.
- **Phase 2 — Wave 2 workflows:** doc generation, routing, extraction; write
  paths with gates.
- **Phase 3 — Wave 3:** QC verification suite, reconciliation, connector SDK,
  hardening, compliance certification path.

See `file PTD.md` for how each of these is built.