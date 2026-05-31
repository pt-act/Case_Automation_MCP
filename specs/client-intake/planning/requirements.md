# Requirements — Client Intake

> Feature slug: `client-intake` · Wave 1 · Traces: FR-8, FR-7, FR-6; NFR-2, NFR-3, NFR-4.
> Inherits everything in `../../_shared/CONVENTIONS.md` (process, domain model,
> risk tiers, security baseline, immigration handling). This file states only
> what is specific to Client Intake.

## 1. Context & problem

Today an inbound lead (an email or a web/intake form) is re-keyed by hand into
both the CRM *and* the case-management system, producing slow onboarding and
transcription errors (PRD §2). Client Intake is the Wave-1 workflow that turns a
single inbound lead into a structured **US immigration matter**: a CRM `Contact`,
a `Matter` shell, computed key dates, an opening task checklist, and a drafted
welcome email held for human approval before anything reaches the client
(PRD §7 Wave 1, PTD §7).

This is deliberately **read-heavy and human-confirmed**: the workflow extracts,
de-duplicates, and drafts, but every external/irreversible effect (the welcome
send) sits behind a human approval gate. It is the first workflow to handle
immigration PII from first touch, so confidentiality and audit are first-class
from step one.

## 2. In scope

- The **intake workflow definition** (`intake` state machine, PTD §7 steps:
  `parse_lead → dedupe_contact → create_contact → create_matter →
  compute_deadlines → open_tasks → draft_welcome → GATE:human_review →
  send_welcome`) registered with `workflow-orchestration`.
- The **`intake.run` composite MCP tool** (PTD §5.1) that starts/drives the
  workflow from a lead payload.
- The **`intake_interview` prompt** (PTD §5.3) for structured, human- or
  agent-led completion of missing intake fields.
- **Lead → domain field mapping**: raw lead (email body / form fields) →
  `Contact` + `Matter` (+ intake metadata), driven by a configurable mapping
  schema. Extraction itself is delegated to `data-extraction`.
- **Dedupe logic**: deciding whether an incoming lead matches an existing
  `Contact` (and/or existing `Matter`) before any create.
- **Configurable opening-task checklist** keyed by case type.
- **Welcome email draft + approval gate + post-gate send**, via `email.draft` /
  `email.send` (FR-6).
- **Idempotency** for the whole run and per side-effecting step (NFR-3).
- **Audit** of every step, gap, gate decision, and side effect (NFR-2).

## 3. Out of scope

- Extraction internals (OCR, LLM structuring, confidence scoring) — owned by
  `data-extraction`; intake only consumes its output.
- Deadline computation internals and the **contents** of deadline rule sets —
  owned by `deadline-engine`; intake only invokes `deadline.compute`/`schedule`.
- Email, CRM, and case connector **adapters** — owned by `connector-framework`;
  intake depends on the ports only.
- The durable state-machine engine, gate plumbing, and idempotency-key store —
  owned by `workflow-orchestration`.
- QC verification registry — Wave 3 (`qc-verification`); intake performs only
  minimal inline completeness/recipient checks needed for the gate, and is
  designed to delegate to `qc.verify` when available.

## 4. Users / actors

| Actor | Interest in intake |
|---|---|
| **Intake Coordinator** | Front door; turns a lead into a matter in minutes, completes gaps, owns the welcome. |
| **Paralegal / Case Manager** | Inherits the opened matter + tasks; needs the checklist correct and complete. |
| **Fee-earner / Attorney** | Owns the matter and the client relationship; is the default approver of the welcome gate. |
| **The AI Agent** | Non-human actor that may call `intake.run` and use `intake_interview` to fill gaps. |
| **Operations / Admin** | Configures the lead→field mapping and per-case-type task checklists. |

## 5. Functional requirements (trace to PRD FR-xx)

- **FR-CI-1 (FR-8)** Accept an inbound lead (email or form payload) via
  `intake.run` and via a webhook-triggered run, and produce a structured matter.
- **FR-CI-2 (FR-8, FR-7)** Parse the lead into the domain model by delegating
  field extraction to `data-extraction`, then map results through a configurable
  intake mapping schema into `Contact` + `Matter` + intake metadata.
- **FR-CI-3 (FR-8)** Capture US immigration intake fields (client bio, country
  of origin, A-number if any, case type, prior filings — see §8 assumptions),
  validating required-vs-optional per case type.
- **FR-CI-4 (FR-8)** **Dedupe** the lead against existing contacts/matters before
  any create; on a confident match, reuse rather than duplicate.
- **FR-CI-5 (FR-8)** Create/upsert the CRM `Contact` (`contact.upsert`, write
  confirm) and create the `Matter` shell (`matter.create`, write confirm) with
  immigration specifics carried in `practice_area`, `external_ids`, and intake
  metadata (no immigration categories hard-coded into core types — CONVENTIONS §5).
- **FR-CI-6 (FR-8)** Invoke `deadline.compute` then `deadline.schedule` for the
  matter's case type, attaching the results to `Matter.key_dates`.
- **FR-CI-7 (FR-8)** Create the **configurable opening-task checklist** for the
  resolved case type as `Task` records on the matter.
- **FR-CI-8 (FR-6)** Draft a context-aware **welcome email** as a draft
  (`email.draft`), never auto-sent.
- **FR-CI-9 (FR-6, FR-14)** Park the run at `GATE:human_review`; only on approval
  (via any of the three channels — CONVENTIONS §4) run `send_welcome`
  (`email.send`, gated/human).
