# Tasks — Platform Foundation

> Feature slug: `platform-foundation` · Root spec (no cross-spec upstream
> dependencies). Sizes: `XS`/`S`/`M`/`L` (CONVENTIONS.md §3.3, §10). Status
> legend: `todo`/`in_progress`/`blocked`/`done`. Cross-spec deps would be
> `<slug>#<id>` — there are none upstream here.

## Overview (task groups + critical path + parallelisation)

Eight task groups. **Critical path:** G0 (repo skeleton) → G1 (domain model)
→ G2 (persistence) → G3 (audit log). G4 (config/secrets) and G6 (observability)
can run in parallel with G1–G2 once G0 lands. G5 (encryption) depends on G4
(secret loader for keys) and feeds G2/G3. G7 (RBAC + identity) depends on G1/G4.
G8 (docs/CI) depends on G1 (schemas) and threads through every other group's
"done = code + tests + docs". Aim to merge G0–G3 first (the spine), then layer
G4–G8.

```
G0 ─┬─ G1 ─┬─ G2 ─── G3
    │      └─ G7
    ├─ G4 ─┬─ G5 ─(into G2/G3)
    │      └─ G7
    ├─ G6
    └─ G8 (depends on G1; gates every group's merge)
```

---

## Group 0: Repo skeleton & tooling — size S

- [ ] **0.1** Create the `src/cam/` layout from PTD §15 (`core/domain`,
  `core/audit`, `persistence`, `config`, `obs`, `security`, `docs`; empty
  `mcp_server/`, `sidecar/`, `connectors/`, `core/workflows`,
  `core/services`, `core/orchestrator` placeholders owned by other specs).
  — size S · parallel: no · depends on: none
  - **Acceptance:** `import cam` works; `ruff` + `mypy --strict` pass on an
    empty-but-typed skeleton; `pyproject.toml` pins the PTD §3 baseline
    versions.
  - **Focused tests:** (1) package imports; (2) `mypy --strict` clean;
    (3) `ruff check` clean.

- [ ] **0.2** Test + CI harness: `pytest`, `hypothesis`, coverage, and a CI
  workflow running lint + type + tests on a disposable Postgres.
  — size S · parallel: yes · depends on: 0.1
  - **Acceptance:** `pytest` runs green on an empty suite; CI spins up Postgres
    and runs the matrix.
  - **Focused tests:** (1) CI dry-run passes; (2) a trivial DB-touching test
    connects to the throwaway Postgres.

## Group 1: Domain model — size M

- [ ] **1.1** Implement `cam.core.domain` with `Contact`, `Matter`, `Document`,
  `Deadline`, `Communication`, `Task` **exactly per PTD §4**, with docstrings on
  every model and field (consumed by G8 doc generator).
  — size M · parallel: no · depends on: 0.1
  - **Acceptance:** field names/types match PTD §4 verbatim (incl. the
    `Literal[...]` enums and `Matter.key_dates: list[Deadline]`); `mypy --strict`
    clean; no immigration category hard-coded (CONVENTIONS.md §5, §8).
  - **Focused tests:** (1) construct a valid instance of each; (2) invalid
    enum value rejected; (3) `EmailStr`/optional fields behave; (4) forward-ref
    `Matter.key_dates` resolves.

- [ ] **1.2** Add shared supporting/value types proposed here for reuse:
  `ResidencyRegion` enum, `FeatureFlag`, and the `AuditRecord` schema (used by
  G3). Reserve the `ACL` name as a downstream-owned placeholder (no semantics).
  — size S · parallel: yes · depends on: 1.1
  - **Acceptance:** types importable and documented; `ACL` placeholder clearly
    marked as owned by `connector-framework`/`document-routing`.
  - **Focused tests:** (1) enum membership; (2) `AuditRecord` validates;
    (3) `FeatureFlag` defaults to disabled.

## Group 2: Persistence & migrations — size L (split as below)

- [ ] **2.1** SQLAlchemy 2 typed ORM models mirroring the six domain types +
  `audit_log`; JSONB for `external_ids`/`inputs`/`outputs`; FKs per PTD §4.
  — size M · parallel: no · depends on: 1.1, 1.2
  - **Acceptance:** metadata creates cleanly; types use `Mapped[...]`; no raw
    vendor blobs (CONVENTIONS.md §10).
  - **Focused tests:** (1) `create_all` on empty DB; (2) FK enforced;
    (3) JSONB round-trips a dict.

