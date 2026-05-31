# Tasks — Client Intake

> Slug: `client-intake` · Wave 1. Sizes: `XS/S/M/L` (CONVENTIONS §3.3, §10).
> Dependency notation: intra-spec `depends on: <id>`; cross-spec
> `depends on: <slug>#<id>` (CONVENTIONS §10). "Definition of done = code +
> tests + docs" (PTD §16) applies to every group.

## Overview (task groups + critical path + parallelisation)

Eight task groups:

1. **G1 — Intake config & domain types** (mapping schema, case-type config, local types)
2. **G2 — Lead parsing & field mapping** (consumes `data-extraction`)
3. **G3 — Dedupe logic**
4. **G4 — Contact & matter creation** (write/confirm, idempotent)
5. **G5 — Deadlines & opening-task checklist**
6. **G6 — Welcome draft, gate, send** (the only gated/human action)
7. **G7 — `intake.run` tool, `intake_interview` prompt, workflow registration**
8. **G8 — Idempotency, audit, observability hardening**

**Critical path:** G1 → G2 → G3 → G4 → G5 → G6 → G7 → G8.
**Parallelisable:** G1 (config) and G3 (dedupe rules) can start together once the
local types exist; G5's checklist work is independent of G6's draft work once G4
lands; observability (G8) instrumentation can be layered per group.

---

## Group 1: Intake config & domain types

- [ ] **1.1** Define intake-local Pydantic types (`LeadPayload`, `IntakeFields`,
  `IntakeGap`, `DedupeResult`, `CaseTypeConfig`, `TaskTemplate`). — **size S**,
  depends on: `platform-foundation#domain-model`, parallel: no.
  - Acceptance: types validate; immigration specifics live in `IntakeFields`/config,
    not in core `Matter`/`Contact` (CONVENTIONS §5); A-number format validated.
  - Focused tests: (a) valid email-lead and form-lead parse into `LeadPayload`;
    (b) invalid A-number rejected; (c) `CaseTypeConfig` round-trips from config;
    (d) unknown case_type falls back to "other/uncategorised".
- [ ] **1.2** Load `intake_mapping_schema`, `case_type_config[]`, `dedupe_config`
  from config (data-driven, CONVENTIONS §8). — **size S**, depends on: 1.1,
  parallel: yes.
  - Acceptance: configs load + validate at startup; each ships an
    `ASSUMPTION (confirm)` doc note; missing config fails fast with a clear error.
  - Focused tests: (a) well-formed config loads; (b) malformed config rejected;
    (c) per-case-type required-fields + task templates resolve by case_type.

## Group 2: Lead parsing & field mapping

- [ ] **2.1** `parse_lead` step: call `document.extract`, merge raw + extracted,
  apply `intake_mapping_schema` → `IntakeFields` with `field_confidence`. —
  **size M**, depends on: 1.2, `data-extraction#extract-tool`, parallel: no.
  - Acceptance: every required field for the resolved case type is present or
    emitted as an `IntakeGap` (mapping completeness); confidences carried through;
    case_type resolved from extraction/hint/default.
  - Focused tests: (a) email lead → fields mapped; (b) form lead → fields mapped;
    (c) missing required field → blocking gap; (d) low-confidence field → gap;
    (e) unresolved case_type → "other/uncategorised" + gap.

## Group 3: Dedupe logic

- [ ] **3.1** `dedupe_contact` step using `CRMConnector.find_contact` +
  `dedupe_config` (email-first, then name+DOB, then A-number). — **size M**,
  depends on: 1.2, `connector-framework#crm-port`, parallel: yes.
  - Acceptance: produces `DedupeResult` with decision in
    {reuse_contact, new_contact, ambiguous}; ambiguous never auto-merges and
    records a blocking gap; contact and matter dedupe evaluated independently.
  - Focused tests: (a) exact email match → reuse_contact; (b) no match →
    new_contact; (c) partial/conflicting match → ambiguous + gap; (d) returning
    client (existing contact, new matter) handled; (e) A-number match path.

## Group 4: Contact & matter creation

- [ ] **4.1** `create_contact` step (`contact.upsert`, write/confirm), idempotent
  on `(run_id, step)`; skip when `reuse_contact`. — **size S**, depends on: 3.1,
  `connector-framework#crm-port`, `workflow-orchestration#idempotency`, parallel: no.
  - Acceptance: exactly one contact per lead; reuse path performs no write; retry
    is a no-op.
  - Focused tests: (a) new contact created once; (b) reuse path writes nothing;
    (c) retry after transient error creates no duplicate.
