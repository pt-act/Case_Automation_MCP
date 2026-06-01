# Client Intake Workflow — Technical Reference

## Trigger
- `intake.run` MCP tool (agent or API)
- Webhook: `lead.created` event from connector-framework

## Steps (exactly per PTD §7)
1. `parse_lead` — `document.extract` → map to IntakeFields; gaps for missing/low-confidence required fields
2. `dedupe_contact` — `CRMConnector.find_contact`; email-first, then A-number; ambiguous → blocking gap
3. `create_contact` — `contact.upsert`; skip if `reuse_contact`; idempotent on `(run_id, step)`
4. `create_matter` — `matter.create`; privileged on create; practice_area from case_type; idempotent
5. `compute_deadlines` — `deadline.compute` + `deadline.schedule` via engine; no local date math
6. `open_tasks` — render `CaseTypeConfig.opening_tasks` exactly; completeness invariant; stable task ids
7. `draft_welcome` — `email.draft`; recipient = client contact; no send
8. `GATE:human_review` — park in `awaiting_approval`; blocking gaps prevent approval; all 3 channels
9. `send_welcome` — `email.send`; exactly-once via `(run_id, step)` idem key; only post-approval

## Gates
- `human_review`: required_role=attorney; channels=[mcp, web, email]; TTL=24h
- Approval unlocks `send_welcome`; rejection is terminal (no send)

## Idempotency keys
- Run: `sha256(source_channel:provider_event_id)[:16]` (or email+name if no event id)
- Contact: `(run_id, create_contact)`
- Matter: `(run_id, create_matter)`
- Task: `uuid5(namespace, run_id:matter_id:title)`
- Send: `(run_id, send_welcome)`

## Failure handling
- ConnectorError(transient) → retry with backoff (engine)
- ConnectorError(auth/fatal) → park run; alert
- Ambiguous dedupe → blocking gap; gate cannot approve until resolved
- Deadline past-due-on-create → logged, flagged, run continues (not parked)
- Engine error on deadline compute → park run

## ASSUMPTION (confirm)
- Exact required fields per case type
- Case type enumeration (family-based, employment-based, etc.)
- Opening task templates per case type
- Dedupe thresholds and tie-break logic
- Welcome email template contents