- [ ] **2.2** Repository pattern + `UnitOfWork`/session scope; domain⇄ORM mapping
  kept internal; concrete repos for all six entities + `AuditRepository`
  (append-only).
  — size M · parallel: no · depends on: 2.1
  - **Acceptance:** repos accept/return **domain** models only; a write + audit
    append commit atomically (supports write-before-complete, spec §5.2).
  - **Focused tests:** (1) CRUD round-trip per entity; (2) read returns domain
    model not ORM; (3) rollback discards both action + audit; (4) list-filter
    works.

- [ ] **2.3** Alembic env wired to ORM metadata; initial migration; `upgrade
  head` / `downgrade base` reversibility; CI guard that models == latest
  migration.
  — size S · parallel: no · depends on: 2.1
  - **Acceptance:** `upgrade head` then `downgrade base` clean; drift between
    models and migrations fails CI.
  - **Focused tests:** (1) up then down clean; (2) autogenerate shows no diff
    after migration; (3) drift case fails.

## Group 3: Audit log service — size M

- [ ] **3.1** `AuditService.record(...)` computing `record_hash =
  H(prev_hash || canonical(record))`; genesis sentinel; monotonic ordering;
  write-before-complete in the same UoW as the action.
  — size M · parallel: no · depends on: 2.2, 1.2, 5.1 (hash uses crypto hash
  util) · **note:** if G5 slips, use stdlib `hashlib` directly — hashing needs
  no secret.
  - **Acceptance:** appending N records yields a verifiable chain; record
    written before the wrapping action is "complete".
  - **Focused tests:** (1) genesis record valid; (2) chain of 3 verifies;
    (3) audit write failure rolls back the action; (4) timestamp is UTC.

- [ ] **3.2** `AuditService.verify(range?)` + DB-level immutability (revoke
  `UPDATE`/`DELETE` for app role + integrity trigger).
  — size S · parallel: no · depends on: 3.1
  - **Acceptance:** tamper (mutate/delete/reorder) makes `verify()` fail at the
    first affected link; direct `UPDATE`/`DELETE` rejected.
  - **Focused tests:** (1) mutate one record → verify fails at its index;
    (2) delete one → fails; (3) `UPDATE` rejected by DB; (4) clean chain passes.

- [ ] **3.3** `AuditService.export(format="jsonl")` producing a portable export
  whose chain re-verifies after export.
  — size S · parallel: yes · depends on: 3.1
  - **Acceptance:** export → re-verify passes; export contains stored hashes;
    access is RBAC-scoped (uses G7).
  - **Focused tests:** (1) export N, re-verify ok; (2) export is valid JSONL;
    (3) round-trip count matches.

## Group 4: Config, secrets & feature flags — size M

- [ ] **4.1** `Settings` (Pydantic-Settings), 12-factor, total validation, fail
  fast, secret-free error messages; includes `residency_region`.
  — size S · parallel: yes · depends on: 0.1
  - **Acceptance:** complete env loads; missing/malformed required setting fails
    fast naming the field but no secret value.
  - **Focused tests:** (1) valid env loads; (2) missing required → precise
    error; (3) bad type → error; (4) error text carries no secret value.

- [ ] **4.2** `SecretLoader` protocol + `EnvSecretLoader` (v1 default) and
  interface-compatible `Vault`/`KMS` stubs; `get_secret` never logs values;
  `SecretNotFound` typed error.
  — size S · parallel: yes · depends on: 4.1
  - **Acceptance:** env-backed secret resolves; backend selectable by config;
    no secret value ever logged.
  - **Focused tests:** (1) resolve a secret; (2) `SecretNotFound` raised;
    (3) backend switch works; (4) log capture shows no value.

- [ ] **4.3** `FeatureFlags.is_enabled(workflow_key)` with default-off for
  unreleased workflows; per-workflow rollout.
  — size XS · parallel: yes · depends on: 4.1
  - **Acceptance:** flags read from config; unknown/unreleased workflow → off.
  - **Focused tests:** (1) enabled flag true; (2) default off; (3) toggle.

## Group 5: Encryption utilities — size M

- [ ] **5.1** `cam.security.crypto`: AES-256-GCM `encrypt`/`decrypt`, envelope
  (DEK wrapped by KEK from secret loader), `DecryptionError` on auth failure;
  rotation-friendly.
  — size M · parallel: no · depends on: 4.2
  - **Acceptance:** `decrypt(encrypt(x,k),k)==x` for all inputs; wrong key →
    `DecryptionError`; ciphertext≠plaintext for non-empty input.
  - **Focused tests:** (1) round-trip; (2) wrong key fails; (3) tampered
    ciphertext fails GCM; (4) empty-input handled.

- [ ] **5.2** `EncryptedStr` SQLAlchemy column type for column-level OAuth-token
  encryption; transparent encrypt-on-write / decrypt-on-read.
  — size S · parallel: no · depends on: 5.1, 2.1
  - **Acceptance:** stored ciphertext at rest; value decrypts on load; rotation
    re-wraps DEK without eager re-encrypt.
  - **Focused tests:** (1) write→DB row is ciphertext; (2) read→plaintext;
    (3) post-rotation old rows still decrypt.

