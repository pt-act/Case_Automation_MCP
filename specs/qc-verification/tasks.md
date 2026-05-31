# Tasks — QC Verification

> Slug: `qc-verification` · Wave 3. Sizes: `XS/S/M/L` (CONVENTIONS §3.3, §10).
> Cross-spec deps use `<slug>#<id>`. Definition of done = code + tests + docs.

## Overview (task groups + critical path + parallelisation)

Seven groups. The **critical path** is: G1 (types + packet) → G2 (registry +
aggregation) → G3–G5 (the seven checks) → G6 (`qc.verify` tool + audit/run
attach) → G7 (prompt + docs/CI). G3, G4, G5 are mutually parallel once G2 lands
(each check only depends on the registry contract). The single most important
correctness work — privilege + aggregation + totality — is in G2 and G4 and is
gated by the PBTs in `pbt-properties.md`.

```
G1 ─► G2 ─► G3 ─┐
            ├─► G4 ─┐
            └─► G5 ─┴─► G6 ─► G7
```

Cross-spec prerequisites (must exist as contracts before G6 can integrate):
- `platform-foundation#audit-writer`, `platform-foundation#domain-model`,
  `platform-foundation#config-flags`, `platform-foundation#pii-redaction`.
- `workflow-orchestration#run-store`, `workflow-orchestration#gate-contract`.

---

## Group 1: Domain types & the verification packet
Establish the QC-local Pydantic v2 types and the packet boundary (spec §3).

- [ ] **1.1** Define `Verdict`, `Aggregate`, `SkipReason`, `CheckResult`,
  `CheckDescriptor`, `SeverityPolicy`, `QCConfig`, `QCReport`.
  — size: **S** · depends on: `platform-foundation#domain-model` · parallel: no
  - **Acceptance:** all types are Pydantic v2; `QCReport` serialises to a stable
    JSON schema; `Verdict` includes `skipped`; `Aggregate` ∈
    {pass, pass_with_warnings, block}.
- [ ] **1.2** Define `VerificationPacket` and its members (`ExtractedField`,
  `TemplateBinding`, `AttachmentRef`, `PacketKind` enum) with `now` and
  `config_snapshot` fields.
  — size: **M** · depends on: 1.1 · parallel: no
  - **Acceptance:** packet validates a fully-populated example; `now` is
    required (no implicit clock); `PacketKind` is an enum (ASSUMPTION-flagged).
- [ ] **1.3** `config_fingerprint` + `QCConfig` loader (effective config from
  snapshot + feature flags).
  — size: **S** · depends on: 1.1, `platform-foundation#config-flags` · parallel: yes
  - **Acceptance:** identical config ⇒ identical fingerprint; changing any
    threshold changes the fingerprint.

**Focused tests (G1):** (a) packet round-trips JSON; (b) missing `now` rejected;
(c) `QCReport` schema snapshot stable; (d) config fingerprint deterministic;
(e) fingerprint changes on threshold change.

---

## Group 2: Check registry, totality & aggregation
The FR-16 extension point and the any-`fail`-blocks core (spec §4.3, §5).

- [ ] **2.1** `Check` Protocol + `@register_check` decorator + entry-point
  discovery; duplicate-id → startup error.
  — size: **M** · depends on: 1.1 · parallel: no
  - **Acceptance:** a check module is discovered without editing the registry;
    duplicate `id` raises at startup; `describe()` returns a valid descriptor.
- [ ] **2.2** `CheckRegistry.run`: select applicable checks, id-sorted stable
  order, per-check exception guard → `fail` ("check errored"), severity-policy
  enforcement (verdict outside policy rejected), **totality** (every selected
  check → result or `skipped` with reason).
  — size: **M** · depends on: 2.1 · parallel: no
  - **Acceptance:** every selected check appears once; an exception yields a
    `fail` not a crash; a check emitting an out-of-policy verdict is rejected.
- [ ] **2.3** Aggregation function: any `fail` ⇒ `block`; else any `warn` ⇒
  `pass_with_warnings`; else `pass`; `skipped` ignored. Order-independent.
  — size: **S** · depends on: 2.1 · parallel: yes
  - **Acceptance:** truth table holds; shuffling results does not change
    aggregate; all-`skipped` ⇒ `pass` but flagged in report.
- [ ] **2.4** Per-check soft timeout guard (config; default 500ms) → `fail`.
  — size: **S** · depends on: 2.2 · parallel: yes
  - **Acceptance:** a slow check is recorded as errored `fail`; latency bounded.

