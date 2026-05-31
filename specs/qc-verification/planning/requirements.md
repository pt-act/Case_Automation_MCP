# Requirements — QC Verification

> Feature slug: `qc-verification` · Wave 3 · Trace anchors: PRD FR-13, FR-14,
> FR-15; NFR-2. Inherits `_shared/CONVENTIONS.md` (process, domain model, risk
> tiers, compliance baseline) and PTD §11 (the QC framework).

## 1. Context & problem

Today, quality control on outbound and externally-filed work is ad-hoc and
person-dependent (PRD §2, §7 Wave 3). Names, dates, and matter references drift
between documents in a packet; template variables ship unresolved; emails go to
the wrong recipient; privileged documents risk leaving the firm; computed
deadlines can be implausible; low-confidence extracted fields enter a system of
record unchecked. Each of these is a defect that can reach a client or a
government body (USCIS, EOIR, DOS), where the cost is reputational, financial,
or a privilege breach.

This feature provides a **composable verification check registry** that turns QC
from human vigilance into a deterministic, audited control. It is invoked by the
`qc.verify` MCP tool and consumed by workflows as a **gate input**: the
orchestration engine (feature `workflow-orchestration`) decides whether to park
a run; QC decides whether the content is acceptable. QC produces a verdict
(`pass | warn | fail` per check, aggregated to a packet verdict where **any
`fail` blocks the gate**) and attaches the full result set to the workflow run
and the hash-chained audit log (PTD §11, §12; PRD FR-15, NFR-2).

QC is a **safety control**. Bypassing or silently degrading it is a security
event, not merely a bug (see `security-audit-prep.md`).

## 2. In scope

- The **check registry abstraction**: a pluggable interface so checks are
  registered and discovered without modifying core (PRD FR-16).
- The **seven checks** (PTD §11): completeness, consistency, recipient
  integrity, attachment integrity, privilege, deadline sanity, extraction
  confidence.
- The **`pass | warn | fail` + reason** result model per check.
- **Aggregation**: combine per-check results into one packet verdict; **any
  `fail` ⇒ block** (`warn` never blocks but is always recorded).
- **Registry totality**: every registered check either runs and returns a
  result, or is explicitly skipped with a recorded, machine-readable reason —
  never silently dropped.
- **Attaching results** to the workflow run and to the audit log (PRD FR-15,
  NFR-2).
- The **`qc.verify` tool** (risk tier `read`, gate input) and the
  **`qc_checklist` prompt** (PTD §5.1, §5.3).

## 3. Out of scope

- The **workflows** that consume QC (`client-intake`, `status-update-emails`,
  `document-generation`, `document-routing`, etc.).
- The **gate mechanism itself** — parking/resuming a run on a verdict lives in
  `workflow-orchestration`. QC returns a verdict; it does not park runs.
- **Doc/email/CRM connector adapters** — QC reads the normalised domain model
  and a packet payload handed to it; it does not call vendor APIs.
- **Producing** the artefacts being checked (template rendering, extraction,
  routing) — those live in their own feature specs.
- **Remediation** of failures (re-drafting, re-routing) — QC reports, it does
  not fix.
- Deadline **computation rules** themselves — owned by `deadline-engine`; the
  deadline-sanity check only validates plausibility of already-computed dates.

## 4. Users / actors

| Actor | Interest in QC |
|---|---|
| **The AI Agent** | Calls `qc.verify` before proposing a gated action; uses `qc_checklist` to reason about a packet; needs a predictable, typed contract. |
| **Workflow orchestrator** (non-human) | Calls QC at gate steps; consumes the aggregate verdict to decide park/proceed. |
| **Fee-earner / Attorney** | Relies on QC to catch privilege and recipient defects before sign-off; reviews QC results in the approval surface. |
| **Paralegal / Case Manager** | Sees QC `warn`/`fail` reasons to know what to fix before resubmitting. |
| **Operations / Admin** | Adds new checks over time (FR-16); audits QC outcomes and bypass attempts. |

## 5. Functional requirements (trace to PRD FR-xx)

- **QR-1** Provide a registry that holds named, versioned checks and runs a
  selected subset against a **verification packet**. *(PRD FR-13, FR-16)*
- **QR-2** Implement the seven checks of PTD §11, each returning
  `pass | warn | fail` with a human-readable `reason` and structured
  `evidence`. *(PRD FR-13)*
