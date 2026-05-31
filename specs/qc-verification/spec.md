# Spec — QC Verification

> Slug: `qc-verification` · Wave 3 · Traces: PRD FR-13/14/15, NFR-1/2/5/7.
> Inherits `_shared/CONVENTIONS.md` and PTD §11 (QC framework), §5 (MCP
> surface), §12 (audit). Describes the **what** (behaviour, interfaces, data,
> edge cases). Implementation steps are in `tasks.md`.

## 1. Summary

QC Verification is a **pluggable check registry** plus seven concrete checks,
exposed as the `read`-tier MCP tool `qc.verify` and the `qc_checklist` prompt.
Given a **verification packet** (a normalised bundle of the artefacts a workflow
is about to act on), the registry runs the applicable checks, each yielding
`pass | warn | fail` + reason + evidence. Results are aggregated with a strict
rule — **any `fail` blocks** — into a `QCReport`. The report is attached to the
workflow run and an audit record is written before the verdict is returned. QC
is a pure, deterministic, fail-closed safety control: it computes a verdict; it
does not mutate artefacts, call vendors, or park runs.

## 2. Scope & out-of-scope

**In scope:** the registry abstraction; the seven checks; the
`pass/warn/fail` + aggregation model (any `fail` ⇒ `block`); registry totality
(run-or-skip-with-reason); attaching results to run + audit; `qc.verify` tool;
`qc_checklist` prompt; per-check self-description for MCP/docs CI.

**Out of scope (restate):** the workflows that call QC; the gate park/resume
mechanism (`workflow-orchestration`); connector adapters and any vendor I/O;
producing the artefacts (rendering, extraction, routing, deadline computation);
remediation of failures; attorney override of a verdict (lives in
`workflow-orchestration`, audited there).

## 3. Domain types used / introduced

**Used (from `platform-foundation` / PTD §4, do not redefine):** `Matter`,
`Document`, `Communication`, `Contact`, `Deadline`, `Task`.

**Introduced (QC-local Pydantic v2 types; proposed for `platform-foundation`
review if shared):**

- `VerificationPacket` — the unit QC checks. Fields:
  - `packet_id: str`, `run_id: str`, `kind: PacketKind` (e.g.
    `email_send`, `document_generate`, `document_route`, `intake_finalize` —
    enum, **ASSUMPTION (confirm)** the kind list)
  - `matter: Matter`
  - `documents: list[Document]`
  - `communications: list[Communication]`
  - `deadlines: list[Deadline]`
  - `extracted_fields: list[ExtractedField]` (id, name, value, `confidence: float`, `source_doc_id`)
  - `intended_recipients: list[Contact]`
  - `template_bindings: list[TemplateBinding]` (template name, `required_vars`, `resolved_vars: dict[str,str|None]`)
  - `attachment_refs: list[AttachmentRef]` (referenced name, `expected_checksum`, `expected_version`)
  - `external_bound: bool` (set by caller; QC re-derives + cross-checks)
  - `now: datetime` (injected reference time — determinism, QN-5)
  - `config_snapshot: QCConfig` (thresholds/severity in force for this run)
- `CheckResult` — `check_id`, `check_version`, `verdict: Verdict`, `reason: str`
  (PII-redacted), `evidence: dict` (structured, redacted), `severity_applied`,
  `duration_ms`.
- `Verdict = Literal["pass","warn","fail","skipped"]`.
- `SkipReason = Literal["not_applicable","field_absent","disabled","unsupported_packet_kind"]`.
- `QCReport` — `packet_id`, `run_id`, `results: list[CheckResult]`,
  `aggregate: Aggregate`, `checks_selected: list[str]`, `checks_skipped: dict[str,SkipReason]`,
  `created_at`, `config_fingerprint: str`.
- `Aggregate = Literal["pass","pass_with_warnings","block"]`.
- `QCConfig` — per-check enable flag, per-check severity policy (which verdicts a
  check may emit), and numeric thresholds (extraction warn/fail, deadline
  horizon). Loaded via `platform-foundation` config + per-workflow feature flag.

## 4. Interfaces (MCP tools/resources/prompts, ports, internal APIs)

