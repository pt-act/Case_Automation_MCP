# Document Routing — Technical Reference

## Tool: document.route
- Risk tier: `write (confirm)` for folder filing; `gated (human)` for external recipient
- Hard block: privileged document to external → `blocked` regardless of any approval (non-overridable)

## Pipeline
1. Derive idempotency key = sha256(document_id:checksum:recipient_id:routing_intent_version)[:16]
2. Idempotency check → return `duplicate` if already routed
3. Classify (rule → extraction → fallback/unknown)
4. Resolve destination (folder | review_queue); totality: every class has an entry
5. Derive canonical name from template
6. Build ACL = matter allowed-principal set ∩ class policy (always ⊆ matter set)
7. **Privilege gate** (non-overridable): privileged + external → `blocked`, no move
8. Review queue → `queued`, no move
9. Dry run → compute, no move
10. DocStoreConnector.move → `moved`
11. Audit record written before returning

## Privilege invariant
- `document.privileged=True` + external target → **always blocked**
- No approval token can override this
- Default-deny: unknown privilege + external → blocked
- Proven by PBT (test_document_routing.py::test_pbt_privilege_never_passes_external)

## Idempotency key
- sha256(document_id : checksum : recipient_id : routing_intent_version)[:16]
- Replay → `duplicate`, no second move
- New checksum (different version) → new key → new decision

## ACL correctness
- Always a subset of matter.allowed_principals ∩ class_permission_policy
- PBT asserts no broadening

## ASSUMPTION (confirm)
- Document class enumeration (current: engagement_letter, court_filing, id_document, correspondence, internal_memo, form_filing, unknown)
- Folder taxonomy and naming template
- Classification confidence threshold (default 0.80)
- Internal vs external boundary for co-counsel and the client themselves
- Whether `warn` privilege verdict requires human confirmation for internal filing
- Recipient routing for v1 (feature flag: off by default)
