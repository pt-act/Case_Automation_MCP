# Changelog

All notable changes to this project will be documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)  
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

### Added
- **`casemat` CLI** (`src/cam/cli/`) — zero-friction local bootstrap:
  - `casemat start [--domain <pack>]` — spins up Postgres + Redis (docker-compose), runs Alembic migrations, seeds sample data (5 contacts, 3 matters, 2 deadlines), validates the domain pack (refuses to serve on invalid — NFR-9), starts the sidecar, opens the browser
  - `casemat init --connector <type>` — scaffolds a connector adapter from templates (client.py, adapter.py, health.py, README.md, contract test stub)
  - `casemat status` — shows running/stopped for Postgres, Redis, Sidecar
  - `casemat stop` — tears down docker-compose services
  - `casemat` console_scripts entrypoint (install with `uv sync --extra dev`)
- **Pack inspector endpoint** (`src/cam/sidecar/pack_inspector.py`):
  - `GET /packs` — lists all registered domain packs with name, version, description, `is_active` flag
  - `GET /packs/{name}/inspect` — returns the resolved configuration: terminology, case types, deadline rules (count + IDs), document classes, restriction policy (label + default_restricted + predicate count), RBAC roles, PII pattern counts (labels only, never regex strings), feature flags
  - Unknown pack → 404 with registered pack names list
  - Makes the tighten-only restriction policy and PII floor observable at runtime
- **Phase 2.1: Distributed run locking** (`src/cam/core/orchestrator/locking.py`) — `RunLock` via Redis SETNX prevents concurrent execution of the same workflow run across Celery workers. TTL-based auto-expiry (300s). Degrades to no-op when Redis unavailable.
- **Phase 2.2: Redis-backed circuit breaker** (`src/cam/connectors/redis_health.py`) — `RedisConnectorHealth` extends `ConnectorHealth` with Redis-backed distributed circuit state. `is_open` checks Redis flag across workers. Async variants for async middleware. Degrades to in-process behavior when Redis unavailable.
- **Deployment wiring** — PostgresRunStore wired into sidecar lifespan (falls back to InMemoryRunStore). Celery tasks use `_make_production_deps()` when `CAM_DATABASE_URL` is set. `Dockerfile` (multi-stage, Python 3.12-slim, WeasyPrint deps, healthcheck). `docker-compose.yml` (Postgres 16, Redis 7, sidecar, Celery worker, beat).
- **5 new feature specs** in `.agents/specs/`: casemat-cli (mini), pack-inspector (mini), chaos-runner (full 4-phase), replay-harness (mini), run-timeline (mini). New `tooling` milestone in manifest.yml.
- `list_registered_pack_names()` — public API in `cam.packs` for querying registered pack names
- `typer>=0.12` — CLI framework dependency

- **Domain packs** (`src/cam/packs/`) — domain-agnostic configuration seam so the engine can run any practice, not just immigration:
  - `DomainPack` contract + `Terminology` + tighten-only `RestrictionPolicy`; immutable (frozen) pack fields
  - Pack registry with single-active-pack selection, `CAM_DOMAIN_PACK` config wiring, optional `cam.packs` entry-point plugin loading, and fail-closed load-time validation (refuse to serve on an invalid/unknown pack)
  - PII redaction composed as `ENGINE_PII_BASELINE ∪ active_pack.pii_patterns` (the universal floor — email, phone, SSN/ITIN, DOB — can never be shrunk by a pack)
  - `Document.restricted` canonical field with `privileged` as a permanent read/write alias (no migration; backed by the existing column)
  - `run_pack_conformance()` harness; two reference packs — `immigration` (1:1 parity with prior behaviour) and `consulting` (proof of generality) — each shipping a `PACK.md`
  - `apply_pack()` populates intake/routing/deadline runtime config from the active pack at startup; `qc` privilege check reads the pack's restriction policy
  - **G6 extraction complete** — the immigration domain values are now owned by the immigration pack and read from the active pack: intake case types (moved out of `intake/config.py`, which keeps only a neutral fallback), the `a_number` identifier format (new pack `identifier_patterns`, read by the intake validator), and the document-generation form map (new pack `prefill_forms`, read by `form.prefill`). Engine core contains no immigration value — enforced by the `test_no_immigration_value_hardcoded_in_core` guard (now a hard assert). Full non-DB suite: 450 passed with the immigration pack active (parity preserved)
