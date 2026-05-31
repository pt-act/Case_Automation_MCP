# Spec — Document Routing

> Feature slug: `document-routing` · Wave 2 · Traces: FR-11, NFR-1
> Inherits `specs/_shared/CONVENTIONS.md` and `concept/PTD.md`. This file is the
> behavioural contract ("what"); implementation steps live in `tasks.md`.

## 1. Summary

Document Routing classifies an inbound or generated `Document`, derives a
canonical name, resolves its destination `Matter` + folder, computes a
least-privilege ACL, enforces a **privilege-aware hard gate**, and moves the
document to its destination via the `DocStoreConnector.move` port — once,
idempotently, with every decision audited. It is exposed as the `document.route`
MCP tool and is invocable as a workflow step (e.g. after `document.generate` or
on a doc-received webhook). Privilege enforcement is the dominant invariant: a
privileged document is **never** routed to an external recipient (FR-11, NFR-1,
PTD §11 Privilege check).

## 2. Scope & out-of-scope

**In scope:** classification (via `data-extraction` + configurable rules); naming;
matter/folder resolution incl. an explicit `review_queue` fallback; ACL
computation; privilege hard gate (via `qc-verification`); idempotent move via the
DocStore port; optional recipient routing under the same gate; the `document.route`
tool; per-decision audit.

**Out of scope:** the DocStore adapter; extraction internals; QC internals;
document generation; email-send mechanics; defining the firm's concrete folder
taxonomy / naming tokens / class enumeration (all are **config**). See
`requirements.md` §3 for the authoritative list.

## 3. Domain types used / introduced

**Used (from PTD §4 / `platform-foundation`, do not redefine):** `Document`
(`classification`, `privileged`, `matter_id`, `version`, `checksum`, `uri`,
`name`), `Matter` (`reference`, `external_ids`, responsible/allowed principals),
`Contact` (recipient identity), `ACL` (from `DocStoreConnector.move` signature,
owned by `connector-framework`/`platform-foundation`).

**Introduced (feature-local Pydantic v2 types; promote to `platform-foundation`
only if reused):**

- `RoutingDestination` — `{ kind: Literal["folder","review_queue"]; matter_id:
  str | None; folder: str | None; reason: str | None }`.
- `RoutingDecision` — `{ document_id; classification: str; classification_confidence:
  float; classification_source: Literal["rule","extraction","fallback"]; name:
  str; destination: RoutingDestination; acl: ACL; privileged: bool; recipient_id:
  str | None; gate_verdict: Literal["pass","warn","fail"]; outcome:
  Literal["moved","queued","blocked","duplicate"]; idempotency_key: str }`.
- `RouteRequest` — `{ document_id: str; recipient_id: str | None; options:
  RouteOptions }`.
- `RouteOptions` — `{ force_review: bool = False; dry_run: bool = False;
  recipient_routing_enabled: bool = <feature flag> }`.
- `RouteResult` — the tool's return DTO: `RoutingDecision` minus internal-only
  fields, plus `audit_id` and `status`.
- `ClassificationRule`, `FolderMap`, `NamingTemplate`, `ClassPermissionPolicy` —
  **config** shapes (data-driven; loaded via `platform-foundation` config), not
  hard-coded logic (CONVENTIONS §5, §8).

## 4. Interfaces (MCP tools/resources/prompts, ports, internal APIs)

### 4.1 MCP tool — `document.route`

- **Risk tier:** `write (confirm)` for matter/folder filing; the **external
  recipient** path is `gated (human)` and additionally hard-blocked for
  privileged docs (PTD §5.1; CONVENTIONS §6).
- **Input (Pydantic):** `RouteRequest` — `document_id` (required), `recipient_id`
  (optional; if present, triggers recipient routing under the privilege gate),
  `options` (`force_review`, `dry_run`, `recipient_routing_enabled`).
- **Output (Pydantic):** `RouteResult` — classification, derived name,
  destination (folder or `review_queue`), ACL summary, privilege flag, gate
  verdict, outcome (`moved | queued | blocked | duplicate`), `audit_id`.
- **Self-describing schema** (FR-17): full field descriptions + the class
  enumeration are published to the MCP client.
- `dry_run=true` computes and returns the decision **without** moving or sharing
  (still audited as a `dry_run` decision).

### 4.2 Ports consumed (never adapters — CONVENTIONS §10)

- `DocStoreConnector.move(id, folder, acl) -> Document` (PTD §6). Idempotency key
  forwarded. Routing also reads classification inputs that originate from
  `data-extraction` (passed in or fetched via its service interface).
- `qc-verification` privilege/recipient checks — invoked as the hard gate; returns
  `pass | warn | fail` + reason (PTD §11).
