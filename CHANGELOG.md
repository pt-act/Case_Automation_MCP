# Changelog

All notable changes to this project will be documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)  
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

### Added
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
- `cam.sidecar.main` — FastAPI app factory with webhook and approval routers wired; health endpoint; startup/shutdown hooks

### Changed
- `observability.py` — removed `structlog.stdlib.add_logger_name` processor (incompatible with `PrintLoggerFactory`; caused 91 test failures in full-suite runs)
- `tests/conftest.py` — added `_reset_global_singletons` autouse fixture to prevent state leakage between test files
- `connectors/middleware.py` — replaced `random.uniform` with `secrets.SystemRandom().uniform` in jitter backoff (Bandit B311)
- `core/orchestrator/engine.py` — same jitter fix
- `status_update/draft.py` — `_DEFAULT_RECIPIENT_ROLES` changed to `frozenset` (immutable)
- 14 test files — removed blanket `pytestmark = pytest.mark.asyncio` (was generating 115 warnings for sync test functions); `asyncio_mode = "auto"` in `pyproject.toml` already handles async tests

### Fixed
- Test suite now 403/403 passing when run in file-alphabetical order (was 312/403 before the structlog + singleton fixes)
- Asyncio warning count reduced from 115 to 0 in test runs

---

## [0.1.0] — 2026-05-30

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