### 4.1 MCP tool — `qc.verify`
- **Risk tier:** `read` (gate input; no side effects beyond an audit *read/verdict* record). *(PTD §5.1)*
- **Input:** `QCVerifyInput { packet: VerificationPacket, checks: list[str] | None }`
  — `checks` omitted ⇒ all checks applicable to `packet.kind`; if provided, the
  named subset (still subject to applicability + totality).
- **Output:** `QCReport` (typed; self-describing schema → FR-17).
- **Contract:** pure w.r.t. artefacts; writes exactly one audit record; never
  raises to the caller for a check-level error (those become `fail`).

### 4.2 MCP prompt — `qc_checklist`
- A reusable, versioned reasoning scaffold (PTD §5.3) that, given a packet kind,
  enumerates the applicable checks, what each verifies, and the
  any-`fail`-blocks rule. Used by the agent to reason about a packet and to
  explain a `block` to a human in the approval surface. Contains **no** client
  content; it is a template, populated at call time.

### 4.3 Internal API — the registry (the FR-16 extension point)
```python
class Check(Protocol):
    id: str                     # stable, unique, e.g. "privilege"
    version: str                # semver; bump on behaviour change
    applies_to: frozenset[PacketKind] | None   # None = all kinds
    severity_policy: SeverityPolicy            # which verdicts allowed + warn/fail mapping
    required_inputs: frozenset[str]            # packet fields it reads
    def describe(self) -> CheckDescriptor: ...
    def run(self, packet: VerificationPacket, cfg: QCConfig) -> CheckResult: ...

class CheckRegistry:
    def register(self, check: Check) -> None: ...      # idempotent by id+version
    def get(self, check_id: str) -> Check: ...
    def applicable(self, kind: PacketKind) -> list[Check]: ...
    def run(self, packet, checks: list[str] | None, cfg) -> QCReport: ...
```
- **Registration** is via Python entry points / a decorator
  (`@register_check`), so a new check module is discovered at startup without
  editing the registry or other checks (FR-16). Duplicate `id` registration is a
  startup error (no silent shadowing).
- `run` enforces **totality**: for every selected check it appends either a
  `CheckResult` or a `skipped` entry with a `SkipReason`.

### 4.4 The seven checks (behavioural contracts)
| id | Verifies | `fail` when | may `warn` |
|---|---|---|---|
| `completeness` | required template vars resolved; required matter fields present | any required var unresolved/empty or required field missing | optional var missing (config) |
| `consistency` | client name / DOB / matter ref identical across packet | any mismatch across documents/comms | near-match (whitespace/case) → `warn` then normalise |
| `recipient_integrity` | every external recipient ∈ `matter.participants` | a recipient not in participants | recipient in participants but role mismatch |
| `attachment_integrity` | referenced attachments present & correct version/checksum | missing attachment or checksum/version mismatch | version newer than referenced (config) |
| `privilege` | no `privileged` doc to an external recipient | any privileged doc in an external-bound packet | — (privilege never downgrades to warn) |
| `deadline_sanity` | computed dates plausible; none past-due at create | `due_at < packet.now` or `> now + horizon` | within a configurable "tight" window → `warn` |
| `extraction_confidence` | flagged fields above thresholds | any field `< fail_floor` | field in `[fail_floor, warn_threshold)` |

## 5. Behaviour & flows (happy path + state transitions)

**Happy path (`qc.verify`):**
1. Validate `QCVerifyInput` (Pydantic). Reject malformed packet with a typed
   validation error (the only error surfaced to the caller).
2. Resolve effective `QCConfig` from `packet.config_snapshot` + feature flags;
   compute `config_fingerprint`.
3. Determine selected checks = requested subset ∩ applicable(kind), else all
   applicable. Record any requested-but-not-applicable as `skipped`.
4. For each selected check, **in a stable, id-sorted order**: call `check.run`.
   Wrap in a guard: any exception ⇒ `CheckResult(verdict="fail", reason="check errored", evidence={"error_type": ...})` (QN-7, fail-closed).
5. Aggregate: `block` if any `fail`; else `pass_with_warnings` if any `warn`;
   else `pass`. `skipped` entries never affect the aggregate but are listed.