- `data-extraction` service — provides structured fields + per-field confidence
  for classification (consumed, not re-implemented).
- `platform-foundation` — audit-log writer, config loader, RBAC/allowed-principal
  lookup for a matter, observability.

### 4.3 Internal service API (within this feature)

- `Classifier.classify(document, extraction) -> (class, confidence, source)`.
- `Namer.derive_name(document, class, matter) -> str`.
- `Resolver.resolve(document, class) -> RoutingDestination`.
- `AclBuilder.build(matter, class) -> ACL`.
- `PrivilegeGate.check(document, destination, recipient) -> GateVerdict` (delegates
  the actual privilege test to `qc-verification`; this is the enforcement wrapper).
- `RoutingService.route(request) -> RoutingDecision` (orchestrates the above; owns
  idempotency + audit).

> If `RoutingService` approaches ~400 LOC, split orchestration from the
> idempotency/audit concern into a thin coordinator + helpers (CONVENTIONS §3.5).

## 5. Behaviour & flows (happy path + state transitions)

**Happy path (file to matter folder):**
1. Validate `RouteRequest`; load `Document` (and its `Matter` if known) via
   `platform-foundation`.
2. Compute the **idempotency key** = `hash(document_id, document.checksum,
   recipient_id, routing_intent_version)`. If a prior completed decision exists
   for this key → return it as `outcome="duplicate"` (no second move).
3. **Classify** using extraction fields + classification rule set →
   `(class, confidence, source)`. If `confidence < threshold` or `class ==
   unknown` or `options.force_review` → destination = `review_queue`.
4. **Resolve** matter + folder via the folder-map (totality: known class →
   concrete folder; otherwise `review_queue`).
