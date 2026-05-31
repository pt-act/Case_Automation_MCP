# Spec — Client Intake

> Slug: `client-intake` · Wave 1 · Traces: FR-8, FR-7, FR-6; NFR-2, NFR-3, NFR-4.
> Inherits `../_shared/CONVENTIONS.md` (domain model §5, risk tiers §6, security
> §7, immigration handling §8). This spec describes behaviour and contracts only;
> implementation steps live in `tasks.md`.

## 1. Summary

Client Intake is the Wave-1 workflow that converts an inbound lead (email or
form) into a structured US immigration matter. It orchestrates extraction,
de-duplication, contact + matter creation, deadline computation, opening-task
creation, and a welcome-email draft that is held at a human approval gate before
sending. It exposes one composite tool (`intake.run`), one prompt
(`intake_interview`), and a registered `intake` workflow definition. It is
read-heavy and human-confirmed: the only `gated (human)` action is the welcome
send.

## 2. Scope & out-of-scope

**In scope:** the `intake` workflow definition; the `intake.run` composite tool;
the `intake_interview` prompt; the configurable lead→domain **mapping schema**;
dedupe logic; the configurable per-case-type **opening-task checklist**; the
welcome draft/gate/send sequencing; per-run and per-step idempotency; audit of
every step and gate decision; gap surfacing.

**Out of scope:** extraction internals (`data-extraction`); deadline computation
internals and rule contents (`deadline-engine`); CRM/Case/Email **adapters**
(`connector-framework`); the state-machine/gate/idempotency engine
(`workflow-orchestration`); the full QC registry (`qc-verification`); the welcome
email **template body** (template storage / `document-generation`). See
`requirements.md §3`.

## 3. Domain types used / introduced

**Used unchanged (PTD §4 / CONVENTIONS §5):** `Contact`, `Matter`, `Task`,
`Deadline`, `Communication`.

**Introduced (intake-local, not forked core types):**

- `LeadPayload` — the raw inbound lead.
  - `source_channel: Literal["email","form","manual","api"]`
  - `received_at: datetime`
  - `raw: dict` (email envelope/body or form fields, as received)
  - `attachments: list[AttachmentRef]` (optional; passed to extraction)
  - `provider_event_id: str | None` (for webhook dedupe)
- `IntakeFields` — the normalised, mapped intake capture (immigration specifics
  live here as data, **not** in core `Matter`/`Contact`):
  - bio: `full_name`, `dob`, `preferred_language`, `email`, `phone`, `address`
  - immigration: `country_of_origin`, `a_number` (optional, validated format),
    `current_status`, `case_type` (enum, config-driven), `prior_filings` (list),
    `lead_source`, `description`
  - `field_confidence: dict[str, float]` (carried from extraction)
  - **ASSUMPTION (confirm):** exact field set per §8 of `requirements.md`.
- `IntakeGap` — a missing/low-confidence required field: `{field, reason,
  severity: Literal["block","warn"]}`.
- `DedupeResult` — `{contact_match: MatchRef | None, matter_match: MatchRef |
  None, decision: Literal["reuse_contact","new_contact","ambiguous"],
  score: float}`.
- `CaseTypeConfig` — config record: `{case_type, required_fields: list[str],
  opening_tasks: list[TaskTemplate], deadline_rule_ids: list[str],
  welcome_template_id: str}`. Loaded from config (CONVENTIONS §5, §8).
- `TaskTemplate` — `{title, assignee_role, due_offset?, sort}` → rendered to `Task`.

`IntakeFields`, `IntakeGap`, and `DedupeResult` are persisted with the workflow
run for audit/resume; they are not new system-of-record entities. Any field that
becomes broadly shared is promoted to `platform-foundation` rather than forked
(CONVENTIONS §5).

## 4. Interfaces (MCP tools / resources / prompts, ports, internal APIs)

### 4.1 MCP tool — `intake.run` (composite; risk tier: composite/confirm)