6. Build `QCReport`; **write one audit record** (`action="qc.verify"`, inputs =
   packet fingerprint + selected checks, outputs = aggregate + per-check
   verdicts/reasons, `run_id`) — *before* returning (QN-1, NFR-2).
7. Attach the report to the workflow run record (via the run store provided by
   `workflow-orchestration`); return `QCReport`.

**Aggregation is order-independent** (QR-11): the set of verdicts determines the
aggregate; the id-sorted run order only ensures deterministic per-check
ordering in the report and logs.

**No state machine of its own** — QC is a single synchronous evaluation. The
*gate* state transition (`awaiting_approval` ↔ resumed) is owned by
`workflow-orchestration` and keyed off `aggregate`.

## 6. Edge cases & error handling

- **Missing optional input field** a check needs ⇒ check returns `skipped`
  (`field_absent`) with the field named — never a false `pass` (QR-12).
  Example: DOB absent ⇒ consistency still runs on name + matter ref, records DOB
  comparison as `skipped`.
- **Empty packet** (no documents/comms) ⇒ checks that require content `skip`;
  aggregate may be `pass` with all-skipped — but `qc.verify` records the
  all-skipped condition so a caller cannot mistake "nothing checked" for
  "verified". *ASSUMPTION (confirm): an all-skipped result on an action-bearing
  packet should be treated as `block` by the calling workflow.*
- **Check raises / times out** ⇒ recorded `fail` ("check errored"), aggregate
  blocks (QN-7). A per-check soft timeout (config, default 500ms) guards latency.
- **Unknown check id requested** ⇒ `skipped` (`disabled`/unknown) recorded; does
  not abort the run.
- **Unsupported packet kind for a requested check** ⇒ `skipped`
  (`unsupported_packet_kind`).
- **Conflicting `external_bound`**: caller says internal but a recipient domain
  is external (or vice-versa) ⇒ privilege/recipient checks use the **stricter**
  interpretation (treat as external) and record the discrepancy as evidence.
- **Duplicate check id at registration** ⇒ startup error, server refuses to
  boot (prevents silently shadowing a safety check).
- **ConnectorError:** not applicable — QC performs no connector calls. If a
  packet field is itself an error sentinel from an upstream producer, the
  relevant check reads it as missing/invalid and fails closed.
- **Determinism guard:** any reliance on `datetime.now()` inside a check is
  prohibited; checks read `packet.now`. A lint/contract test enforces this.

## 7. Risk tiers & gates for each action

| Action | Risk tier | Gate |
|---|---|---|
| `qc.verify` (run checks, return report) | `read` | none (it is itself a **gate input**) |
| Attach report to run + write audit record | `write (confirm)`-equivalent internal effect, but non-reversible-only-as-append; performed automatically, no human gate (it is the audit of a read) | none |
| New check registration (deploy-time) | n/a (code/deploy) | covered by `security-audit-prep.md` (a new safety check is reviewed) |

**Severity policy (defaults — ASSUMPTION (confirm)):** `privilege`,
`recipient_integrity`, `attachment_integrity`, and `completeness` may emit
`fail`; `consistency` and `deadline_sanity` may `warn` or `fail`;
`extraction_confidence` `warn` below `warn_threshold`, `fail` below `fail_floor`.
A check's `severity_policy` is enforced by the registry: a check cannot emit a
verdict outside its declared policy (guards against a misconfigured check
silently downgrading `fail`→`warn`).

## 8. Data & persistence

- QC owns **no durable tables of its own**; it persists through two
  `platform-foundation` / `workflow-orchestration` surfaces:
  1. **Audit log** (hash-chained, append-only; PTD §12): one record per
     `qc.verify` call — `actor` (agent/service identity), `action="qc.verify"`,
     `inputs` (packet fingerprint, selected checks, config fingerprint),
     `outputs` (aggregate + per-check verdict/reason, PII-redacted), `run_id`,
     `timestamp`. *(NFR-2)*
  2. **Workflow run record**: the `QCReport` is stored as a structured attachment
     on the run (read by the gate and by the approval UI).
- **`config_fingerprint`** (hash of effective `QCConfig`) is stored with the
  report so a verdict is always reproducible against the config that produced it.
