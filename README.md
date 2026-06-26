# Case Automation MCP Server

A Python **Model Context Protocol (MCP) server** for any caseworked, deadline-driven, document-heavy practice — it unifies case-management, CRM, email, and document systems behind one AI-orchestratable surface, with mandatory human-in-the-loop gates, a fully auditable hash-chained log, and confidentiality-aware document routing. The engine is domain-agnostic and configured via swappable **domain packs**; immigration ships as the reference pack.

> Status: **Planning → MVP** | Vendors not yet confirmed (see [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md))

---

## What it does

| Capability | Tool / workflow |
|---|---|
| Client intake (lead → matter) | `intake.run` |
| Status-update emails | `status_update_email` workflow |
| Document generation (template → DOCX/PDF) | `document.generate`, `form.prefill` |
| Document routing (classify, file, ACL) | `document.route` |
| Deadline tracking & reminders | `deadline.compute`, `deadline.schedule` |
| QC verification before external actions | `qc.verify` |
| Workflow approval gates | `approval.decide` / web UI / email links |

Every external or irreversible action is blocked behind a human-approval gate.  
Every state change writes to a tamper-evident hash-chained audit log before completion.

---

## Architecture

```
MCP client (Claude / agent)
        │
        ▼
┌──────────────────────────────────┐
│        MCP Server (FastMCP)       │  ← tools · resources · prompts
│                                  │
│  Workflow Orchestrator           │  ← durable state-machine, gates
│  ├── client-intake               │
│  ├── status-update-emails        │
│  ├── document-generation         │
│  └── document-routing            │
│                                  │
│  Core Services                   │
│  ├── data-extraction (OCR + LLM) │
│  ├── qc-verification             │
│  └── deadline-engine             │
│                                  │
│  Connector Layer (ports only)    │  ← CRM · Case · Email · DocStore
└──────────────────────────────────┘
        │
  Sidecar (FastAPI)
  ├── POST /webhooks/{connector}
  └── GET/POST /approvals/{token}

Persistence: PostgreSQL + Redis
```

See [`docs/PTD.md`](concept/PTD.md) for the full technical design and [`manifest.yml`](manifest.yml) for the 10-spec dependency DAG.

### Domain packs (Under Development)