- **Input:** `{ lead: LeadPayload, case_type_hint?: str, idempotency_key?: str,
  options?: { auto_open_tasks?: bool, approver_role?: str } }`.
- **Output:** `{ run_id, status, contact_id?, matter_id?, gaps: IntakeGap[],
  dedupe: DedupeResult, welcome_draft_id?, gate_state }`.
- **Behaviour:** validates input, derives an idempotency key if absent (see §8),
  starts/locates the `intake` workflow run, returns current state. Non-blocking:
  long steps (extraction, deadline compute) execute as workflow steps; the tool
  returns the run handle and current snapshot. Re-invocation with the same key
  returns the existing run.

### 4.2 Prompt — `intake_interview` (PTD §5.3)

A versioned prompt that drives structured completion of `IntakeFields`. Inputs:
current `IntakeFields`, the `gaps` list, and the resolved `CaseTypeConfig`
required fields. Output guidance: ask only for unfilled/low-confidence required
fields, one logical group at a time; never invent values; emit values mapped to
field names. Used by the agent or surfaced in the gate UI.

### 4.3 Workflow definition — `intake` (registered with `workflow-orchestration`)

Steps exactly per PTD §7:
`parse_lead → dedupe_contact → create_contact → create_matter →
compute_deadlines → open_tasks → draft_welcome → GATE:human_review →
send_welcome`. Each step's contract is in §5.

### 4.4 Ports consumed (vendor-agnostic; CONVENTIONS §3.10)

- `CRMConnector.find_contact`, `CRMConnector.upsert_contact`
- `CaseConnector.create_matter`
- `EmailConnector.create_draft`, `EmailConnector.send`
- Cross-feature tools: `document.extract` (data-extraction),
  `deadline.compute` + `deadline.schedule` (deadline-engine).

### 4.5 Config inputs (read at run time)

- `intake_mapping_schema` — maps extracted/raw lead fields → `IntakeFields`.
- `case_type_config[]` — the `CaseTypeConfig` records (required fields, task
  templates, rule ids, welcome template id).
- `dedupe_config` — match keys, threshold, tie-break, ambiguity behaviour.
All are config/data, not code (CONVENTIONS §8); each carries an
`ASSUMPTION (confirm)` until the firm confirms contents.

## 5. Behaviour & flows (happy path + state transitions)

Happy path (run states in **bold**):

1. **received** — `intake.run` (or a webhook trigger) creates the run with the
   `LeadPayload` and an idempotency key.
2. **parse_lead** — call `document.extract` over lead body/attachments; map raw +
   extracted fields through `intake_mapping_schema` into `IntakeFields` with
   `field_confidence`. Resolve `case_type` (from extraction, `case_type_hint`, or
   default "other/uncategorised", flagged as a gap if unresolved).
3. **dedupe_contact** — call `CRMConnector.find_contact` using `dedupe_config`
   keys; produce `DedupeResult`. `reuse_contact` → skip create; `ambiguous` →
   record a blocking gap for human disambiguation; `new_contact` → proceed.
4. **create_contact** (write/confirm) — if not reusing, `contact.upsert`
   (idempotent on run+step). Attach immigration specifics via `external_ids` +
   intake metadata, not new core fields.
5. **create_matter** (write/confirm) — `matter.create` with `practice_area` set
   from case type, `client` = the contact, intake metadata attached, `privileged`
   posture set from creation (NFR-1). Idempotent on run+step.
6. **compute_deadlines** — `deadline.compute` for the case type's
   `deadline_rule_ids`, then `deadline.schedule`; attach to `Matter.key_dates`.
7. **open_tasks** — render the case type's `opening_tasks` `TaskTemplate`s into
   `Task` records (assignee from role mapping). Creates **exactly** the configured
   set (completeness invariant).
8. **draft_welcome** (draft only) — `email.draft` using the case type's
   `welcome_template_id` and `IntakeFields`/matter variables; recipient = client
   contact. Run minimal inline checks (recipient ∈ matter participants; no
   unresolved blocking gaps) — designed to defer to `qc.verify` when available.