- [ ] **4.2** `create_matter` step (`matter.create`, write/confirm), privileged on
  create, `practice_area` from case_type, intake metadata + `external_ids`
  attached. — **size M**, depends on: 4.1, `connector-framework#case-port`,
  parallel: no.
  - Acceptance: one matter linked to the contact; privileged flag set; immigration
    specifics carried as data not core fields; idempotent on `(run_id, step)`.
  - Focused tests: (a) matter created + linked; (b) privileged=true on create;
    (c) resume after contact step does not duplicate matter; (d) practice_area
    reflects case_type.

## Group 5: Deadlines & opening-task checklist

- [ ] **5.1** `compute_deadlines` step: `deadline.compute` then
  `deadline.schedule`, attach to `Matter.key_dates`. — **size S**, depends on:
  4.2, `deadline-engine#compute`, `deadline-engine#schedule`, parallel: yes.
  - Acceptance: deadlines computed only via the engine (no local date math);
    results attached + scheduled idempotently; engine errors park the run.
  - Focused tests: (a) deadlines attached for a case type with rules; (b) case
    type with no rules → empty key_dates, no error; (c) engine failure parks run.
- [ ] **5.2** `open_tasks` step: render the case type's `opening_tasks`
  `TaskTemplate`s into `Task` records (assignee by role). — **size M**, depends
  on: 4.2, 1.2, parallel: yes.
  - Acceptance: creates **exactly** the configured checklist for the case type
    (no missing, no extra — completeness invariant); idempotent on `(run_id, step)`;
    default checklist used for "other/uncategorised".
  - Focused tests: (a) each configured case type yields its exact task set; (b)
    re-run creates no duplicate tasks; (c) default checklist for uncategorised;
    (d) assignee role mapping applied.

## Group 6: Welcome draft, gate, send

- [ ] **6.1** `draft_welcome` step (`email.draft`, draft only) using
  `welcome_template_id` + matter/`IntakeFields` variables; recipient = client. —
  **size S**, depends on: 4.2, `connector-framework#email-port`, parallel: no.
  - Acceptance: a draft `Communication` exists; recipient ∈ matter participants;
    no send occurs; unresolved variables surface as gaps.
  - Focused tests: (a) draft created with correct recipient; (b) recipient not a
    participant → blocked; (c) missing template variable → gap.
- [ ] **6.2** `GATE:human_review` integration: park run in `awaiting_approval`;
  accept approval/rejection via all three channels (CONVENTIONS §4). — **size M**,
  depends on: 6.1, `workflow-orchestration#gates`, parallel: no.
  - Acceptance: run parks; blocking gaps prevent approval; approval/rejection
    recorded with approver identity; gate payload shows matter+gaps+draft.
  - Focused tests: (a) run parks at gate; (b) approval resumes; (c) rejection →
    terminal `rejected`; (d) blocking gap prevents approval; (e) approver recorded.
- [ ] **6.3** `send_welcome` step (`email.send`, gated/human), exactly-once via
  `(run_id, step)` key; no send before approval. — **size S**, depends on: 6.2,
  `connector-framework#email-port`, parallel: no.
  - Acceptance: send happens only post-approval, exactly once; ambiguous success
    never re-sends; rejection path never sends.
  - Focused tests: (a) send only after approval; (b) double-trigger sends once;
    (c) rejected run never sends; (d) ambiguous provider success not re-sent.

## Group 7: Tool, prompt, workflow registration

- [ ] **7.1** `intake.run` composite MCP tool (validate → start/locate run →
  snapshot). — **size M**, depends on: 2.1, 3.1, 4.2, parallel: no.
  - Acceptance: accepts email + form leads; returns `run_id` + snapshot; same
    idempotency key returns existing run; self-describing schema (FR-17).
  - Focused tests: (a) email lead starts run; (b) form lead starts run; (c)
    duplicate key returns same run with no new effects; (d) schema published.
- [ ] **7.2** Register the `intake` workflow definition (steps per PTD §7) with
  `workflow-orchestration`; wire webhook trigger to the same run. — **size S**,
  depends on: 7.1, `workflow-orchestration#engine`, parallel: no.
  - Acceptance: workflow registers with the exact 9-step sequence; webhook and
    tool triggers enqueue the same run type; resumes after restart.
  - Focused tests: (a) workflow registered with correct steps; (b) webhook
    trigger starts a run; (c) restart mid-run resumes from last step.
