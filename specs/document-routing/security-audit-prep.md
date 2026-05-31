# Security Audit Prep — Document Routing

> Feature slug: `document-routing` · Traces: FR-11, NFR-1 · Inherits the security
> baseline in `CONVENTIONS.md` §7 and `PTD.md` §12. This file re-checks this
> feature's own surfaces against that floor. Security focus: **privilege
> enforcement (critical)**, ACL correctness, audit of every routing decision, PII.

## Sensitive surfaces (data, actions, external calls)

| Surface | Why sensitive |
|---|---|
| `document.route` MCP tool input | Accepts a `document_id` + optional `recipient_id`; the recipient field is the external-exposure vector. |
| Privilege flag (`Document.privileged`) | Drives the hard gate; mis-read = privilege breach. Unknown must default-deny. |
| `DocStoreConnector.move(id, folder, acl)` | Mutates location + permissions of a (possibly privileged, PII-laden) document. |
| Recipient routing / external share path | The only path that can send a document outside the matter boundary. |
| Computed ACL | Controls who can read the document; a broadened ACL is an access-control failure. |
| Classification inputs from `data-extraction` | May contain immigration PII (A-numbers, passports, status, country-of-origin). |
| `routing_decision` table + audit records | Persist routing metadata; must hold ids/digests only, never content/PII. |
| Folder-map / naming / class-policy config | Mis-config can map a privileged class to an external/shared folder. |

## Threats & mitigations (map to NFR-1, 2, 4, 6, 8)

| Threat | Maps to | Mitigation |
|---|---|---|
| Privileged document routed/forwarded to an external recipient | **NFR-1** | Hard, non-overridable privilege gate via `qc-verification`; default-deny on unknown privilege; PBT P1 proves the invariant; blocked outcome performs no move/share. |
| ACL grants access beyond the matter's allowed set | NFR-1 | `AclBuilder` builds ACL as subset by construction; out-of-set principals dropped + audited; PBT P4. |
| A routing decision happens with no audit trail | NFR-2 | Every outcome audited to the hash-chained table **before** return; covered by focused + chain-integrity tests. |
| Automated external routing without human oversight | NFR-4 | External recipient path is `gated (human)`; privileged-external is hard-blocked (cannot be approved); ambiguous → `review_queue`. |
| Document moved/copied outside configured residency region | NFR-6 | DocStore port assumed region-scoped; routing never crosses regions; residency documented. |
| Secrets/tokens leak via routing code or logs | NFR-8 | No creds in code; tokens from central store; never logged; no env-dumping. |
| PII (A-number, passport, status) written to logs/telemetry/decision rows | NFR-1, NFR-6 | PII-scrubbed structlog; decision/audit rows store ids + digests only; no document content in traces. |
| Idempotency bypass causes duplicate filing/share | NFR-3 | Unique idempotency-key constraint + lock; replay → `duplicate`; PBT P2. |
| Spoofed/mis-set privilege flag from upstream | NFR-1 | Routing does not trust unknown as non-privileged; default-deny; ASSUMPTION (confirm) on privilege provenance flagged in `requirements.md` §8. |
| Connector error leaves a partial/ambiguous filing | NFR-3, NFR-9 | `ConnectorError` taxonomy → retry/park/fail; idempotency key makes retries no-ops; no partial move; failure audited. |

## AuthZ & privilege checks

- **Privilege gate (critical):** invoked for **every** route with any external
  target; delegates the privilege/recipient-integrity test to `qc-verification`
  (PTD §11); a `fail` blocks unconditionally and cannot be released by a normal
  `gated (human)` approval (`tasks.md` 5.2). Default-deny when privilege is unknown.
- **ACL ⊆ matter allowed set:** enforced by construction in `AclBuilder`; verified
  by PBT P4; class policy may only narrow, never widen.
- **RBAC:** routing runs under the constrained service identity (PTD §12); matter
  allowed-principal lookups come from `platform-foundation` RBAC, not invented here.
- **External boundary:** `is_external` predicate per `requirements.md` §8
  ASSUMPTION; co-counsel and the client-as-privilege-holder cases flagged for
  confirmation and default to deny until confirmed.

## Audit log coverage

- Every routing decision **and** outcome (`moved | queued | blocked | duplicate |
  dry_run`) writes an append-only, hash-chained audit record **before** the call
  returns (NFR-2): `actor, action="document.route", inputs (ids/refs only),
  outputs (decision summary: class, destination kind, acl digest, gate verdict),
  approval, timestamp, run_id`.
- Privilege blocks are explicitly audited with a reason code (no content) and also
  emit a metric/alert hook.
- ACL principal drops (out-of-set) are audited.
- Audit records contain **no** document content or raw PII — ids and digests only.

## PII handling & residency

- Immigration PII (A-numbers, passports, biometrics, immigration status,
  country-of-origin) flows through classification inputs but is **never** persisted
  in routing/audit rows or emitted to logs/traces (PII-scrubbed structlog,
  CONVENTIONS §7).
- Document content is never copied into telemetry; only `document_id`, `checksum`,
  and an `acl_digest`.
- Residency: documents are moved only within the configured region via the
  region-scoped DocStore port (NFR-6); cross-region moves are out of scope and
  must be blocked by deployment config.
- GDPR-aware: routing metadata is minimal and tied to ids, supporting
  data-subject deletion via `platform-foundation` (no PII duplicated here).

## Pre-audit checklist

- [ ] Privilege hard gate invoked on every external-target route; privileged/unknown
      + external → `blocked` with no move/share (PBT P1 green).
- [ ] No `gated (human)` approval path can release a privileged doc externally
      (`tasks.md` 5.2 test green).
- [ ] ACL ⊆ matter allowed set for all inputs (PBT P4 green); empty allowed set →
      empty ACL (no default-open).
- [ ] Classification → destination totality holds over the full class enum (PBT P3).
- [ ] Idempotency: duplicate request → exactly one move (PBT P2); unique constraint
      in place.
- [ ] Every outcome audited before return; audit hash-chain verified; no PII/content
      in records or logs.
- [ ] No secrets in code/logs; no env-dumping; tokens from central store.
- [ ] Residency: DocStore port region-scoped; no cross-region move possible by config.
- [ ] `ConnectorError` paths never leave a partial filing; failures audited.
- [ ] All `ASSUMPTION (confirm)` items (external boundary, privilege provenance,
      folder-map, naming, class enum, confidence threshold) tracked and confirmed
      with the firm before go-live.
- [ ] Metrics/alerts for privilege blocks and parked runs wired to ops.