- `scripts/check_pack_docs.py` + CI `pack-docs` job — fails the build if any pack directory lacks a complete `PACK.md` (domain-packs task 7.5)
- `docs/GENERALIZATION_PLAN.md` — domain-agnostic evolution plan documenting the transition from immigration-specific to domain-configurable
- `README.md` — top-level project documentation with quickstart, architecture, environment variables, and troubleshooting
- `.python-version` — pins Python 3.12 for `uv` and tools
- `.env.example` — environment variable template with generation instructions
- `docs/ASSUMPTIONS.md` — centralised log of 65 design decisions pending firm sign-off
- `CONTRIBUTING.md` — development workflow, PR checklist, security guidelines
- `CHANGELOG.md` — this file
- CI: `security` job (Bandit SAST + pip-audit dependency check) — runs on every PR and weekly
- CI: `--cov-fail-under=70` coverage floor enforced in test job
- CI: Python 3.12 and 3.13 test matrix
- CI: Weekly nightly scheduled security scan (Monday 03:00 UTC)
- CI: `schema-drift` job — fails if `docs/domain_model.md` is out of sync with the code model
- CI: `pack-docs` job — fails if any domain pack lacks a complete `PACK.md`
- `cam.sidecar.main` — FastAPI app factory with webhook and approval routers wired; health endpoint; startup/shutdown hooks

### Changed
- `manifest.yml` — project status `planning` → `executing`; all 11 original specs marked `implemented` with `impl_note`; 5 new specs added (`planned`); new `tooling` milestone; `spec_level` field (full/mini); 22 history entries
- `WorkflowEngine.execute()` — acquires `RunLock` before executing, delegates to `_execute()` (internal), releases lock in `finally`. Skips run if lock held by another worker.
- `src/cam/sidecar/main.py` — wired PostgresRunStore + pack inspector router
- `src/cam/sidecar/celery_app.py` — `_make_deps()` dispatcher chooses production/in-memory deps based on `CAM_DATABASE_URL`
- `src/cam/packs/base.py` — added `list_registered_pack_names()` public function
- `pyproject.toml` — added `typer>=0.12` dependency, `casemat` console_scripts entrypoint, `[project.optional-dependencies.pdf]` for WeasyPrint
- `.env.example` — added `CAM_PDF_ENGINE` documentation (auto/libreoffice/weasyprint/none)
- ADR-4: updated from "configurable stub" to "implemented (LibreOffice + WeasyPrint, auto-detect)"
- ADR-13: recorded — Secret store = SOPS + age (or .env gitignored for dev)

