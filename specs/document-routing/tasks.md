# Tasks — Document Routing

> Feature slug: `document-routing` · Wave 2 · Traces: FR-11, NFR-1
> Sizes: `XS/S/M/L` (L should be split). Deps: intra-spec by task id, cross-spec
> as `<slug>#<id>`. See `CONVENTIONS.md` §10 for the legend. Definition of done =
> code + tests + docs.

## Overview (task groups + critical path + parallelisation)

Groups: **G1 Config & types** → **G2 Classification** / **G3 Naming & resolution**
(parallel) → **G4 ACL** → **G5 Privilege gate** → **G6 Routing service +
idempotency + move** → **G7 MCP tool** → **G8 Observability & audit** → **G9
Docs + PBT/security wiring**.

**Critical path:** G1 → G2 → G6 → G5 → G7 (the privilege gate G5 is a hard
prerequisite for any external-target outcome in G6/G7; build the gate before
enabling recipient routing). G3, G4, G8 parallelise alongside the path.

Cross-spec prerequisites must exist (ports/services available): `platform-foundation`
(domain model, audit, config, RBAC), `connector-framework` (`DocStoreConnector`,
`ConnectorError`, idempotency), `qc-verification` (privilege check),
`data-extraction` (fields + confidence).

---

## Group 1: Config & feature-local types

- [ ] **1.1 Define feature-local Pydantic types** — `RouteRequest`, `RouteOptions`,
  `RoutingDestination`, `RoutingDecision`, `RouteResult`, `GateVerdict`.
  - size: S · depends on: `platform-foundation#domain-model` · parallel: no
  - acceptance: types validate; `RoutingDestination.kind ∈ {folder, review_queue}`;
    `outcome ∈ {moved, queued, blocked, duplicate, dry_run}`; schemas serialise for MCP.
  - focused tests: (1) valid/invalid `RouteRequest`; (2) destination enum guard;
    (3) outcome enum guard; (4) `RouteResult` round-trips to JSON schema.
- [ ] **1.2 Define config shapes + loader bindings** — `ClassificationRule`,
  `FolderMap`, `NamingTemplate`, `ClassPermissionPolicy`, class enumeration; load
  via `platform-foundation` config; record `routing_intent_version`.
  - size: M · depends on: 1.1, `platform-foundation#config` · parallel: no
  - acceptance: config loads from data (no hard-coded classes/folders, CONVENTIONS
    §5/§8); missing folder-map entry for a declared class fails **at load time**;
    version is exposed on every decision.
  - focused tests: (1) loads sample config; (2) load-time error on class missing a
    folder map; (3) naming template parsed; (4) class enum is closed + includes
    `unknown`; (5) version surfaced.

## Group 2: Classification

- [ ] **2.1 `Classifier.classify`** — combine `data-extraction` fields + confidence
  with the classification rule set → `(class, confidence, source)`; `source ∈
  {rule, extraction, fallback}`; below-threshold/unknown → flagged for review.
  - size: M · depends on: 1.2, `data-extraction#service-interface` · parallel: no
  - acceptance: returns exactly one class from the enum; threshold (ASSUMPTION
    0.80) configurable; ambiguous → `unknown`/flagged; no PII in outputs/logs.
  - focused tests: (1) clear rule match; (2) extraction-driven match; (3)
    below-threshold → review flag; (4) conflicting signals → `unknown`; (5)
    deterministic for identical inputs.

## Group 3: Naming & matter/folder resolution (parallel with G2)

- [ ] **3.1 `Namer.derive_name`** — render configurable naming template over matter
  ref + class + date + version; deterministic + filesystem-safe.
  - size: S · depends on: 1.2 · parallel: yes
  - acceptance: same inputs → same name; template tokens honoured; unsafe chars
    sanitised; matches ASSUMPTION default `{matter_reference}_{class}_{yyyymmdd}_v{version}`.
  - focused tests: (1) determinism; (2) token substitution; (3) char sanitisation;
    (4) version increment reflected.
- [ ] **3.2 `Resolver.resolve` + totality guarantee** — map (document, class) →
  `RoutingDestination`; every class → concrete folder or `review_queue`; unresolved
  matter → `review_queue`.
  - size: M · depends on: 1.2 · parallel: yes
  - acceptance: **total** over the class enum (no unhandled class); unresolved
    matter never invents a destination; `force_review` overrides to `review_queue`.
  - focused tests: (1) known class → folder; (2) unknown class → review_queue; (3)
    unresolved matter → review_queue; (4) `force_review` honoured; (5) totality
    over full enum (loop test).