- **No raw client content** is written to the audit log — reasons/evidence carry
  field *names*, identifiers (redacted/last-4 where needed), checksums, and
  booleans, not document bodies or PII values (QN-2; CONVENTIONS §7).

## 9. Observability (logs/metrics/traces for this feature)

- **Logs (structlog):** one structured line per `qc.verify` (run_id, packet kind,
  aggregate, counts of pass/warn/fail/skip) and one per check at debug; all
  PII-scrubbed.
- **Metrics (Prometheus):** `qc_fail_rate` (per check id + aggregate),
  `qc_check_duration_ms` histogram, `qc_skip_total{reason}`,
  `qc_check_errored_total{check_id}`, `qc_block_total`.
- **Traces (OpenTelemetry):** a `qc.verify` span with one child span per check,
  correlated by `run_id` (NFR-7).
- **Alerting hooks:** `qc_check_errored_total` rising and any
  `qc.bypass_attempt` audit event page operations (a safety control erroring or
  being bypassed is operationally significant).

## 10. Security & privilege considerations

- QC is a **safety control**; see `security-audit-prep.md` for the full threat
  map. Key points here:
  - **Privilege check correctness** is paramount: it must never `pass` a
    privileged-doc-to-external packet, must fail closed on ambiguity, and is
    covered by a dedicated PBT (`pbt-properties.md`).
  - **Fail-closed everywhere:** unknown state, errored check, ambiguous
    external-bound flag ⇒ block.
  - **Bypass = security event:** any path that would skip QC at a gate, or
    downgrade a `fail`, is logged as `qc.bypass_attempt` in the audit log and
    alerted. QC itself emits an immutable verdict; only an audited override in
    `workflow-orchestration` may proceed past a `block`, and that override is its
    own audit event.
  - **PII handling:** reasons/evidence are redacted; immigration PII (A-numbers,
    passport numbers, DOB) never appears in plaintext in logs/telemetry
    (CONVENTIONS §7).
  - **AuthZ:** `qc.verify` runs under the constrained service identity; it reads
    only the packet handed to it (no broad data access), reducing blast radius.

## 11. Dependencies & integration points

- **`platform-foundation`** — domain model, audit-log writer, config/feature
  flags, observability, PII redaction utility.
- **`workflow-orchestration`** — run store (where `QCReport` is attached); the
  gate that consumes `aggregate`; the override/bypass audit semantics.
- **Consumes packet payloads** assembled by calling workflows from the outputs
  of `document-generation`, `data-extraction`, `deadline-engine`,
  `document-routing` — as data, not code imports.
- **Integration contract:** callers build a `VerificationPacket`, call
  `qc.verify`, and treat `aggregate == "block"` as a hard stop. QC publishes the
  `VerificationPacket` schema as the integration boundary.

## 12. Test strategy (focused tests + pointer to PBT)

- **Focused tests** (pytest) per check: a `pass`, a `warn` (where applicable),
  and a `fail` fixture each; registry totality (run-or-skip); aggregation truth
  table; fail-closed on errored check; determinism on repeat; audit-record
  written exactly once; new-check registration discovered without core edits.
- **Property-based tests** (Hypothesis) — see `pbt-properties.md`: any-single-
  fail-blocks; totality; determinism; recipient-integrity correctness; privilege
  never passes a privileged-to-external packet.
- **Security checks** — see `security-audit-prep.md`: privilege correctness,
  audit coverage, PII redaction, bypass detection.
- Detailed task-level tests live in `tasks.md` per group.

## 13. Open questions

- Should an **all-skipped** packet be a `block` by policy (see §6)? Proposed yes
  at the caller; confirm.
- Is an audited **attorney override** of a `block` required for v1? If so it is
  specified in `workflow-orchestration`; QC's verdict stays immutable.
- Canonical **immigration identifier set** for consistency matching (A-number,
  receipt number, etc.) — confirm which are enforced vs informational.
- Should `consistency` auto-normalise near-matches (case/whitespace) to `pass`,
  or always `warn`? Proposed: normalise then `warn` if normalisation was needed.
- Confirm the `PacketKind` enumeration and which checks apply to each kind.
