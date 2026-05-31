# Requirements — Document Routing

> Feature slug: `document-routing` · Wave 2 · Traces: FR-11, NFR-1
> Inherits `specs/_shared/CONVENTIONS.md` (process, domain model, risk tiers,
> compliance baseline) and `concept/PTD.md` (architecture, ports, QC framework).
> This file is the "what & why"; behaviour lives in `spec.md`, steps in `tasks.md`.

## 1. Context & problem

Inbound and generated documents (IDs, USCIS notices, court filings, signed
forms, generated letters) arrive scattered across email attachments and ad-hoc
folders. Today a human reads each one, decides which matter and folder it
belongs to, renames it, sets who can see it, and may forward it to a recipient.
This is slow, inconsistent, and — critically for an immigration practice —
carries **privilege/confidentiality risk**: a privileged document can be filed
or forwarded where an external party can read it (PRD §2 "Document routing"
pain; Risk row "Confidentiality / privilege breach").

Document Routing makes this a first-class, repeatable, audited workflow: given a
document (already stored, or generated upstream), it **classifies** it, derives
a **canonical name**, **resolves the destination matter + folder**, assigns an
**ACL**, enforces a **privilege-aware hard gate** (a privileged document is never
routed to an external recipient), and performs the **move** via the
`DocStoreConnector` port — idempotently, with every decision audited.

This is the routing **decision + enforcement + move-orchestration** layer only.
It depends on `data-extraction` for classification signals and on
`qc-verification` for the privilege check; it does not own the doc store
adapter, extraction internals, QC internals, or document generation.

## 2. In scope

- **Classification** of a document into a class (e.g. `engagement_letter`,
  `uscis_notice`, `court_filing`, `identity_document`) using `data-extraction`
  outputs plus a configurable, data-driven **classification rule set**.
- **Naming**: derive a canonical, deterministic file name from a configurable
  naming-convention template (matter ref, class, date, version).
- **Matter / folder resolution**: resolve the target `Matter` and destination
  folder from the document, classification, and configurable folder-map; emit an
  explicit **review-queue** destination when resolution is ambiguous/unknown.
- **ACL / permission assignment**: compute the access-control list for the
  document from the resolved matter's allowed principal set + class policy.
- **Privilege-aware routing enforcement**: a **hard gate** (backed by the
  `qc-verification` privilege check) that blocks routing a privileged document
  to any external recipient/destination (FR-11, NFR-1, PTD §11 Privilege check).
- **Move** of the document to its destination folder with the computed ACL via
  the `DocStoreConnector.move` port (PTD §6).
- **Optional recipient routing**: when a routing request names a recipient
  (e.g. forward a copy), apply the same privilege gate before any external share.
- **Idempotency**: the same document is filed exactly once; repeats are no-ops.
- The **`document.route` MCP tool** (typed input/output, self-describing schema).
- **Audit** of every routing decision and outcome.

## 3. Out of scope

- The **doc store adapter** itself (a `DocStoreConnector` adapter is
  `connector-framework` + a future vendor adapter). We depend on the **port**.
- **Extraction internals** (text/OCR/LLM structuring) — owned by `data-extraction`.
- **QC internals** — the privilege check logic/registry is owned by
  `qc-verification`; we **call** it and enforce its verdict.
- **Document generation** — owned by `document-generation`; routing may be
  invoked *after* generation but does not generate.
- Email **send** mechanics (the `EmailConnector.send` path / approval-gated send)
  — owned by `status-update-emails` / `connector-framework`; routing only decides
  and enforces *whether* a privileged doc may go to a recipient.
- Defining the firm's specific immigration folder taxonomy and naming convention
  (these are **config**; see Assumptions).

## 4. Users / actors

| Actor | Interest in this feature |
|---|---|
| **Paralegal / Case Manager** | Inbound docs land in the right matter/folder, correctly named, without manual filing. |
| **Fee-earner / Attorney** | Privilege is never broken; every routing decision is auditable and reversible-by-record. |
| **Operations / Admin** | Folder-map, naming convention, and class policies are configurable, not code changes. |
| **The AI Agent** | Calls `document.route` with a predictable, safe contract; gets a clear destination or a review-queue result. |
| **Workflow Orchestrator** | Invokes routing as a step (e.g. after `document.generate` or a doc-received webhook). |