- **QR-3** **Completeness** — all required template variables resolved and all
  required matter fields present for the declared packet kind. *(FR-13)*
- **QR-4** **Consistency** — client name, date of birth, and matter reference
  are identical across every document/communication in the packet. *(FR-13)*
- **QR-5** **Recipient integrity** — every external recipient of a
  communication is a participant of the matter (and resolves to the intended
  client). *(FR-13, NFR-1)*
- **QR-6** **Attachment integrity** — every attachment referenced in the packet
  body is present and is the correct version/checksum. *(FR-13)*
- **QR-7** **Privilege** — no document marked `privileged` is bound for an
  external recipient. This check must **never** return `pass` for a
  privileged-doc-to-external packet. *(FR-13, NFR-1)*
- **QR-8** **Deadline sanity** — computed dates are within plausible bounds and
  no deadline is past-due at creation time. *(FR-13)*
- **QR-9** **Extraction confidence** — any extracted field below the configured
  confidence threshold is surfaced; below a hard floor it fails. *(FR-13)*
- **QR-10** **Aggregate** the per-check results into one verdict: `block` if any
  check is `fail`; otherwise `pass_with_warnings` if any `warn`; otherwise
  `pass`. *(FR-13, FR-14)*
- **QR-11** Be **deterministic**: identical packet + identical check set +
  identical config ⇒ identical result (ordering-independent aggregate). *(FR-13)*
- **QR-12** **Totality**: every check selected to run produces exactly one of
  {result, explicit skip with reason}; no check is silently omitted. *(FR-16)*
- **QR-13** Attach the complete result set (per-check + aggregate) to the
  workflow run record and write an **audit record** before the verdict is
  considered final. *(FR-15, NFR-2)*
- **QR-14** Expose `qc.verify` as a `read`-tier MCP tool whose output is a
  typed `QCReport`; expose `qc_checklist` as a reusable prompt. *(FR-17)*
- **QR-15** Allow new checks to be **registered without modifying** the registry
  core or existing checks (entry-point / plugin registration). *(FR-16)*
- **QR-16** Each check **self-describes** (id, version, applicable packet kinds,
  severity policy, required inputs) for the MCP client and for docs CI. *(FR-17,
  FR-18)*

## 6. Non-functional requirements (trace to PRD NFR-xx)

- **QN-1 Auditability (NFR-2)** — 100% of QC verdicts and every bypass/override
  attempt are written to the hash-chained audit log before completion.
- **QN-2 Confidentiality / privilege (NFR-1)** — privilege and recipient checks
  are correctness-critical; QC reads but never transmits privileged content
  externally; no client content in logs/telemetry (PII-redacted reasons).
- **QN-3 Latency (NFR-5)** — `qc.verify` on a typical packet returns < 2s; QC is
  CPU/IO-light (no model inference of its own beyond reading provided fields).
  *ASSUMPTION (confirm): typical packet ≤ 25 documents / ≤ 200 template vars.*
- **QN-4 Observability (NFR-7)** — structured logs, a `qc_fail_rate` metric, and
  a trace span per check, correlated by `run_id`.
- **QN-5 Reliability (NFR-3)** — QC is pure/idempotent: re-running on the same
  packet has no side effect beyond an additional audit read-trace; verdicts do
  not depend on wall-clock except the deadline-sanity "now" reference, which is
  injected for determinism.
- **QN-6 Documentation freshness (NFR-10)** — every registered check ships a
  doc entry CI-checked against its self-description schema (FR-18).
- **QN-7 Graceful degradation (NFR-9)** — if a single check raises an
  unexpected error, it is recorded as a `fail` ("check errored") rather than
  crashing the whole run; the aggregate then blocks (fail-closed).
- **QN-8 Secret hygiene (NFR-8)** — QC holds no credentials; reads no secrets.

## 7. Dependencies (other feature specs, external systems)

- **`platform-foundation`** — domain model (`Matter`, `Document`,
  `Communication`, `Contact`, `Deadline`), the hash-chained audit-log writer,
  config/feature-flag loading, observability primitives.
- **`workflow-orchestration`** — owns the run record QC attaches to and the gate
  that consumes QC's verdict. QC is called *by* it, never the reverse.