**Focused tests (G2):** (a) discovery finds a fixture check; (b) duplicate id
errors at startup; (c) aggregation truth table; (d) order-independence; (e)
errored check → fail-closed; (f) out-of-policy verdict rejected; (g) totality:
selected-but-skipped recorded with reason; (h) timeout → fail.

---

## Group 3: Content/structure checks
Completeness, consistency, attachment integrity (spec §4.4).

- [ ] **3.1** `completeness` — required template vars resolved + required matter
  fields present.
  — size: **M** · depends on: 2.2 · parallel: yes
  - **Acceptance:** unresolved/empty required var ⇒ `fail`; optional missing ⇒
    `warn` (config); all present ⇒ `pass`; missing binding input ⇒ `skipped`.
- [ ] **3.2** `consistency` — client name / DOB / matter ref identical across
  packet; near-match normalisation then `warn`.
  — size: **M** · depends on: 2.2 · parallel: yes
  - **Acceptance:** mismatch ⇒ `fail`; case/whitespace-only diff ⇒ normalise +
    `warn`; DOB absent ⇒ that comparison `skipped`, others still run.
- [ ] **3.3** `attachment_integrity` — referenced attachments present & correct
  version/checksum.
  — size: **M** · depends on: 2.2 · parallel: yes
  - **Acceptance:** absent attachment ⇒ `fail`; checksum mismatch ⇒ `fail`;
    newer version than referenced ⇒ `warn` (config); exact match ⇒ `pass`.

**Focused tests (G3):** completeness pass/warn/fail/skip; consistency
mismatch-fail / near-match-warn / dob-absent-skip; attachment present-pass /
missing-fail / checksum-mismatch-fail.

---

## Group 4: Safety-critical checks
Recipient integrity and privilege — the highest-stakes checks (spec §4.4, §10).

- [ ] **4.1** `recipient_integrity` — every external recipient ∈
  `matter.participants`; resolves to intended client.
  — size: **M** · depends on: 2.2 · parallel: yes
  - **Acceptance:** recipient not in participants ⇒ `fail`; in participants but
    role mismatch ⇒ `warn`; all valid ⇒ `pass`; empty recipients on a send
    packet ⇒ `fail` (nothing to verify against is not a pass).
- [ ] **4.2** `privilege` — no `privileged` doc to an external recipient; uses
  stricter of caller `external_bound` vs re-derived recipient domains; **never**
  downgrades to `warn`; fail-closed on ambiguity.
  — size: **M** · depends on: 2.2 · parallel: yes
  - **Acceptance:** any privileged doc in an external-bound packet ⇒ `fail`;
    privileged doc in a provably internal packet ⇒ `pass`; ambiguous
    external-bound ⇒ treated external ⇒ `fail`; privilege never emits `warn`.

**Focused tests (G4):** recipient in/out/role-mismatch/empty; privilege
external-fail / internal-pass / ambiguous-fail / multi-doc one-privileged-fail;
assert privilege can never return `pass` when any privileged doc + external.

---

## Group 5: Temporal & confidence checks
Deadline sanity and extraction confidence (spec §4.4).

- [ ] **5.1** `deadline_sanity` — `due_at >= packet.now` and
  `due_at <= now + horizon`; tight-window ⇒ `warn`.
  — size: **S** · depends on: 2.2 · parallel: yes
  - **Acceptance:** past-due at create ⇒ `fail`; beyond horizon ⇒ `fail`; within
    tight window ⇒ `warn`; plausible ⇒ `pass`; uses `packet.now` only.
- [ ] **5.2** `extraction_confidence` — field `< fail_floor` ⇒ `fail`; in
  `[fail_floor, warn_threshold)` ⇒ `warn`; `>= warn_threshold` ⇒ `pass`.
  — size: **S** · depends on: 2.2 · parallel: yes
  - **Acceptance:** thresholds read from config; boundary values handled
    (inclusive/exclusive as specified); no extracted fields ⇒ `skipped`.

**Focused tests (G5):** deadline past-due-fail / horizon-fail / tight-warn /
ok-pass / now-injected-determinism; confidence below-floor-fail /
mid-warn / above-pass / boundary / empty-skip.

---

## Group 6: `qc.verify` tool, audit & run attachment
Wire the registry into the MCP surface and the audit/run stores (spec §4.1, §8).

- [ ] **6.1** `qc.verify` MCP tool: validate `QCVerifyInput`, resolve config,
  select checks, run registry, aggregate, build `QCReport`.
  — size: **M** · depends on: 2.2, 2.3, G3, G4, G5 · parallel: no
  - **Acceptance:** `read` risk tier; returns typed `QCReport`; malformed packet
    ⇒ typed validation error (only error surfaced); self-describing schema.