## 5. Functional requirements (trace to PRD FR-xx)

- **DR-FR-1** Classify a document into exactly one class using `data-extraction`
  fields + a configurable classification rule set; attach the class and a
  confidence/source to the result. *(FR-11, FR-7)*
- **DR-FR-2** Classification → destination mapping is **total**: every class maps
  to a concrete destination folder **or** an explicit `review_queue` destination;
  no class is silently unhandled. *(FR-11)*
- **DR-FR-3** Derive a canonical document name from a configurable naming template
  over domain fields (matter reference, class, date, version). *(FR-11, FR-5)*
- **DR-FR-4** Resolve the target `Matter` and destination folder from the
  document + classification + folder-map; unresolved → `review_queue`. *(FR-11)*
- **DR-FR-5** Compute an ACL for the document from the resolved matter's allowed
  principal set and the class's permission policy; the ACL **never grants access
  beyond the matter's allowed set**. *(FR-11, NFR-1)*
- **DR-FR-6** Enforce a **privilege hard gate**: if the document is privileged and
  the routing target is (or includes) an external recipient/destination, the
  route is **blocked** with a typed refusal — never partially executed. *(FR-11,
  FR-13/§11 Privilege, NFR-1)*
- **DR-FR-7** Perform the move via `DocStoreConnector.move(id, folder, acl)`,
  forwarding an idempotency key so a repeat does not re-file. *(FR-11, FR-4)*
- **DR-FR-8** Be idempotent: routing the same document with the same routing
  intent yields the same single filed outcome; a duplicate request returns the
  prior result without a second move. *(FR-4, NFR-3)*
- **DR-FR-9** Expose `document.route` as a self-describing MCP tool: typed input
  (document ref + optional recipient + options) → typed output (decision,
  destination, ACL summary, status). *(FR-17)*
- **DR-FR-10** Write an immutable audit record for **every** routing decision and
  its outcome (classified, named, resolved, ACL, gate verdict, moved/blocked/
  queued), before the action is considered complete. *(FR-15)*
- **DR-FR-11** When resolution is ambiguous, low-confidence, or the privilege gate
  blocks an external target, route to `review_queue` / return a blocked result
  with reasons — never guess into a client-facing destination. *(FR-11, FR-14)*

## 6. Non-functional requirements (trace to PRD NFR-xx)

- **DR-NFR-1 Privilege/confidentiality (NFR-1).** A privileged document is **never**
  routed to an external recipient; ACLs are least-privilege and never broaden
  beyond the matter's allowed set; immigration PII (A-numbers, passports,
  biometrics, status, country-of-origin) is never written to logs/telemetry.
- **DR-NFR-2 Auditability (NFR-2).** 100% of routing decisions/outcomes logged to
  the append-only, hash-chained audit table before completion.
- **DR-NFR-3 Reliability / idempotency (NFR-3).** Safe retries; no duplicate
  filings; the move is keyed so re-runs are no-ops.
- **DR-NFR-4 Human control (NFR-4).** External/irreversible routing (a privileged
  doc to an external recipient) cannot proceed automatically — it is hard-blocked;
  ambiguous routing parks in `review_queue` for a human.
- **DR-NFR-5 Latency (NFR-5).** A single `document.route` decision (excluding the
  underlying extraction job) returns < 2s typical; large/slow moves run async with
  status. *(ASSUMPTION (confirm) on async threshold.)*
- **DR-NFR-6 Residency / compliance (NFR-6).** Routing honours configurable data
  residency; no document content leaves the configured region; PII handling
  documented in `security-audit-prep.md`.
- **DR-NFR-7 Observability (NFR-7).** Structured logs/metrics/traces per route run
  (class distribution, review-queue rate, privilege-block count, move latency).
- **DR-NFR-8 Graceful degradation (NFR-9).** If the doc store or extraction is
  unavailable, routing parks/returns a typed transient error and never loses or
  mis-files the document.