- **Upstream producers** (data consumed, not depended on as code): rendered
  documents from `document-generation`; extracted fields + confidences from
  `data-extraction`; computed dates from `deadline-engine`. QC receives their
  outputs inside the packet; it does not import them.
- **External systems:** none directly. QC operates on the normalised domain
  model and an in-memory/persisted packet; no connector calls.

## 8. Assumptions & open questions

- **ASSUMPTION (confirm):** The `warn` vs `fail` **severity threshold per
  check** is configurable per workflow/risk tier; defaults proposed in
  `spec.md` §7. Firm to confirm which checks may only `warn` vs must `fail`.
- **ASSUMPTION (confirm):** Extraction-confidence has two thresholds — a `warn`
  threshold (default 0.85) and a hard `fail` floor (default 0.60). Values to be
  confirmed against the firm's risk appetite and the extractor's calibration.
- **ASSUMPTION (confirm):** Date-of-birth is available on the matter/client for
  the consistency check. The domain `Contact` model does not currently carry
  `dob`; if unavailable, the consistency check compares on the fields that *are*
  present (name, matter reference) and records DOB as `skipped (field absent)`.
  Proposed: add `dob` as a shared field via `platform-foundation`.
- **ASSUMPTION (confirm):** "Plausible bounds" for deadline sanity = not in the
  past at creation, and not more than *N* years in the future (proposed N = 15,
  to accommodate long immigration timelines); N is config.
- **ASSUMPTION (confirm):** A document is "external-bound" when the packet's
  intended recipients include any contact whose `role`/domain is outside the
  firm; the precise internal-vs-external classification rule is provided by the
  packet (set by the calling workflow), not inferred by QC. QC trusts and audits
  that flag but also re-derives it from recipient domains where possible.
- **ASSUMPTION (confirm):** Immigration-specific identity fields used for
  cross-document consistency (e.g. A-number, receipt number) are matched **if
  present** in the packet; the canonical set of immigration identifiers to
  enforce is unconfirmed (treat as config-driven, per CONVENTIONS §8).
- **Open question:** Does a workflow ever need to proceed despite a `fail` via an
  explicit, audited attorney override? If yes, the override is implemented and
  gated in `workflow-orchestration`, not QC — QC's verdict is immutable. Confirm
  the override policy.
- **Open question:** Should `qc.verify` support a **dry-run / explain** mode that
  returns reasons without writing an audit record? Proposed: no — every
  invocation is audited (QN-1); a non-auditing preview would create a blind spot.

## 9. Acceptance criteria (testable checklist)

- [ ] Given a packet with one `fail` check and any number of `pass`/`warn`
      checks, `qc.verify` returns aggregate verdict `block`.
- [ ] Given a packet where all checks `pass`, the aggregate is `pass`; with at
      least one `warn` and no `fail`, the aggregate is `pass_with_warnings`.
- [ ] A privileged document addressed to an external recipient yields the
      privilege check = `fail` and aggregate = `block`, in 100% of cases.
- [ ] A recipient not present in `Matter.participants` yields recipient
      integrity = `fail`.
- [ ] Two documents in a packet with differing client name (or matter reference,
      or DOB when present) yield consistency = `fail`.
- [ ] An unresolved template variable (e.g. `{{ client_name }}` left literal or
      empty) yields completeness = `fail`.
- [ ] A referenced attachment that is absent, or present at the wrong checksum,
      yields attachment integrity = `fail`.
- [ ] A deadline with `due_at` before the injected "now", or beyond the
      configured horizon, yields deadline sanity = `fail`.
- [ ] An extracted field below the hard floor yields extraction confidence =
      `fail`; between floor and warn-threshold yields `warn`.
- [ ] Running `qc.verify` twice on identical input returns byte-identical
      per-check verdicts and aggregate (determinism).
- [ ] Every selected check appears in the report exactly once, as a result or an
      explicit `skipped` with reason (totality).
- [ ] A check raising an unexpected exception is recorded as `fail`
      ("check errored") and the aggregate blocks (fail-closed).
- [ ] A new check can be registered via the plugin mechanism and appears in
      `qc.verify` output without any edit to the registry or existing checks.
- [ ] Every `qc.verify` call writes exactly one audit record (verdict + per-check
      summary) before returning, linked to `run_id`.
- [ ] `qc_checklist` prompt enumerates the applicable checks for the packet kind.
