# Assumptions Pending Firm Confirmation

Every item below is a design decision baked into the current implementation as a
**reasonable default**, but flagged `ASSUMPTION (confirm)` because it needs
the firm's explicit sign-off before production go-live.

**Status values:** `pending` · `confirmed` · `rejected` · `superseded`

---

## How to use this file

When the firm confirms or changes an assumption:
1. Update the **Status** column.
2. Update the corresponding source file / config.
3. Remove the `ASSUMPTION (confirm)` comment from the code.
4. Record the confirmed value in the **Confirmed value / notes** column.

---

## Vendor & integration

| # | ID | Topic | Current default | Status | Confirmed value / notes |
|---|---|---|---|---|---|
| 1 | V-001 | Case-management vendor (v1) | Clio (reference adapter only) | pending | |
| 2 | V-002 | CRM vendor (v1) | Salesforce / Lawmatics (reference adapter only) | pending | |
| 3 | V-003 | Email vendor (v1) | Microsoft Graph or Gmail (reference adapter only) | pending | |
| 4 | V-004 | Document store (v1) | SharePoint/OneDrive or NetDocuments (reference adapter only) | pending | |
| 5 | V-005 | Webhook signature scheme per vendor | `x-hub-signature-256: sha256=<hex>` baseline | pending | |
| 6 | V-006 | Idempotency mechanism per vendor (header vs field) | `Idempotency-Key` header | pending | |
| 7 | V-007 | 409 on idempotent replay = success (not FatalError) | Yes, treated as success | pending | |
| 8 | V-008 | E-signature (DocuSign) — webhook source or its own port for v1? | Out of scope v1 | pending | |
| 9 | V-009 | LLM provider for extraction structuring | Not chosen; provider-agnostic | pending | |
| 10 | V-010 | Whether client data may leave the firm boundary for LLM inference | No (ALLOW_EXTERNAL_INFERENCE=false) | pending | |

---

## Hosting & deployment

| # | ID | Topic | Current default | Status | Confirmed value / notes |
|---|---|---|---|---|---|
| 11 | H-001 | Hosting target (firm infra / cloud / Zo) | Containerised, host-agnostic | pending | |
| 12 | H-002 | Secret store backend | `env` (dev) — Vault/KMS stubs exist | pending | |
| 13 | H-003 | Scheduler backend | Celery beat (chosen) | **confirmed** | Celery beat selected by operator 2026-05-31 |
| 14 | H-004 | Startup policy when migrations not at head | Refuse to serve | pending | |
| 15 | H-005 | Data residency granularity (country / region / single-tenant) | `us-east-1` default | pending | |
| 16 | H-006 | Whether LLM inference egress is constrained by residency setting | Yes (fail-closed) | pending | |

---

## Connectors & webhooks

| # | ID | Topic | Current default | Status | Confirmed value / notes |
|---|---|---|---|---|---|
| 17 | C-001 | Raw webhook body retention (yes/no; where) | Not retained | pending | |
| 18 | C-002 | Webhook dedup TTL | 7 days | pending | |
| 19 | C-003 | Max webhook body size | 1 MiB | pending | |
| 20 | C-004 | Replay window for HMAC timestamp check | 5 minutes | pending | |
| 21 | C-005 | Reconciliation sweep cadence | Not scheduled (alert-only baseline) | pending | |
| 22 | C-006 | Drift resolution policy (alert-only vs auto-resolve) | Alert-only | pending | |
| 23 | C-007 | Retry/backoff + circuit defaults per vendor SLA | 5 attempts, 0.5s–30s, 120s total | pending | |

---

## QC verification

| # | ID | Topic | Current default | Status | Confirmed value / notes |
|---|---|---|---|---|---|
| 24 | Q-001 | PacketKind enumeration | `email_send`, `document_generate`, `document_route`, `intake_finalize`, `generic` | pending | |
| 25 | Q-002 | All-skipped packet = `block` by calling workflow? | Proposed: yes | pending | |
| 26 | Q-003 | Attorney override of a QC `block` in v1? | No (v1 scope) | pending | |
| 27 | Q-004 | Canonical immigration identifier set for consistency matching | A-number, receipt number | pending | |
| 28 | Q-005 | `consistency` check: auto-normalise near-matches to `pass` or always `warn`? | Normalise then `warn` | pending | |

---

## Intake workflow

| # | ID | Topic | Current default | Status | Confirmed value / notes |
|---|---|---|---|---|---|
| 29 | I-001 | Exact intake field set per case type | See `intake/config.py` | pending | |
| 30 | I-002 | Case type enumeration | `family-based`, `employment-based`, `other/uncategorised` | pending | |
| 31 | I-003 | Per-type opening task checklists | See `intake/config.py` | pending | |
| 32 | I-004 | Write-confirm steps auto-confirmed inside authorised `intake.run`? | Auto within authorised run; send always gated | pending | |
| 33 | I-005 | Internal "matter opened" notification — in scope? Gated? | Internal-only, ungated | pending | |
| 34 | I-006 | Ambiguous dedupe — ever auto-merge with high confidence? | Always human disambiguation | pending | |
| 35 | I-007 | Dedupe thresholds (email exact=1.0, name+DOB=0.90, A-number=1.0) | See `intake/config.py` | pending | |