## 7. Dependencies (other feature specs, external systems)

- **`platform-foundation`** — domain model (`Document`, `Matter`, `ACL`),
  persistence, audit log, config/secrets, observability baseline.
- **`connector-framework`** — `DocStoreConnector` port (`move`), `ConnectorError`
  taxonomy, idempotency-key plumbing, webhook ingestion (doc-received trigger).
- **`qc-verification`** — the **privilege check** (and recipient-integrity check)
  invoked as the hard gate; we consume its `pass | warn | fail` verdict.
- **`data-extraction`** — structured fields + per-field confidence used as
  classification signals.
- **External:** the document store (via the port only — vendor-agnostic) and the
  configured object store / Postgres for state + audit.

## 8. Assumptions & open questions (each `ASSUMPTION (confirm)`)

- **ASSUMPTION (confirm):** The firm's **folder taxonomy** (per-matter folder
  structure, e.g. `Correspondence`, `USCIS`, `Identity`, `Privileged`, `Filings`)
  is supplied as **config**; this spec ships a default placeholder map only.
- **ASSUMPTION (confirm):** The **naming convention** is
  `{matter_reference}_{class}_{yyyymmdd}_v{version}` with a configurable template;
  exact tokens/order to be confirmed by the firm.
- **ASSUMPTION (confirm):** The **document class enumeration** (e.g.
  `engagement_letter`, `uscis_notice`, `court_filing`, `identity_document`,
  `g28_notice_of_appearance`, `rfe_noid`, `unknown`) is configurable; the initial
  set and which immigration forms (I-130/I-485/N-400/G-28, etc.) map to which
  class is unconfirmed.
- **ASSUMPTION (confirm):** "External recipient" is determined by the
  recipient/destination **not** being in the matter's allowed-principal set
  *and* being outside the firm's domain/principal directory; the precise
  internal-vs-external boundary (e.g. co-counsel, the client themselves) must be
  confirmed — note the client is the privilege holder, so client-directed
  sharing rules need explicit confirmation.
- **ASSUMPTION (confirm):** **Privilege determination** is taken from
  `Document.privileged` (set by generation/extraction/QC); routing does not
  re-derive privilege, it enforces it. Default-deny: if privilege is unknown,
  treat as privileged for external-routing purposes.
- **ASSUMPTION (confirm):** Classification **confidence threshold** below which a
  document goes to `review_queue` defaults to 0.80; firm to confirm.
- **ASSUMPTION (confirm):** ACL principal model (roles vs named users vs groups)
  comes from `platform-foundation` RBAC; routing composes ACLs from the matter's
  allowed set and class policy, it does not define the RBAC model.
- **Open question:** Is recipient routing (forwarding a copy out) in v1 of this
  workflow, or only matter/folder filing + ACL? (Spec covers both but recipient
  routing can be feature-flagged off.)

## 9. Acceptance criteria (testable checklist)

- [ ] Given a document with extraction fields, `document.route` returns a single
      `class` from the configured enumeration (or `unknown`).
- [ ] Every class in the enumeration maps to a concrete folder **or**
      `review_queue` (mapping totality verified by test over the full enum).
- [ ] The derived name matches the configured naming template exactly for given
      inputs (deterministic; same inputs → same name).
- [ ] A resolvable document is filed to the correct matter/folder; an unresolvable
      or low-confidence one returns `review_queue` (no client-facing destination).
- [ ] The computed ACL is a subset of the matter's allowed-principal set for every
      generated case (no broadening).
- [ ] A privileged document with an external recipient/destination returns a typed
      **blocked** result and performs **no** move/share.
- [ ] Calling `document.route` twice for the same document+intent performs exactly
      one move; the second call returns the prior result (409/duplicate semantics).
- [ ] Every call writes an audit record (decision + outcome) before returning; the
      record contains no raw PII/document content.
- [ ] Doc store unavailable → typed transient error / parked run; no partial or
      lost filing.
- [ ] `document.route` exposes a complete, self-describing schema to the MCP client.