- `observability.py` — removed `structlog.stdlib.add_logger_name` processor (incompatible with `PrintLoggerFactory`; caused 91 test failures in full-suite runs)
- `tests/conftest.py` — added `_reset_global_singletons` autouse fixture to prevent state leakage between test files; also runs the entire suite under the immigration pack (parity net)
- `connectors/middleware.py` — replaced `random.uniform` with `secrets.SystemRandom().uniform` in jitter backoff (Bandit B311)
- `core/orchestrator/engine.py` — same jitter fix; replaced bare `assert` with explicit `if … raise AssertionError` (Aikido SAST)
- `core/services/qc/registry.py` — replaced bare `assert` with explicit guard (Aikido SAST)
- `status_update/draft.py` — `_DEFAULT_RECIPIENT_ROLES` changed to `frozenset` (immutable)
- 14 test files — removed blanket `pytestmark = pytest.mark.asyncio` (was generating 115 warnings for sync test functions); `asyncio_mode = "auto"` in `pyproject.toml` already handles async tests
- `tests/test_connector_middleware.py` — `pytest.raises(Exception)` replaced with specific exception types (ruff B017)
- `tests/test_connector_reference.py` — same B017 fix
- `tests/test_config.py`, `tests/test_extraction_pipeline.py`, `tests/test_extraction_types.py` — same B017 fix (use `pydantic.ValidationError`)
- `tests/test_orchestrator_dsl.py` — same B017 fix (use `(ValueError, KeyError)`)
- `tests/test_persistence.py` — same B017 fix (use `sqlalchemy.exc.IntegrityError`)
- 4 exception classes renamed with `Error` suffix per ruff N818: `SecretNotFound` → `SecretNotFoundError`, `ConnectorNotFound` → `ConnectorNotFoundError`, `IllegalTransition` → `IllegalTransitionError`, `TemplateNotFound` → `TemplateNotFoundError` (all references updated in `src/` and `tests/`)
- `src/cam/packs/` — 78 bare generic types annotated with type parameters (`dict` → `dict[str, Any]`, `list` → `list[Any]`, `Callable` → `Callable[..., Any]`, etc.) for `mypy --strict` compliance
- `src/cam/core/services/qc/checks/` — 7 QC check `run()` methods given explicit `packet: VerificationPacket` parameter types (mypy `no-untyped-def`)
- `src/cam/sidecar/main.py` — `lifespan()` given `AsyncIterator[None]` return type annotation (mypy `no-untyped-def`)
- `src/cam/connectors/ports.py` — `Event` import added via `TYPE_CHECKING` to resolve forward reference in `TriggerSink` protocol
- `src/cam/core/services/qc/registry.py` — `VerificationPacket` import added to resolve `name-defined` errors
- `src/cam/core/services/qc/checks/privilege.py` — `_active_restriction()` given explicit `-> RestrictionPolicy | None` return type; `RestrictionPolicy` imported from `cam.packs.base`
- `src/cam/core/orchestrator/gates.py` — `None` check added for token payload before indexing; Protocol method reformatted for mypy `empty-body`
- `src/cam/sidecar/approvals.py` — `None` check for payload; redundant `cast()` removed; form parsing simplified
- `src/cam/core/services/deadline/reconcile.py` — variable renamed to avoid `no-redef`; `isinstance` check uses bare `dict` instead of parameterized generic
- `src/cam/core/services/extraction/ocr.py` — `Image.frombytes` size argument changed from `list` to `tuple` (mypy `arg-type`)
- `src/cam/sidecar/webhooks.py` — `_noop_trigger_sink` renamed to `_NoopTriggerSink` (N801); duplicate dict key `"rejected_bad_signature"` removed (F601)
- `src/cam/core/orchestrator/engine.py` — `_ApprovalChannel` renamed to `_approval_channel` (N806)
- `src/cam/security/crypto.py` — `# noqa: E402` added to SQLAlchemy imports placed after function definitions (intentional placement)
- `src/cam/core/services/deadline/calendar.py` — `# noqa: B019` added to `@lru_cache` on instance method (bounded cache on singleton)
- `.github/workflows/ci.yml` — 3rd-party GitHub Actions pinned to commit SHAs (Aikido SAST)
- `pyproject.toml` — `bandit>=1.7` and `pip-audit>=2.7` added to dev dependencies
- Copyright holder changed from rADICor RNA to pt-act; LICENSE updated accordingly
- `docs/PRD.md`, `docs/PTD.md`, `docs/ASSUMPTIONS.md` — repositioned domain-agnostic (immigration = reference pack)
- `docs/domain_model.md` — regenerated to reflect `Document.restricted` canonical field and domain-agnostic entity descriptions