---

## Status-update emails

| # | ID | Topic | Current default | Status | Confirmed value / notes |
|---|---|---|---|---|---|
| 36 | S-001 | Trigger allow-list (which status values notify) | Empty (nothing triggers by default) | pending | |
| 37 | S-002 | Recipient role policy beyond the client (cc attorney / G-28?) | Client only | pending | |
| 38 | S-003 | Sweep cadence | Not scheduled | pending | |
| 39 | S-004 | Should status reverts suppress repeat notification? | No suppression | pending | |
| 40 | S-005 | Stale-but-approved draft — re-validate freshness at send? | No (snapshot approach) | pending | |
| 41 | S-006 | Per-matter opt-out flag for automated emails? | Not implemented | pending | |

---

## Document generation

| # | ID | Topic | Current default | Status | Confirmed value / notes |
|---|---|---|---|---|---|
| 42 | D-001 | Priority target forms for `form.prefill` | I-130, I-485, N-400, G-28 | pending | |
| 43 | D-002 | Default PDF engine (LibreOffice vs WeasyPrint) | Configurable; stub only | pending | |
| 44 | D-003 | Behaviour when PDF mandatory and conversion fails | `failed` | pending | |
| 45 | D-004 | Behaviour when QC unavailable (block vs warn) | `incomplete` (not `ready`) | pending | |
| 46 | D-005 | Default privilege classification for generated docs | `true` | pending | |

---

## Document routing

| # | ID | Topic | Current default | Status | Confirmed value / notes |
|---|---|---|---|---|---|
| 47 | R-001 | Document class enumeration | `engagement_letter`, `court_filing`, `id_document`, `correspondence`, `internal_memo`, `form_filing`, `unknown` | pending | |
| 48 | R-002 | Folder taxonomy and naming template | `{matter_reference}_{doc_class}_{date}_v{version}` | pending | |
| 49 | R-003 | Classification confidence threshold | 0.80 | pending | |
| 50 | R-004 | Recipient routing for v1 (filing+ACL only or also external share)? | Filing+ACL only (flag off) | pending | |
| 51 | R-005 | Internal vs external boundary for co-counsel and the privilege holder | External = recipient not in matter.participants | pending | |
| 52 | R-006 | `warn` privilege verdict — human confirmation for internal filing? | No gate for internal | pending | |

---

## Deadline engine

| # | ID | Topic | Current default | Status | Confirmed value / notes |
|---|---|---|---|---|---|
| 53 | DE-001 | Specific US immigration rule contents (RFE windows, EOIR dates, etc.) | Illustrative only; all `assumption_unconfirmed=true` | pending | |
| 54 | DE-002 | Holiday calendars beyond US federal | US federal only | pending | |
| 55 | DE-003 | Court-local cutoff handling for EOIR/DOS | UTC instants only | pending | |
| 56 | DE-004 | Staleness threshold for dead-man's-switch | 300 seconds | pending | |
| 57 | DE-005 | Calendar supported year range | 2020–2040 | pending | |

---

## RBAC & security

| # | ID | Topic | Current default | Status | Confirmed value / notes |
|---|---|---|---|---|---|
| 58 | SEC-001 | Exact RBAC role set | `attorney`, `paralegal`, `intake_coordinator`, `operations`, `agent_service`, `admin` | pending | |
| 59 | SEC-002 | Exact permission matrix | See `security/rbac.py` | pending | |
| 60 | SEC-003 | Agent service identity minimal grant set | `matter.read`, `contact.read`, `document.read`, `deadline.read`, `email.draft`, `workflow.run`, `workflow.status` | pending | |
| 61 | SEC-004 | Compliance posture (SOC 2 / HIPAA / jurisdictional residency) | SOC 2 path, GDPR-aware | pending | |

---

## Other

| # | ID | Topic | Current default | Status | Confirmed value / notes |
|---|---|---|---|---|---|
| 62 | O-001 | TriggerSink contract exact shape | Protocol stub in connector-framework | pending | |
| 63 | O-002 | Extraction default threshold | 0.80 | pending | |
| 64 | O-003 | Extraction max pages | 500 | pending | |
| 65 | O-004 | Extraction max bytes | 50 MB | pending | |

---

*Last updated: 2026-06-01. Source: code grep of `ASSUMPTION (confirm)` across `src/cam/**/*.py` plus open questions from `concept/PRD.md §11` and `concept/PTD.md §18`.*