The engine is fixed; everything practice-specific lives in a swappable **domain pack** loaded once at startup. A pack supplies terminology (Matter / Case / Engagement / Claim; the confidentiality label), case types and intake checklists, deadline rule sets, document classes, QC packet kinds, the confidentiality/restriction policy, RBAC roles, and PII pattern additions. One active pack per deployment, selected via the `CAM_DOMAIN_PACK` environment variable. There is **no implicit default** — if `CAM_DOMAIN_PACK` is unset or unknown the server refuses to serve. Immigration ships as the reference pack (`packs/immigration`, 1:1 with today's behaviour); `packs/consulting` proves generality. A pack can only **tighten** the two engine-owned safety guarantees (the confidentiality gate and the PII redaction floor), never weaken them.

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | **3.12+** | [python.org](https://www.python.org/downloads/) |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| PostgreSQL | 16 | Docker or local |
| Redis | 7 | Docker or local |

> **All commands use `uv run …`**. Never use bare `python3 -m pytest` — the project requires Python 3.12+ and `uv` manages the correct interpreter.

---

## Quickstart

```bash
# 1. Clone
git clone https://github.com/pt-act/Case_Automation_MCP.git
cd Case_Automation_MCP

# 2. Install (including dev dependencies)
uv sync --extra dev

# 3. Copy and fill environment variables
cp .env.example .env
# edit .env — see "Environment variables" below

# 4. Start backing services (Docker)
docker compose up -d postgres redis

# 5. Run database migrations
uv run alembic upgrade head

# 6. Run the test suite
uv run pytest -q

# Expected: ~403 passed, 1 skipped
```

---

## Running locally

```bash
# MCP server (stdio mode for local agents)
uv run python -m cam.sidecar.main

# Sidecar (webhooks + approval UI) on port 8001
uv run uvicorn cam.sidecar.main:app --port 8001 --reload
```

---

## Running tests

```bash
# Full unit suite (no Postgres/Redis needed)
uv run pytest -q

# With coverage
uv run pytest --cov=cam --cov-report=term-missing -q

# DB-dependent tests (requires live Postgres)
CAM_DATABASE_URL=postgresql+asyncpg://cam:cam_test_pw@localhost:5432/cam_test \
  uv run pytest tests/test_audit.py tests/test_persistence.py -q

# Single spec
uv run pytest tests/test_deadline_engine.py -v
```

---

## Environment variables

Copy `.env.example` to `.env` and fill in values:

| Variable | Required | Description |
|---|---|---|
| `CAM_DOMAIN_PACK` | Yes | Active domain pack id (e.g. `immigration`, `consulting`). No implicit default — unset/unknown means refuse to serve |
| `CAM_DATABASE_URL` | Yes | PostgreSQL async URL (`postgresql+asyncpg://...`) |
| `CAM_REDIS_URL` | Yes | Redis URL (`redis://localhost:6379/0`) |
| `CAM_SECRET_BACKEND` | No | `env` (default) · `vault` · `kms` |
| `CAM_ENCRYPTION_KEK` | Yes | Base64-encoded 32-byte Key Encryption Key |
| `CAM_RESIDENCY_REGION` | No | Data-residency region (default `us-east-1`) |
| `CAM_EXTRACTION_ALLOW_EXTERNAL_INFERENCE` | No | `false` (default) — LLM residency gate |
| `CAM_LOG_LEVEL` | No | `INFO` (default) |

> **Never commit real keys.** The CI workflow uses a zero-filled test KEK (`AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=`). Production must use a real 256-bit key from your secret store.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: Intake services not configured` | `configure_services()` not called | Call it at app startup before any workflow run |
| `KeyError: workflow 'intake' not registered` | `register_intake_workflow()` not called | Call at startup after `configure_services()` |
| `alembic.util.exc.CommandError: Can't locate revision` | Migration out of sync | `uv run alembic upgrade head` |
| Tests fail on `AttributeError: 'PrintLogger'` | structlog re-configured across tests | `_reset_global_singletons` fixture in conftest.py handles this; ensure you have the latest conftest |
| `requires-python >=3.12` error | Wrong Python interpreter | Use `uv run pytest` not `python3 -m pytest` |

---

## Repository layout

```
src/cam/
├── config/              12-factor settings, secret loaders, feature flags
├── packs/               Domain packs: base contract + immigration (reference) + consulting
├── core/
│   ├── audit/           Hash-chained audit log service
│   ├── domain/          Canonical Pydantic v2 domain model (Contact, Matter, …)
│   ├── orchestrator/    Durable workflow engine, gates, DSL
│   ├── services/
│   │   ├── deadline/    Business-day computation, reminders, dead-man's-switch
│   │   ├── extraction/  Text + OCR + LLM structuring pipeline
│   │   └── qc/          Composable check registry (7 checks, any-fail-blocks)
│   └── workflows/
│       ├── document_gen/    Template → DOCX/PDF pipeline
│       ├── document_routing/ Classify, name, file, ACL, confidentiality/restriction gate (privilege in the immigration pack)
│       ├── intake/          Lead → matter intake workflow
│       └── status_update/   Status-change → draft → gate → send
├── connectors/          Ports (Protocols) + reference adapters + webhook ingestion
├── mcp_server/          FastMCP tool/resource/prompt definitions
├── obs/                 structlog + OpenTelemetry + Prometheus + PII scrubber
├── persistence/         SQLAlchemy 2 ORM, repositories, Alembic migrations
├── security/            AES-256-GCM crypto, RBAC, agent service identity
└── sidecar/             FastAPI app — webhooks, approval UI, scheduler

tests/                   403 unit + PBT tests (no live services required)
docs/
├── adr/                 Architecture Decision Records
├── workflows/           Per-workflow technical reference docs
├── ASSUMPTIONS.md       Unconfirmed design decisions pending firm sign-off
└── AUDIT_REPORT.md      Code quality audit (2026-06-01)
specs/                   Behavioural contracts (spec.md, tasks.md, pbt-properties.md)
concept/                 PRD.md and PTD.md — product + technical design
```

---

## CI overview

| Job | Trigger | What it does |
|---|---|---|
| `lint-and-type` | Every PR | `ruff check` + `mypy --strict` |
| `test` | Every PR | Alembic migrations → `pytest --cov=cam` (Postgres 16 + Redis 7) |
| `schema-drift` | Every PR | Regenerates domain docs and fails if they drift |
| `security` | Every PR + nightly | `bandit` SAST + `pip-audit` dependency check |

---

## Security notes

- **No credentials in source.** Secrets are read from the secret store at runtime.
- **PII never in logs/traces.** A shared scrubber enforces an engine baseline (email, phone, SSN/ITIN, DOB) that packs can only extend; the immigration pack adds A-numbers and passport numbers. Redaction runs across all three observability signals.
- **Confidentiality/restriction gate is non-overridable (privilege in the immigration pack).** A restricted document (`Document.restricted=True`, with `privileged` as a permanent alias) can never reach an external recipient, regardless of approval tokens. The gate fails closed and is engine-owned; a pack may only tighten it.
- **Every write audited before completion.** Hash-chained, append-only, tamper-evident.
- See [`concept/PTD.md §12`](concept/PTD.md) for the full security design.

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

See [`LICENSE`](LICENSE).

## Assumptions pending firm confirmation

See [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md) for the full list of design decisions that require the firm to confirm (vendors, rule contents, case types, policies).


## License

See [`LICENSE`](LICENSE).

## Assumptions pending firm confirmation

See [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md) for the full list of design decisions that require the firm to confirm (vendors, rule contents, case types, policies).
