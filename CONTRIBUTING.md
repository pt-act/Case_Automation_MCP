# Contributing to Case Automation MCP Server

Thank you for contributing. This document covers the essential workflow.

## Prerequisites

- Python 3.12+ and `uv` installed (see `README.md`)
- Familiarity with the architecture: read `concept/PTD.md` and `manifest.yml` first

## Development workflow

```bash
# Install all dependencies including dev tools
uv sync --extra dev

# Run the test suite (must pass before opening a PR)
uv run pytest -q

# Run linting and type checks
uv run ruff check src/ tests/
uv run mypy src/cam/

# Run security scans
uv run bandit -r src/cam --severity-level medium
uv run pip-audit
```

## Definition of done

A task / PR is **done** only when:

1. **Code** — implements the contract in the relevant `specs/<slug>/spec.md`
2. **Tests** — 2–8 focused tests pass; relevant Hypothesis PBT properties pass
3. **Docs** — `docs/workflows/<name>.md` updated; tool/resource schema has a description; CI schema-drift check passes
4. **No regressions** — full test suite stays green (`uv run pytest -q`)
5. **No PII in logs** — the PII scrubber covers new field types

## Branching convention

| Branch | Purpose |
|---|---|
| `feat/<slug>` | New feature or spec implementation |
| `fix/<description>` | Bug fix |
| `chore/<description>` | Tooling, CI, dependency updates |
| `docs/<description>` | Documentation only |

## Adding a new connector adapter

1. Implement the port Protocol in `src/cam/connectors/<vendor>/adapter.py`
2. Add a normaliser for each webhook event type
3. Register via `register_connector("<name>", ..., normaliser=normaliser)`
4. Pass the contract-test harness: `from tests.connectors.harness import run_contract`
5. Ship a complete `CONNECTOR.md` (auth, scopes, endpoints, webhook events, rate limits, quirks)
6. No merge without all five steps (CI enforces `CONNECTOR.md` presence)

## Adding a new QC check

1. Create a class with `id`, `version`, `applies_to`, `severity_policy`, `run()`, `describe()`
2. Call `register_check(MyCheck())` at startup
3. Add it to `src/cam/core/services/qc/checks/__init__.py` and `ALL_CHECKS`
4. Add tests: pass/warn/fail/skip for each verdict path
5. No code edits to the registry core needed (FR-16)

## Security guidelines

- **Never commit credentials.** Use `.env` (gitignored) for local dev.
- **No raw PII in logs.** Run `grep -r "log\." src/cam | grep "email\|ssn\|a_number\|passport"` and check that values go through the PII scrubber.
- **No naive date arithmetic** in the deadline engine — always via `Calendar`.
- **Privilege gate is non-overridable** — any PR that routes around `PrivilegeGate.check()` for external targets will be rejected.

## Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(intake): add A-number validation to IntakeFields
fix(observability): remove add_logger_name incompatible with PrintLoggerFactory
chore(ci): add security scan job with Bandit and pip-audit
docs(assumptions): centralise ASSUMPTION (confirm) items
```

## PR checklist

- [ ] Tests pass: `uv run pytest -q`
- [ ] Linting passes: `uv run ruff check src/ tests/`
- [ ] Type check passes: `uv run mypy src/cam/`
- [ ] Security scan passes: `uv run bandit -r src/cam --severity-level medium`
- [ ] Relevant spec / workflow doc updated
- [ ] No new `ASSUMPTION (confirm)` added without an entry in `docs/ASSUMPTIONS.md`
- [ ] PR description explains *why*, not just *what*