## Group 4: ACL construction

- [ ] **4.1 `AclBuilder.build`** — compose ACL = matter allowed-principal set ∩
  class permission policy; result always a subset of the matter's allowed set;
  drop+audit any principal outside the allowed set.
  - size: M · depends on: 1.2, `platform-foundation#rbac` · parallel: yes
  - acceptance: ACL ⊆ matter allowed set for all inputs; least-privilege per class
    policy; dropped principals recorded; empty allowed set → empty ACL (no default-open).
  - focused tests: (1) subset for sample matter; (2) class policy narrows further;
    (3) out-of-set principal dropped + audited; (4) empty allowed set → empty ACL.

## Group 5: Privilege hard gate (critical)

- [ ] **5.1 `PrivilegeGate.check` wrapper** — call `qc-verification` privilege +
  recipient-integrity checks with `(privileged, destination, recipient)`; map to
  `GateVerdict`; default-deny on unknown privilege for external targets.
  - size: M · depends on: 1.1, `qc-verification#privilege-check` · parallel: no
  - acceptance: privileged + external → `fail` (block); internal filing of
    privileged → allowed; unknown privilege + external → block (default-deny);
    verdict + reason returned without content.
  - focused tests: (1) privileged+external → fail; (2) privileged+internal → pass;
    (3) non-privileged+external → pass/warn; (4) unknown privilege+external →
    fail; (5) reason code present, no PII.
- [ ] **5.2 Non-overridable enforcement** — ensure a `gated (human)` approval cannot
  release a privileged doc externally; the hard block precedes/over-rides approval.
  - size: S · depends on: 5.1 · parallel: no
  - acceptance: even with an approval token, privileged+external yields `blocked`;
    no move/share performed.
  - focused tests: (1) approval present but privileged+external → blocked; (2) no
    DocStore.move call made on block.

## Group 6: Routing service, idempotency & move

- [ ] **6.1 Idempotency key + decision persistence** — key = `hash(document_id,
  checksum, recipient_id, routing_intent_version)`; unique constraint on
  `routing_decision.idempotency_key`; replay returns prior result as `duplicate`.
  - size: M · depends on: 1.1, 1.2, `platform-foundation#persistence` · parallel: no
  - acceptance: second identical request performs **no** second move and returns
    `duplicate`; unique-constraint violation handled (409 semantics); content-change
    (new checksum) yields a new key.
  - focused tests: (1) replay → duplicate; (2) no second move on replay; (3) new
    checksum → new decision; (4) concurrent duplicate → one move (lock/constraint).
- [ ] **6.2 `RoutingService.route` orchestration** — wire classify → resolve → name
  → acl → privilege gate → (move | queue | block | duplicate); enforce ordering
  (gate before any external effect); `dry_run` short-circuits the move.
  - size: L → split into 6.2a coordinator (flow/states) + 6.2b effects (move call +
    outcome mapping) · depends on: 2.1, 3.1, 3.2, 4.1, 5.1, 5.2, 6.1 · parallel: no
  - acceptance: happy path files once; review_queue/blocked perform no move;
    `dry_run` performs no move but audits; state transitions emitted in order.
  - focused tests: (1) happy path → moved; (2) low-confidence → queued; (3)
    privileged+external → blocked; (4) dry_run → no move; (5) transition order; (6)
    audit written before return.
- [ ] **6.3 DocStore move + ConnectorError handling** — call
  `DocStoreConnector.move(id, folder, acl)` with idempotency key; map
  `ConnectorError` kinds (auth/fatal/rate-limit/transient/not-found) to retry/park/
  fail; never partial-move.
  - size: M · depends on: 6.2, `connector-framework#docstore-port`,
    `connector-framework#error-taxonomy` · parallel: no
  - acceptance: transient/rate-limit retried w/ backoff then parked if exhausted;
    auth/fatal not retried; not-found typed; no partial filing; failure audited.
  - focused tests: (1) success path; (2) transient → retry → success; (3) transient
    exhausted → parked; (4) auth → no retry, typed error; (5) not-found handled.

## Group 7: MCP tool `document.route`