## Group 6: Observability bootstrap — size M

- [ ] **6.1** `configure_observability(settings)` wiring structlog (JSON,
  `run_id` correlation), OpenTelemetry tracing, Prometheus metrics in one call.
  — size M · parallel: yes · depends on: 0.1, 4.1
  - **Acceptance:** logs carry bound `run_id`; a span is exported; a counter
    increments.
  - **Focused tests:** (1) `run_id` appears in log record; (2) span recorded;
    (3) metric increments; (4) idempotent re-configure.

- [ ] **6.2** Shared PII scrubber (one ruleset) applied as a structlog
  processor, an OTel span processor, and a metric-label sanitiser; immigration
  PII patterns included (configurable list).
  — size M · parallel: no · depends on: 6.1
  - **Acceptance:** a field redacted in logs is redacted in traces and metric
    labels; secrets never appear; high-cardinality PII labels dropped/bucketed.
  - **Focused tests:** (1) A-number/passport/SSN redacted in a log; (2) same in
    a span attribute; (3) PII not used as a metric label; (4) `cam_pii_redactions_total`
    increments.

## Group 7: RBAC model & agent service identity — size M

- [ ] **7.1** `Role`, `Permission`, `Principal`, declarative permission matrix,
  `authorize(principal, permission, resource?)` with **default-deny**.
  — size M · parallel: no · depends on: 1.2, 4.1
  - **Acceptance:** any (role, permission) not explicitly granted is denied;
    matrix is data, not scattered `if`s.
  - **Focused tests:** (1) granted pair allowed; (2) ungranted denied;
    (3) unknown role denied; (4) resource-scoped check.

- [ ] **7.2** `AGENT_SERVICE_PRINCIPAL`: constrained service identity with an
  explicit minimal grant set; any permission outside it denied.
  — size S · parallel: no · depends on: 7.1
  - **Acceptance:** agent principal denied all non-granted permissions;
    grant set is enumerated and small (ASSUMPTION (confirm) pending firm matrix).
  - **Focused tests:** (1) granted agent action allowed; (2) out-of-scope action
    denied; (3) escalation attempt denied.

## Group 8: Docs & CI discipline — size M

- [ ] **8.1** Schema-derived doc generator: render tool/domain docs from
  Pydantic schemas + docstrings (the *discipline*; concrete tools downstream).
  — size M · parallel: yes · depends on: 1.1
  - **Acceptance:** running the generator produces docs for the domain models;
    a model lacking a description is reported.
  - **Focused tests:** (1) generates expected doc for a model; (2) missing
    description flagged; (3) deterministic output.

- [ ] **8.2** Schema-drift CI check (fails build on drift or missing
  description) + `docs/adr/` convention + a "done = code + tests + docs"
  contributor checklist.
  — size S · parallel: yes · depends on: 8.1
  - **Acceptance:** CI fails when a schema changes without regenerating docs and
    passes when in sync; ADR template present.
  - **Focused tests:** (1) drift case fails CI; (2) in-sync passes; (3) ADR
    template renders.

---

## Dependency graph (intra-spec + cross-spec)

```
0.1 → 0.2
0.1 → 1.1 → 1.2
1.1,1.2 → 2.1 → 2.2 → 3.1 → 3.2
                 2.1 → 2.3
                 3.1 → 3.3
0.1 → 4.1 → 4.2 → 5.1 → 5.2 (also ← 2.1)
      4.1 → 4.3
      4.1 → 6.1 → 6.2
1.2,4.1 → 7.1 → 7.2
1.1 → 8.1 → 8.2
3.1 ← 5.1 (soft: stdlib hashlib fallback if 5.1 slips)
3.3 ← 7.x (export access RBAC-scoped)
```

**Cross-spec:** none upstream. Downstream consumers (`connector-framework#*`,
`workflow-orchestration#*`, all workflow slugs) depend on this spec's tasks but
are tracked in their own files.

## Definition of done (code + tests + docs)

A task group is **done** only when (CONVENTIONS.md §3.6, PTD §16):

1. **Code** merged, `ruff` + `mypy --strict` clean.
2. **Tests** — its 2–8 focused tests pass, plus the relevant properties in
   `pbt-properties.md` (audit chain, encryption round-trip, RBAC soundness,
   config totality, domain round-trip) are green.
3. **Docs** — schema-derived docs regenerated and in sync (8.2 CI passes); any
   significant decision recorded as an ADR; this spec updated if behaviour
   changed. No group merges with the schema-drift check red.