9. **GATE:human_review** — park in `awaiting_approval`. Approval via any of the
   three channels (MCP callback / web UI / email action — CONVENTIONS §4). The
   gate payload shows the matter, contact, gaps, and the draft.
10. **send_welcome** (gated/human) — on approval, `email.send` with the run+step
    idempotency key (exactly-once). On rejection, transition to **rejected**, no
    send. On approval-with-edits, re-draft then send.
11. **completed** — terminal success; final audit record closes the chain.

Resume: because the run is a durable state machine (`workflow-orchestration`),
the gate may sit for hours/days and resume cleanly; a restart mid-run replays
from the last persisted step using stored inputs/outputs.

## 6. Edge cases & error handling (incl. ConnectorError handling)

- **Duplicate lead (same key):** `intake.run` returns the existing run; no new
  side effects (NFR-3). Webhook duplicates are filtered upstream by
  `provider_event_id` (PTD §6) and again by the run idempotency key.
- **Ambiguous dedupe:** do **not** auto-merge; record a blocking `IntakeGap` and
  surface for human disambiguation in the gate UI.
- **Unresolved case type:** proceed with "other/uncategorised", mark a gap; the
  task checklist falls back to a configurable default set.
- **Missing required field(s):** recorded as blocking gaps; `draft_welcome` may
  proceed but the gate cannot be approved while blocking gaps remain (configurable).
- **`ConnectorError` taxonomy (PTD §6):**
  - `auth` / `fatal` → park run, alert; no partial duplicate writes.
  - `rate-limit` / `transient` → retry with backoff (engine-provided); step stays
    idempotent so retries cannot double-write.
  - `not-found` (e.g. template) → blocking gap / parked run with clear reason.
- **Partial failure after create_contact but before create_matter:** resume
  re-enters at `create_matter`; the contact step is a no-op via its idempotency
  key, so no duplicate contact (NFR-3 / PBT focus).
- **Extraction low confidence:** low-confidence required fields become gaps; never
  silently accepted into the matter (FR-7 alignment, deferred internals).
- **Gate rejection / timeout:** rejection → terminal `rejected`; configurable
  reminder/escalation on a stale gate (no auto-send ever).
- **Email send returns ambiguous success:** treat as sent if the idempotency key
  was accepted; never re-send on uncertainty (NFR-3).

## 7. Risk tiers & gates for each action

| Step / action | Risk tier | Gate |
|---|---|---|
| `parse_lead` (extraction) | `read` | none |
| `dedupe_contact` (CRM find) | `read` | none |
| `create_contact` (`contact.upsert`) | `write (confirm)` | single confirm |
| `create_matter` (`matter.create`) | `write (confirm)` | single confirm |
| `compute_deadlines` (`deadline.compute`) | `read` | none |
| `deadline.schedule` | `write (confirm)` | single confirm |
| `open_tasks` | `write (confirm)` | single confirm |
| `draft_welcome` (`email.draft`) | `draft only` | none |
| `GATE:human_review` | — | **explicit human approval (default-on, NFR-4)** |
| `send_welcome` (`email.send`) | `gated (human)` | the gate above clears it |

Write-confirm steps may be auto-confirmed within a run started by an authorised
actor (configurable per workflow, CONVENTIONS §6); the client-facing send is
**always** gated.

## 8. Data & persistence

- **Run record** (owned by `workflow-orchestration`): stores `LeadPayload`,
  `IntakeFields`, `DedupeResult`, `gaps`, step states, and step inputs/outputs for
  audit + resume.
- **Idempotency keys:** run-level key = caller-supplied or derived as a stable
  hash of normalised lead identity (`source_channel` + `provider_event_id` if
  present, else normalised `email`+`full_name`+`received_at` bucket). Per-step
  external effects keyed on `(run_id, step)` (PTD §3, §7).
- **Created entities:** `Contact`, `Matter`, `Task[]`, `Deadline[]`,
  `Communication` (the welcome) persisted via their connectors/system of record;
  intake stores only references (`external_ids`) plus the run snapshot.