- [ ] **7.3** `intake_interview` prompt (versioned) for gap completion. —
  **size S**, depends on: 2.1, parallel: yes.
  - Acceptance: prompt asks only for unfilled/low-confidence required fields;
    never invents values; outputs map to field names.
  - Focused tests: (a) prompt targets only gap fields; (b) no gaps → no questions;
    (c) output maps to `IntakeFields`.

## Group 8: Idempotency, audit, observability hardening

- [ ] **8.1** Run-level idempotency key derivation + enforcement across re-submits
  and webhook duplicates. — **size M**, depends on: 7.2,
  `workflow-orchestration#idempotency`, parallel: no.
  - Acceptance: identical lead → one run, one contact, one matter, ≤ one send
    (NFR-3); webhook duplicate filtered by `provider_event_id` then run key.
  - Focused tests: (a) double `intake.run` → one run; (b) duplicate webhook → no
    second run; (c) full re-process creates no duplicate entities.
- [ ] **8.2** Audit coverage for every state-changing step + gate decision
  (hash-chained, PII-free). — **size S**, depends on: all write steps (4.x, 5.x,
  6.x), `platform-foundation#audit-log`, parallel: no.
  - Acceptance: each write/gate writes a chained audit record before completing;
    no PII values in records/logs; chain verifies; exportable.
  - Focused tests: (a) every write step emits an audit record; (b) gate decision
    audited with approver; (c) chain integrity verifies; (d) no PII in output.
- [ ] **8.3** Observability: metrics, traces, PII-scrubbed logs (spec §9). —
  **size S**, depends on: 7.2, `platform-foundation#observability`, parallel: yes.
  - Acceptance: all spec §9 metrics emitted; spans per step + cross-tool call;
    logs carry `run_id`, never PII values.
  - Focused tests: (a) metrics emitted per outcome; (b) trace spans present; (c)
    log scrubber drops PII fields.

---

## Dependency graph (intra-spec + cross-spec)

```
platform-foundation#domain-model ─► 1.1 ─► 1.2 ─┬─► 2.1 ──────────────┐
                                                 ├─► 3.1 ─► 4.1 ─► 4.2 ─┼─► 5.1
data-extraction#extract-tool ───────────────────┘                     ├─► 5.2
connector-framework#crm-port ───────► 3.1, 4.1                         ├─► 6.1 ─► 6.2 ─► 6.3
connector-framework#case-port ──────► 4.2                              │
connector-framework#email-port ─────► 6.1, 6.3                        │
deadline-engine#compute/#schedule ──► 5.1                              │
workflow-orchestration#idempotency ─► 4.1, 8.1                         │
2.1 + 3.1 + 4.2 ───────────────────► 7.1 ─► 7.2 ─► 8.1                 │
2.1 ───────────────────────────────► 7.3                              │
workflow-orchestration#gates ───────► 6.2                             │
workflow-orchestration#engine ──────► 7.2                             │
all write steps ───────────────────► 8.2                             │
platform-foundation#audit-log ─────► 8.2                             │
platform-foundation#observability ─► 8.3                             ▼
                                                                  (completed)
```

Cross-spec dependencies (must exist before the dependent intake task ships):
`platform-foundation` (domain model, audit, observability), `connector-framework`
(crm/case/email ports, idempotency, ConnectorError), `workflow-orchestration`
(engine, gates, idempotency), `data-extraction` (extract tool), `deadline-engine`
(compute, schedule). See CONVENTIONS §9.

## Definition of done (code + tests + docs)

A group is done only when:
- **Code** implements the steps/contracts in `spec.md` against domain ports
  (no vendor payloads, CONVENTIONS §3.10).
- **Tests** pass: the group's focused tests (2–8) **and** the relevant
  properties in `pbt-properties.md` (dedupe idempotency, no-send-before-gate,
  checklist completeness, mapping completeness).
- **Docs** updated: `docs/workflows/intake.md` (trigger, steps, gates, side
  effects, idempotency keys, failure handling — PTD §16); `intake.run` and
  `intake_interview` carry descriptions/schemas (FR-17); every
  `ASSUMPTION (confirm)` is listed for the firm to confirm.
