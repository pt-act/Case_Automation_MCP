# Status-Update-Emails Workflow — Technical Reference

## Trigger paths (both converge on one run)
1. **Webhook**: `matter.status_changed` event → `handle_status_change_event()`
2. **Sweep**: periodic `sweep_matters()` compares last-known vs Case port current status

Both derive the same `change_id` for the same transition → one run per change.

## Steps
1. `detect_change` — check allow-list; skip if `to_status` not in config
2. `compute_delta` — persist last-known status for future sweeps
3. `resolve_recipients` — derive from matter.participants; missing email → gap
4. `draft_email` — `email.draft`; prompt version recorded on run; no send
5. `qc_recipient_integrity` — `qc.verify` recipient check; `fail` → blocked
6. `GATE:human_review` — parks run; approve/reject via MCP/web/email
7. `send_email` — `email.send(draft_id, idem_key)` where idem_key = `status_update:{change_id}`
8. `record_outcome` — mark `change_id` delivered; final audit record

## Idempotency
- `change_id` = `sha256(matter_id:from_status:to_status:discriminator)[:16]`
- Run-level idem key = `change_id` (webhook: provider_event_id; sweep: transition_counter)
- Send idem key = `status_update:{change_id}` → exactly one message per change
- `delivered` flag set only AFTER send + audit write (crash-safe)

## QC gate
- `fail` → run stays blocked; gate cannot approve into a send
- `warn` → surfaced in gate UI; approver decides
- `pass` → normal gate flow

## ConnectorError handling
- `auth`/`fatal` → park run; `change_id` not marked delivered
- `transient`/`rate-limit` → retry with backoff; same `idem_key` reused
- `not-found` (draft missing at send) → park blocked; no silent re-create

## ASSUMPTION (confirm)
- Trigger allow-list (which status values notify)
- Recipient role policy beyond the client (cc attorney/G-28?)
- Sweep cadence and webhook-primary/sweep-safety-net relationship
- Whether status reverts suppress a repeat notification
- Whether stale draft re-validated for freshness at send gate
- `change_id` composition once vendor status semantics confirmed
- Per-matter opt-out flag