- [ ] **6.2** Write exactly one audit record per call (PII-redacted) **before**
  returning; attach `QCReport` to the workflow run.
  — size: **M** · depends on: 6.1, `platform-foundation#audit-writer`,
  `platform-foundation#pii-redaction`, `workflow-orchestration#run-store` · parallel: no
  - **Acceptance:** one audit record per call linked to `run_id`; no raw PII in
    record; report retrievable from run; audit written before return value.
- [ ] **6.3** `qc.bypass_attempt` audit hook + observability (metrics, spans,
  logs) per spec §9.
  — size: **S** · depends on: 6.2 · parallel: yes
  - **Acceptance:** `qc_fail_rate`, `qc_skip_total{reason}`,
    `qc_check_errored_total`, `qc_block_total` emitted; per-check trace spans;
    a simulated bypass logs `qc.bypass_attempt` and increments its metric.

**Focused tests (G6):** tool happy-path returns report; malformed packet →
validation error; audit record count == 1 per call; report attached to run;
redaction applied (no PII string in record); bypass attempt audited.

---

## Group 7: `qc_checklist` prompt, self-description & docs/CI
Close the loop on FR-17/FR-18, NFR-10 (spec §4.2; CONVENTIONS §3, PTD §16).

- [ ] **7.1** `qc_checklist` MCP prompt: given packet kind, enumerate applicable
  checks, what each verifies, the any-`fail`-blocks rule; no client content.
  — size: **S** · depends on: 6.1 · parallel: yes
  - **Acceptance:** prompt lists exactly the checks `applicable(kind)`; contains
    no PII; versioned.
- [ ] **7.2** Per-check `CheckDescriptor` self-description surfaced to MCP client
  + a docs-CI check that fails the build if a registered check lacks a
  descriptor/doc entry or its schema drifts.
  — size: **M** · depends on: 2.1, 6.1 · parallel: yes
  - **Acceptance:** every registered check has a descriptor + doc page; CI fails
    on a check missing docs or with drifted schema (NFR-10, FR-18).
- [ ] **7.3** Feature docs: `docs/workflows/qc-verification.md` (or connector-
  style page) — checks, packet schema, verdict model, extension guide for adding
  a check.
  — size: **S** · depends on: 7.2 · parallel: yes
  - **Acceptance:** an engineer can add a new check following the guide without
    touching registry core; doc shows the integration boundary
    (`VerificationPacket`).

**Focused tests (G7):** prompt enumerates correct checks per kind; CI red when a
check lacks docs; CI red on schema drift; extension-guide example check registers
and runs end-to-end.

---

## Dependency graph (intra-spec + cross-spec)

```
Intra-spec:
  1.1 ─► 1.2 ─► 2.1 ─► 2.2 ─► {3.1,3.2,3.3,4.1,4.2,5.1,5.2} ─► 6.1 ─► 6.2 ─► 6.3
  1.1 ─► 1.3                     2.1 ─► 2.3 ─► 6.1
                                 2.2 ─► 2.4
  6.1 ─► 7.1 ; 6.1 ─► 7.2 ─► 7.3

Cross-spec (must exist as contracts):
  1.1            depends on platform-foundation#domain-model
  1.3            depends on platform-foundation#config-flags
  6.2            depends on platform-foundation#audit-writer,
                            platform-foundation#pii-redaction,
                            workflow-orchestration#run-store
  (consumes, not code-deps) gate verdict → workflow-orchestration#gate-contract
```

**Parallelisation:** after 2.2 lands, Groups 3/4/5 run fully in parallel
(seven checks, independent). Within G6, 6.3 parallels later work once 6.2 lands.
G7 tasks parallelise against each other after 6.1.

## Definition of done (code + tests + docs)

A task/group is **done** only when:
- **Code:** implemented against the contracts in `spec.md`; ≤ ~400 LOC per
  component (split if larger, per CONVENTIONS §3.5); no `datetime.now()` inside
  checks (uses `packet.now`); fail-closed on the unknown.
- **Tests:** its focused tests pass **and** the relevant invariants in
  `pbt-properties.md` pass (esp. any-fail-blocks, totality, determinism,
  recipient-integrity, privilege-never-passes-external).
- **Docs:** a CI-verified descriptor + doc entry for every check (NFR-10,
  FR-18); the extension guide updated; `ASSUMPTION (confirm)` items still listed
  until the firm confirms.