### Fixed
- **CI: all 6 jobs green** (was 4/6 failing):
  - **ruff check** — 471 lint errors → 0
  - **mypy --strict** — 128 type errors → 0
  - **Security scan** — bandit not found → installed; pip-audit 30 CVEs → 0
  - **Tests** — `ModuleNotFoundError: No module named 'tests'` → fixed by creating `tests/__init__.py`
  - **Schema drift** — `docs/domain_model.md` out of sync → regenerated
  - **Pack docs** — missing `PACK.md` files → added for immigration and consulting
- Bandit SAST B608 (SQL injection in seed data) and B104 (0.0.0.0 binding) — suppressed with `# nosec` (parameterized SQL, intentional Docker binding)
- Deprecated service shims removed: `configure_services()`, `get_services()`, `configure_status_update_services()`, `_services` globals (Phase 1.5)

- Test suite now 403/403 passing when run in file-alphabetical order (was 312/403 before the structlog + singleton fixes)
- Asyncio warning count reduced from 115 to 0 in test runs
- **CI: all 6 jobs green** (was 4/6 failing):
  - **ruff check** — 471 lint errors → 0 (auto-fixed 343: unused imports F401, unsorted imports I001, deprecated annotations UP017/UP035/UP037; manually fixed 128: line-length E501, raise-without-from B904, assert-raises-exception B017, exception naming N818, unused variables F841, and more)
  - **mypy --strict** — 128 type errors → 0 (78 bare generics parameterized, 13 `no-any-return` suppressed, 8 untyped functions annotated, 4 Celery decorators typed, 3 missing imports added, 5 None-indexing fixed, 4 `isinstance` with parameterized generics fixed)
  - **Security scan** — `bandit` not found → installed via dev deps; `pip-audit` — 30 known CVEs in 6 packages → 0 (cryptography 48→50, mcp 1.27→1.29, pillow 12.2→12.3, pydantic-settings 2.14→2.15, python-multipart 0.0.30→0.0.32, starlette 1.2→1.6)
  - **Tests (Python 3.12 & 3.13)** — `ModuleNotFoundError: No module named 'tests'` → fixed by creating `tests/__init__.py`
  - **Schema drift check** — `docs/domain_model.md` out of sync → regenerated
  - **Pack docs check** — missing `PACK.md` files → added for immigration and consulting packs
- `tests/__init__.py` created — `tests/` is now an importable package, fixing cross-test imports (`test_connector_reference.py` imports `from tests.connectors.harness`)

### Security
- 3rd-party GitHub Actions pinned to commit SHAs (Aikido SAST — supply-chain protection)
- Bare `assert` statements replaced with explicit guards in `engine.py` and `registry.py` (Aikido SAST — assertions stripped in `-O` mode)
- 30 dependency CVEs patched: cryptography (3 CVEs + 1 GHSA), mcp (1 CVE), pillow (20 CVEs), pydantic-settings (1 GHSA), python-multipart (1 CVE), starlette (2 CVEs)

---

## [0.1.0] — 2026-05-31

### Added
- Initial implementation of all 10 specs across 3 layers:
  - **Layer 1 (Foundations):** `platform-foundation`, `connector-framework`, `workflow-orchestration`
  - **Layer 2 (Core Services):** `data-extraction`, `qc-verification`, `deadline-engine`
  - **Layer 3 (Workflows):** `client-intake`, `status-update-emails`, `document-generation`, `document-routing`
- 403 focused tests + ~30 Hypothesis PBT properties
- 4 Alembic migrations covering all domain and orchestrator tables
- SHA-256 hash-chained append-only audit log
- AES-256-GCM envelope encryption for OAuth tokens and PII fields
- Non-overridable privilege gate (PBT-proven)
- Idempotency at all three layers (run, step, send)
- PII scrubber across structlog, OpenTelemetry spans, and Prometheus labels
- GitHub Actions CI with lint, type check, test (Postgres 16 + Redis 7), and schema-drift check