- **PII at rest:** immigration PII fields (A-number, passport, DOB,
  country-of-origin, status) stored encrypted (AES-256, CONVENTIONS §7); never in
  logs/telemetry.
- **Audit:** every state-changing step writes a hash-chained audit record
  (`actor, action, inputs, outputs, approval, timestamp, run_id`) before the step
  is considered complete (NFR-2).

## 9. Observability (logs/metrics/traces for this feature)

- **Logs (structlog, PII-scrubbed):** one structured event per step keyed by
  `run_id`; dedupe decision, gap count, gate state transitions. Never log PII
  values — log field **names**/counts only.
- **Metrics (Prometheus):** `intake_runs_total{outcome}`, `intake_dedupe_total
  {decision}`, `intake_gaps{severity}`, `intake_gate_dwell_seconds`,
  `intake_welcome_sends_total`, `intake_step_errors_total{step,error_class}`.
- **Traces (OpenTelemetry):** span per workflow step and per connector/cross-tool
  call (`document.extract`, `deadline.compute`, `email.send`), correlation id =
  `run_id` (PTD §13).

## 10. Security & privilege considerations

- **PII from first touch:** the `LeadPayload` may contain immigration PII before a
  matter exists; treat the run record as confidential from `received`. Encrypt at
  rest; scrub from logs/traces (NFR-1, CONVENTIONS §7).
- **Privilege from creation:** the `Matter` is privileged on create; the welcome
  recipient must be a matter participant (recipient-integrity check) so privileged
  content is never routed externally (PTD §11).
- **Gate before any client-facing send:** default-on human approval before
  `email.send` (NFR-4); no path sends pre-approval.
- **AuthZ:** `intake.run` and the gate approval are RBAC-scoped; the agent runs
  under a constrained service identity; connectors hold least-privilege scopes
  (CONVENTIONS §7).
- **Audit:** full, hash-chained, exportable; gate approver and decision recorded.
- Detailed enumeration and pre-audit checklist in `security-audit-prep.md`.

## 11. Dependencies & integration points

- `platform-foundation` — domain model, persistence, audit, config/secrets, obs.
- `connector-framework` — CRM/Case/Email ports, idempotency, `ConnectorError`.
- `workflow-orchestration` — run engine, gates, idempotency keys, retry, triggers.
- `data-extraction` — `document.extract` for `parse_lead`.
- `deadline-engine` — `deadline.compute` + `deadline.schedule`.
- Trigger sources: `intake.run` (agent/tool) and webhook (new-lead event,
  PTD §6) — both enqueue the **same** `intake` run.

## 12. Test strategy (focused tests + pointer to PBT)

- **Focused tests (per task group in `tasks.md`):** lead-shape parsing (email vs
  form), mapping schema application, dedupe decisions (reuse/new/ambiguous),
  per-case-type checklist rendering, gap surfacing, gate sequencing
  (no-send-before-approval), rejection path, connector-error parking/resume.
- **Property-based tests (`pbt-properties.md`):** dedupe idempotency (one
  contact + one matter on double-process), no-send-before-gate, opening-task-set
  completeness, mapping completeness (every required field present or surfaced as
  a gap).
- **Contract tests:** against recorded CRM/Case/Email port fixtures (respx/vcr) —
  intake side only; adapter contract tests live in `connector-framework`.

## 13. Open questions

- Should write-confirm steps be auto-confirmed inside an authorised `intake.run`,
  or require per-step confirmation? Default proposed: auto within an authorised
  run; send always gated. **ASSUMPTION (confirm).**
- Is an internal "matter opened" notification in scope, and is it gated? Default:
  internal-only, ungated. **ASSUMPTION (confirm).**
- Does an ambiguous dedupe ever permit auto-merge with high confidence, or always
  require human disambiguation? Default: always human. **ASSUMPTION (confirm).**
- Exact intake field set, case-type enumeration, per-type required fields, and
  opening-task checklists — all **ASSUMPTION (confirm)** (`requirements.md §8`).