- [ ] **7.1 Implement `document.route` tool** — thin: validate → `RoutingService.route`
  → audit → return `RouteResult`; self-describing schema incl. class enum;
  recipient routing behind `recipient_routing_enabled` flag.
  - size: M · depends on: 6.2, 6.3, 8.1 · parallel: no
  - acceptance: schema published to MCP client (FR-17); risk tiers per `spec.md` §7;
    recipient path runs the gate before any external share; `dry_run` supported.
  - focused tests: (1) schema completeness; (2) file path → moved; (3) external
    recipient + privileged → blocked; (4) flag off → recipient routing skipped;
    (5) dry_run.

## Group 8: Observability & audit

- [ ] **8.1 Audit every decision + outcome** — write to the shared hash-chained
  audit table before returning; ids/digests only, no content/PII.
  - size: S · depends on: 1.1, `platform-foundation#audit-log` · parallel: yes
  - acceptance: every outcome (moved/queued/blocked/duplicate/dry_run) audited
    before return; record contains no raw PII/content; `run_id` correlated.
  - focused tests: (1) audit precedes return; (2) no PII in record; (3) all
    outcomes audited; (4) hash-chain link valid.
- [ ] **8.2 Metrics, logs, traces** — emit the metrics/spans/logs in `spec.md` §9;
  PII-scrubbed; privilege-block + parked-run alert hooks.
  - size: S · depends on: 6.2, `platform-foundation#observability` · parallel: yes
  - acceptance: counters/histograms exposed; spans per step; privilege block emits
    WARN + metric; logs PII-scrubbed.
  - focused tests: (1) outcome counter increments; (2) privilege-block metric; (3)
    log line scrubbed; (4) span hierarchy present.

## Group 9: Docs, PBT & security wiring

- [ ] **9.1 Workflow doc** — `docs/workflows/document-routing.md` (trigger, steps,
  gates, side effects, idempotency key, failure handling) per PTD §16.
  - size: S · depends on: 7.1 · parallel: yes
  - acceptance: doc present; tool schema description CI-check passes (FR-18); idem
    key + privilege gate documented.
  - focused tests: (1) docs CI check passes; (2) tool has description.
- [ ] **9.2 Implement PBT suite** — the four invariants in `pbt-properties.md`.
  - size: M · depends on: 6.2, 5.1, 4.1, 3.2 · parallel: no
  - acceptance: all four properties pass; seeded edge inputs included.
  - focused tests: covered by PBT (privilege, idempotency, totality, ACL subset).
- [ ] **9.3 Security review prep** — execute `security-audit-prep.md` checklist for
  this feature.
  - size: S · depends on: 7.1, 8.1 · parallel: yes
  - acceptance: every checklist item verified or flagged; threats mapped to
    NFR-1/2/4/6/8.
  - focused tests: (n/a — review checklist).

---

## Dependency graph (intra-spec + cross-spec)

```
platform-foundation#{domain-model,config,persistence,rbac,audit-log,observability}
connector-framework#{docstore-port,error-taxonomy,idempotency}
qc-verification#privilege-check
data-extraction#service-interface
        │
        ▼
1.1 ─┬─► 1.2 ─┬─► 2.1 ─────────────┐
     │        ├─► 3.1 (parallel)   │
     │        ├─► 3.2 (parallel)   │
     │        └─► 4.1 (parallel)   │
     ├─► 5.1 ─► 5.2 ───────────────┤
     └─► 8.1 (parallel)            │
1.1,1.2 ─► 6.1 ───────────────────►├─► 6.2 ─► 6.3 ─► 7.1 ─► 9.1
                                   │                 │
8.2 (parallel, after 6.2) ────────┘            9.2 ─┤
                                               9.3 ─┘
```

Critical path: `1.1 → 1.2 → 2.1 → 6.1 → 6.2 → 6.3 → 7.1`, with `5.1 → 5.2`
required before any external-target outcome is enabled in 6.2/7.1.

## Definition of done (code + tests + docs)

- **Code:** all groups implemented; no component > ~400 LOC (split noted for 6.2);
  vendor-agnostic (ports only); classes/folders/naming are config, not code.
- **Tests:** focused tests per group passing; the four PBTs passing; DocStore
  contract test via `connector-framework` harness.
- **Docs:** `docs/workflows/document-routing.md` present; `document.route` tool
  self-describes and passes the docs/schema CI check (FR-18); ASSUMPTIONs tracked
  for client confirmation.
- **Security:** `security-audit-prep.md` checklist satisfied; privilege invariant
  proven by PBT; every routing decision audited.