- **FR-CI-10 (FR-8)** Surface any missing required field as an explicit **gap**
  (not a silent blank), resolvable via `intake_interview` or the gate UI, before
  the welcome can be approved.
- **FR-CI-11 (FR-8)** Provide the `intake_interview` prompt to drive structured
  completion of captured/missing fields.

## 6. Non-functional requirements (trace to PRD NFR-xx)

- **NFR-CI-1 (NFR-3)** The whole run and each side-effecting step is idempotent:
  the same lead processed twice yields **exactly one** contact and **one** matter,
  and at most one welcome send.
- **NFR-CI-2 (NFR-2)** 100% of state-changing steps (create contact, create
  matter, schedule deadlines, open tasks, draft, gate decision, send) write a
  hash-chained audit record before completing.
- **NFR-CI-3 (NFR-4)** No client-facing send occurs without an explicit, default-on
  human approval gate.
- **NFR-CI-4 (NFR-1)** Immigration PII (A-number, passport, DOB, country of
  origin, status) is encrypted at rest, never written to logs/telemetry, and the
  matter is privileged from creation.
- **NFR-CI-5 (NFR-5)** Synchronous portions of `intake.run` return < 2s typical;
  extraction and deadline computation run as workflow steps with status, not as a
  blocking call.
- **NFR-CI-6 (NFR-7)** Each step emits structured logs/metrics/traces correlated
  by `run_id` (gate dwell time, gap count, dedupe outcome).
- **NFR-CI-7 (NFR-9)** A degraded connector (e.g. case system down) parks the run
  for retry/human attention rather than failing the whole intake or losing the lead.

## 7. Dependencies (other feature specs, external systems)

- `platform-foundation` — domain model, persistence, audit log, config/secrets,
  observability.
- `connector-framework` — CRM/Case/Email ports, webhook ingestion, idempotency,
  `ConnectorError` taxonomy.
- `workflow-orchestration` — durable state machine, gates, idempotency keys,
  retry/compensation, triggers.
- `data-extraction` — `document.extract` / lead parsing with per-field confidence.
- `deadline-engine` — `deadline.compute` / `deadline.schedule` and rule sets.
- External systems (via ports only, vendor-agnostic): CRM, case management,
  email. No vendor payloads referenced (CONVENTIONS §3.10).

## 8. Assumptions & open questions

- **ASSUMPTION (confirm):** v1 intake field set is — client bio (full name, DOB,
  preferred language, contact email/phone, mailing address), **country of
  origin/nationality**, **A-number** (alien registration number, optional),
  current immigration status, **case type**, prior filings/history, source of
  lead, and free-text matter description.
- **ASSUMPTION (confirm):** supported **case types** (enumeration in config):
  family-based, employment-based, asylum, naturalization, removal-defense,
  and "other/uncategorised". The enumeration is data-driven, not hard-coded
  (CONVENTIONS §5, §8).
- **ASSUMPTION (confirm):** **required-field set varies per case type** (e.g.
  removal-defense requires A-number and next hearing date; naturalization
  requires green-card date). Encoded as a per-case-type required-fields schema.
- **ASSUMPTION (confirm):** the **opening-task checklist is configurable per
  case type** (e.g. "send G-28", "collect passport bio page", "open RFE watch").
  Form numbers (I-130, I-485, N-400, G-28) are referenced only as config data,
  flagged unconfirmed (CONVENTIONS §8).
- **ASSUMPTION (confirm):** **dedupe match key** is email-first, then
  (normalised name + DOB), then A-number when present; threshold and tie-break
  behaviour configurable. A confident match reuses the contact; an ambiguous
  match surfaces a gap for human disambiguation rather than auto-merging.
- **ASSUMPTION (confirm):** a lead may legitimately produce a **new matter on an
  existing contact** (returning client); dedupe applies to contact and matter
  independently.
- **ASSUMPTION (confirm):** default welcome-gate **approver** is the matter's
  responsible attorney; fallback to the intake coordinator role.
- **ASSUMPTION (confirm):** the welcome email **template** is firm-branded and
  lives in `document-generation`/template storage; intake supplies variables.
- **Open question:** does intake also send an internal "new matter opened"
  notification (separate, non-client-facing) — and is that gated? Default: yes,
  internal-only, ungated.

## 9. Acceptance criteria (testable checklist)

- [ ] `intake.run` accepts an email-shaped and a form-shaped lead payload and
      returns a `run_id` with initial status.
- [ ] Re-submitting the identical lead (same idempotency key) returns the same
      `run_id` and creates **no** additional contact, matter, tasks, or sends.
- [ ] After a successful run pre-gate, exactly one `Contact` and one `Matter`
      exist for the lead, linked via `Matter.client` and `external_ids`.
- [ ] For each configured case type, opening the matter creates **exactly** the
      configured task checklist for that case type (no missing, no extra).
- [ ] Every required matter field for the resolved case type is either present
      or recorded as an explicit gap; gaps block gate approval.
- [ ] The welcome email exists as a draft after `draft_welcome`, and `email.send`
      is never invoked before the gate is approved.
- [ ] On gate approval the welcome is sent exactly once; on rejection it is never
      sent and the run records the rejection.
- [ ] Every state-changing step has a corresponding audit record chained to the
      prior one, with no client PII in log/telemetry output.
- [ ] A simulated case-system outage parks the run (resumable) rather than
      losing the lead or partially duplicating writes.