5. **Derive name** from the naming template.
6. **Build ACL** from the matter's allowed-principal set ∩ class permission policy
   (result is always a subset of the matter's allowed set).
7. **Privilege gate:** call `qc-verification`'s privilege/recipient check with
   `(document.privileged, destination, recipient)`.
   - Privileged **and** target external → `outcome="blocked"`, **no move/share**,
     audit, return blocked result with reason.
   - Otherwise verdict `pass`/`warn` → proceed (`warn` is allowed but recorded).
8. If destination is `review_queue` → record `outcome="queued"`, **no move**,
   audit, return (a human handles it).
9. **Move** via `DocStoreConnector.move(document_id, folder, acl)` with the
   idempotency key → updated `Document` (new folder/ACL; version/checksum per
   store). `outcome="moved"`.
10. **Audit** the full decision + outcome (before returning, per NFR-2), return
    `RouteResult`.

**Recipient routing (optional):** when `recipient_id` is set and
`recipient_routing_enabled`, steps 7's gate runs **before** any external share;
privileged → blocked; otherwise the recipient share is requested under the same
idempotency + audit discipline. Actual send mechanics belong to the email/connector
layer (out of scope) — routing only authorises/denies and records.

**State transitions (per route run):** `received → classified → resolved →
acl_built → gate_checked → (moved | queued | blocked | duplicate)`. Each
transition is logged; terminal states are recorded in the audit log.

## 6. Edge cases & error handling (incl. ConnectorError handling)

- **Unknown/low-confidence class** → `review_queue` (`queued`), no move. *(DR-FR-11)*
- **Class not in folder-map** → mapping totality guarantees `review_queue`, never
  an undefined destination. A missing map entry is a **config error** surfaced at
  load time, not at route time.
- **Privileged + unknown privilege** → default-deny: treat as privileged for
  external targets (blocked). Internal filing still allowed.
- **Privileged + external recipient/destination** → `blocked`, typed
  `PrivilegeViolation` reason; **never** partially moved. *(NFR-1)*
- **Matter not resolvable** → `review_queue`; do not invent a matter.
- **ACL would exceed matter allowed set** → treated as a bug-class invariant
  violation: the build is constrained to a subset by construction; if a computed
  principal is not in the allowed set it is dropped and the drop is audited.
- **Idempotency replay** → return prior result as `duplicate`; no second move.
- **`ConnectorError` taxonomy (PTD §6):**
  - `auth` / `fatal` → do not retry; park run / return typed error; audit failure.
  - `rate-limit` / `transient` → retry with `tenacity` backoff+jitter; if exhausted,
    park as parked-run (NFR-9), audit, return transient error. **No partial move.**
  - `not-found` (document/folder) → typed error, `review_queue` or fail per config;
    audited.
- **Doc store partial success ambiguity** → rely on the forwarded idempotency key
  so a retry is a no-op; never assume success without the store's confirmation.
- **`dry_run`** → compute decision, skip move/share, audit as `dry_run`.

## 7. Risk tiers & gates for each action

| Action | Risk tier | Gate |
|---|---|---|
| Classify / name / resolve / build ACL | `read` (pure decision) | none |
| Move to matter folder (internal) | `write (confirm)` | single confirmation; reversible-by-record |
| Route/share to a recipient (external) | `gated (human)` | human approval (any of the 3 channels) **and** privilege hard gate |
| Route a **privileged** doc externally | n/a | **hard-blocked** — cannot be approved by automation; never proceeds |

The privilege hard gate is **not** a discretionary approval gate — it is a
non-overridable invariant (NFR-1). A normal `gated (human)` approval cannot
release a privileged document to an external recipient.

## 8. Data & persistence

- **Routing decisions** persisted to a `routing_decision` table (Postgres via
  SQLAlchemy 2 / Alembic): `id, document_id, idempotency_key (unique), class,
  confidence, source, name, destination_kind, matter_id, folder, acl_digest,
  privileged, recipient_id, gate_verdict, outcome, created_at, run_id`. The
  unique `idempotency_key` enforces "filed exactly once".
- **Audit records** written to the shared append-only, hash-chained audit table
  (`platform-foundation`): `actor, action="document.route", inputs (refs/ids,
  no content), outputs (decision summary), approval, timestamp, run_id`.
- **Config** (folder-map, naming template, classification rules, class policies)
  loaded via `platform-foundation` config; versioned so a decision records which
  config/`routing_intent_version` produced it.
- **No document content or raw PII** stored in routing/audit rows — only ids,
  digests, and a redacted ACL summary.

## 9. Observability (logs/metrics/traces for this feature)

- **Logs (structlog, PII-scrubbed, correlation = `run_id`):** one structured line
  per transition; privilege blocks logged at WARN with reason code (no content).
- **Metrics (Prometheus):** `routing_decisions_total{outcome}`,
  `routing_classification_confidence` (histogram), `routing_review_queue_rate`,
  `routing_privilege_blocks_total`, `routing_move_latency_seconds`,
  `routing_connector_errors_total{kind}`.
- **Traces (OpenTelemetry):** span per step (`classify`, `resolve`, `acl_build`,
  `privilege_gate`, `move`) under a parent `document.route` span.
- **Alerting hooks:** any `routing_privilege_blocks_total` increment and any
  parked-run from routing surface to ops (privilege blocks are expected-but-
  notable; parked runs need attention).

## 10. Security & privilege considerations

- **Privilege enforcement (critical):** the hard gate is invoked for **every**
  route that has any external target; default-deny on unknown privilege; the
  invariant is covered by PBT (`pbt-properties.md`). *(NFR-1)*
- **ACL correctness:** ACL ⊆ matter allowed-principal set by construction; PBT
  asserts no broadening. Least-privilege per class policy.
- **Audit:** every decision + outcome audited before completion (NFR-2).
- **PII:** immigration PII (A-numbers, passports, biometrics, status, country) is
  never logged or stored in decision/audit rows; only ids/digests.
- **Residency:** routing moves documents only within the configured region; the
  DocStore port is assumed region-scoped (NFR-6).
- Full enumeration in `security-audit-prep.md`.

## 11. Dependencies & integration points

- **`platform-foundation`** — domain model, audit writer, config loader, RBAC /
  matter allowed-principal lookup, observability.
- **`connector-framework`** — `DocStoreConnector` port + `move`, `ConnectorError`
  taxonomy, idempotency plumbing, doc-received webhook trigger.
- **`qc-verification`** — privilege + recipient-integrity checks (the hard gate).
- **`data-extraction`** — structured fields + confidence for classification.
- **Invoked by:** `workflow-orchestration` (as a step) and the MCP agent directly;
  may be chained after `document-generation`.

## 12. Test strategy (focused tests + pointer to PBT)

- **Focused (pytest):** classification of representative docs per class;
  naming-template determinism; folder-map resolution incl. `review_queue`
  fallback; ACL subset for sample matters; privilege block on external target;
  idempotent replay returns `duplicate`; each `ConnectorError` kind handled;
  `dry_run` performs no move; audit written before return.
- **Contract:** `DocStoreConnector.move` exercised against the port's recorded
  fixtures (respx/vcr) via `connector-framework`'s harness — adapter itself
  out of scope.
- **PBT (Hypothesis):** the four invariants in `pbt-properties.md` — privilege
  never→external, idempotency (filed once), classification→destination totality,
  ACL never broadens.

## 13. Open questions

- Recipient routing in v1, or filing+ACL only? (feature flag default to confirm).
- Internal-vs-external boundary for co-counsel and the client themselves (the
  privilege holder) — see `requirements.md` §8.
- Async threshold for large moves (DR-NFR-5).
- Whether `warn` privilege verdicts should still require a human confirmation for
  internal filing, or only for external targets.
