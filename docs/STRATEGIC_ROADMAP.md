# Strategic Roadmap — Case Automation MCP Server
## Future Development: Scalability · Algorithmic Optimisation · Advanced Features

**Version:** 1.0 | **Date:** 2026-06-01  
**Authors:** Engineering + Product  
**Based on:** Current codebase audit (91/100, Grade A−) + 10 completed specs + confirmed architecture (Python 3.12+, FastMCP, FastAPI, SQLAlchemy 2, Celery beat, PostgreSQL, Redis)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Baseline](#2-current-baseline)
3. [Phase 1 — Production Hardening (Months 1–3)](#3-phase-1)
4. [Phase 2 — Scalability (Months 3–9)](#4-phase-2)
5. [Phase 3 — Algorithmic Optimisation (Months 6–12)](#5-phase-3)
6. [Phase 4 — Advanced Feature Integration (Months 9–18)](#6-phase-4)
7. [Phase 5 — Platform & Multi-Tenancy (Months 15–24)](#7-phase-5)
8. [Risk Register](#8-risk-register)
9. [Technology Decisions](#9-technology-decisions)
10. [Prioritisation Matrix](#10-prioritisation-matrix)

---

## 1. Executive Summary

The Case Automation MCP Server is architected correctly for a production immigration law practice. The foundation — vendor-agnostic ports, durable state-machine workflows, non-overridable privilege gates, and hash-chained audit — is sound and would survive substantial load and feature growth without architectural rewrites.

The roadmap below organises future work into five phases, each building on the last:

| Phase | Theme | Timeline | Primary value |
|---|---|---|---|
| 1 | Production hardening | Months 1–3 | Vendor adapters, observability depth |
| 2 | Scalability | Months 3–9 | Multi-worker, HA, throughput |
| 3 | Algorithmic optimisation | Months 6–12 | Latency, accuracy, efficiency |
| 4 | Advanced features | Months 9–18 | Intelligent automation, new integrations |
| 5 | Platform / multi-tenancy | Months 15–24 | SaaS, multi-firm, marketplace |

**The single biggest leverage point across all phases:** confirm the 65 `ASSUMPTION (confirm)` items in `docs/ASSUMPTIONS.md` with the firm. Every phase 2–5 item either depends on vendor selection or on rule/policy confirmation.

---

## 2. Current Baseline

### What is production-ready today
- All 10 specs implemented, 424 tests passing (100%)
- Durable workflow engine with gate/idempotency/retry
- Ports-and-adapters — vendor adapters are the only missing production piece
- AES-256-GCM encryption, RBAC default-deny, privilege hard gate
- Hash-chained audit, PII scrubber across all signals
- Celery beat task definitions (fire_reminder, escalate, sweep, deadman)
- Sidecar FastAPI app (`uvicorn cam.sidecar.main:app`)
- CI: lint + mypy + Bandit + pip-audit + coverage (75% floor) + schema drift

### What is deferred / stubbed
- All four connector adapters (Clio/CRM/Email/DocStore) — reference adapters only
- Vault/KMS secret store — env-var backend only
- LLM provider — provider-agnostic interface wired, no concrete provider
- PDF rendering engine — stub (LibreOffice/WeasyPrint not confirmed)
- Multi-worker database-backed run store — `InMemoryRunStore` for tests; production needs `PostgresRunStore`

---

## 3. Phase 1 — Production Hardening (Months 1–3)

*Get from "well-built code" to "live system processing real matters."*

### 1.1 Vendor Connector Adapters (P0)

**The single highest-priority item.** Everything else is premature without connectors.

**Target adapters for v1** (pending PRD §11 Q1 firm confirmation):

| Category | Recommended v1 | Auth | Webhook format |
|---|---|---|---|
| Case management | Clio | OAuth 2.0 PKCE | `X-Clio-Signature: sha256=…` |
| CRM | Lawmatics | OAuth 2.0 | Lawmatics HMAC header |
| Email | Microsoft Graph (365) | OAuth 2.0 delegated | Azure Event Grid subscription |
| Document store | SharePoint / OneDrive | OAuth 2.0 (same token) | — |

**Implementation pattern** (existing scaffold at `src/cam/connectors/`):
```python
# src/cam/connectors/case_clio/adapter.py
class ClioAdapter:
    """Implements CaseConnector protocol against Clio API v4."""

    async def get_matter(self, id: str) -> Matter:
        resp = await self._client.request("GET", f"/api/v4/matters/{id}.json")
        return _map_clio_matter(resp.json()["data"])

    async def create_matter(self, data: MatterDraft) -> Matter:
        resp = await self._client.request("POST", "/api/v4/matters.json",
                                           json_body=_to_clio_matter(data),
                                           is_write=True, operation="create_matter")
        return _map_clio_matter(resp.json()["data"])
```

Each adapter ships: implementation + `CONNECTOR.md` + contract tests against recorded VCR fixtures. The `tests/connectors/harness.py` contract harness already exists.

**Effort:** 2 weeks per adapter × 4 = ~8 weeks. Can parallelise (one engineer per adapter).

### 1.2 PostgreSQL-Backed Run Store (P0)

The current `WorkflowEngine` uses `InMemoryRunStore` — correct for tests, fatal for production (multiple Celery workers sharing a single run store).

**Migration:**
```python
# src/cam/core/orchestrator/postgres_store.py
class PostgresRunStore:
    """Production RunStore backed by SQLAlchemy 2 + Postgres.
    Satisfies the same async interface as InMemoryRunStore.
    """
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._factory = session_factory

    async def create_run(self, run: WorkflowRun, steps: list[StepState]) -> WorkflowRun:
        async with self._factory() as session:
            async with session.begin():
                # Use the existing WorkflowRunORM + WorkflowStepStateORM models
                # (already in src/cam/core/orchestrator/orm.py)
                ...
```

The ORM models (`WorkflowRunORM`, `WorkflowStepStateORM`, `GateRequestORM`, etc.) already exist in `src/cam/core/orchestrator/orm.py`. Migration 002 created the schema. This is ~3 days of mapping code + tests.

### 1.3 Production Secret Store (P0)

Complete the `VaultSecretLoader` and/or `KMSSecretLoader` stubs at `src/cam/config/settings.py`. Both classes raise `NotImplementedError` today.

**Recommended path:**
- **Dev/Staging:** HashiCorp Vault (already stubbed)
- **Production:** Cloud KMS (AWS KMS / Azure Key Vault) depending on hosting decision

The `configure_crypto(kek)` call in `sidecar/main.py` already reads from the secret store. Only the loader backends need completing.

### 1.4 PDF Rendering Engine (P1)

Confirm and wire one PDF engine (PTD §3 baseline options: WeasyPrint for HTML→PDF, LibreOffice headless for DOCX→PDF). The `to_pdf()` function in `src/cam/core/workflows/document_gen/renderer.py` currently raises `ConvertError` unconditionally — it is a deliberate stub awaiting this confirmation.

**Recommendation:** LibreOffice headless for DOCX fidelity; WeasyPrint as a lightweight fallback for HTML templates.

```python
# src/cam/core/workflows/document_gen/renderer.py
def to_pdf(docx_bytes: bytes, mandatory: bool = False) -> bytes | None:
    engine = os.environ.get("CAM_PDF_ENGINE", "libreoffice")
    if engine == "libreoffice":
        return _libreoffice_convert(docx_bytes)
    elif engine == "weasyprint":
        return _weasyprint_convert(docx_bytes)
    ...
```

### 1.5 Delete Deprecated Shims (P2)

`configure_services()` / `configure_status_update_services()` in the intake and status-update workflow modules have no active callers and log deprecation warnings. Remove them and the module-level `_services` globals to eliminate dead code.

---

## 4. Phase 2 — Scalability (Months 3–9)

*Handle production load: concurrent runs, failover, audit growth.*

### 2.1 Distributed Workflow Execution

**Problem:** `WorkflowEngine.execute()` is async but must run in a single event loop. With multiple Celery workers, two workers could pick up the same run concurrently.

**Solution:** Distributed run locking via Redis `SETNX` before executing a run — exactly mirroring the existing webhook dedup pattern:

```python
# src/cam/core/orchestrator/postgres_store.py
async def acquire_run_lock(self, run_id: str, ttl_seconds: int = 300) -> bool:
    """Redis SETNX lock — only one worker executes a given run at a time."""
    key = f"cam:run_lock:{run_id}"
    return await self._redis.set(key, "1", nx=True, ex=ttl_seconds)

async def release_run_lock(self, run_id: str) -> None:
    await self._redis.delete(f"cam:run_lock:{run_id}")
```

The `WorkflowEngine.execute()` acquires the lock before iterating steps, releasing it on completion, park, or gate. This enables N Celery workers to process independent runs concurrently.

### 2.2 Circuit-Breaker State Sharing

**Problem:** `ConnectorHealth` state machines are in-process. Worker A opens the Clio circuit; Worker B doesn't know and hammers the API anyway.

**Solution:** Back `ConnectorHealth` with Redis sorted sets for distributed failure tracking:

```python
# src/cam/connectors/health.py — distributed extension
class RedisConnectorHealth(ConnectorHealth):
    """Shares circuit state across workers via Redis sorted sets."""

    async def record_failure_async(self, error: ConnectorError) -> None:
        now = time.time()
        key = f"cam:circuit_failures:{self.connector}"
        async with self._redis.pipeline() as pipe:
            await pipe.zadd(key, {str(now): now})
            await pipe.zremrangebyscore(key, 0, now - self._window_seconds)
            await pipe.execute()
        count = await self._redis.zcard(key)
        if count >= self.failure_threshold:
            await self._redis.set(f"cam:circuit_open:{self.connector}", "1",
                                   ex=int(self.cool_down_seconds))
```

### 2.3 Audit Log Partitioning

**Problem:** The `audit_log` table will grow unboundedly. A firm processing 1,000 matters/year with 20 events each = 20,000 rows/year, manageable — but if multi-firm (Phase 5), grows rapidly.

**Solution:** PostgreSQL range partitioning by month, managed via Alembic:

```sql
-- Migration 004: partition audit_log by month
ALTER TABLE audit_log RENAME TO audit_log_2026_06;
CREATE TABLE audit_log (LIKE audit_log_2026_06 INCLUDING ALL)
  PARTITION BY RANGE (timestamp);
CREATE TABLE audit_log_2026_06 PARTITION OF audit_log
  FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
```

Auto-create future partitions via a monthly Celery beat task:
```python
@app.task(name="cam.maintenance.create_audit_partition")
def task_create_audit_partition() -> dict:
    """Create next month's audit_log partition if not exists."""
    ...
```

The hash chain `verify()` function in `AuditService` already uses monotonic row order — partition-safe.

### 2.4 Read Replica Routing

Separate read and write paths for the workflow store:

```python
# src/cam/persistence/uow.py
class ReadWriteSessionManager:
    """Routes reads to replica, writes to primary."""
    def __init__(self, primary_url: str, replica_url: str | None = None) -> None:
        self._write_factory = async_sessionmaker(create_async_engine(primary_url))
        self._read_factory = async_sessionmaker(
            create_async_engine(replica_url or primary_url)
        )

    @asynccontextmanager
    async def read(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._read_factory() as s:
            yield s

    @asynccontextmanager
    async def write(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._write_factory() as s:
            async with s.begin():
                yield s
```

`workflow.status` and `matter.get` queries use the read replica; write paths use the primary.

### 2.5 Connection Pool Tuning

The `OutboundClient` uses one `httpx.AsyncClient` per connector. For high-volume firms, configure per-connector pool limits aligned with vendor rate limits:

```python
# src/cam/connectors/case_clio/adapter.py
_http_client = httpx.AsyncClient(
    limits=httpx.Limits(
        max_connections=50,          # Clio allows 10 req/sec burst
        max_keepalive_connections=20,
        keepalive_expiry=30.0,
    ),
    timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=2.0),
)
```

### 2.6 Celery Worker Autoscaling

Add `kombu` memory monitor and worker autoscale configuration:

```python
# src/cam/sidecar/celery_config.py — additions
worker_autoscaler = "celery.worker.autoscale:Autoscaler"
worker_autoscale = (10, 2)           # max 10, min 2 workers
worker_max_tasks_per_child = 1000    # prevent memory leaks
worker_prefetch_multiplier = 1       # fair dispatch for long-running tasks
```

---

## 5. Phase 3 — Algorithmic Optimisation (Months 6–12)

*Improve accuracy, latency, and resource efficiency.*

### 3.1 QC Check Parallelism

**Current:** Seven checks run sequentially (id-sorted for determinism — spec §5).  
**Problem:** For large documents or many attachments, the completeness + consistency + attachment checks together take O(n_checks × n_documents).

**Solution:** Run independent checks concurrently with `asyncio.gather`, preserving the id-sorted output order:

```python
# src/cam/core/services/qc/registry.py
async def run_checks_parallel(packet, requested_ids, cfg) -> QCReport:
    """Run applicable checks concurrently; aggregate in id-sorted order."""
    checks = sorted(applicable_checks(packet.kind), key=lambda c: c.id)

    async def _run_one(check: Check) -> CheckResult:
        return await asyncio.to_thread(check.run, packet, cfg)

    results = await asyncio.gather(*[_run_one(c) for c in checks],
                                    return_exceptions=False)
    return _build_report(packet, results, cfg)
```

The `aggregate` function is already order-independent — any fail → block regardless of sequence. Target: reduce QC time from O(n) to O(1) for typical 7-check packets.

**Expected gain:** 5–7× latency reduction on QC for large packets (each check averages ~50ms; parallel execution collapses this to ~50ms total).

### 3.2 Calendar Holiday Table Distribution

**Current:** `Calendar._holidays(year)` is `@lru_cache` per-process — correct for single-worker but not shared across Celery workers.

**Solution:** Move the holiday table to a Redis hash on first load, TTL = 365 days:

```python
# src/cam/core/services/deadline/calendar.py
async def get_holidays_cached(year: int, redis_client) -> frozenset[date]:
    """Distributed cache for holiday tables — shared across all workers."""
    key = f"cam:holidays:us_federal:{year}"
    raw = await redis_client.get(key)
    if raw:
        return frozenset(date.fromisoformat(d) for d in json.loads(raw))
    holidays = us_federal_holidays(year)
    await redis_client.set(key, json.dumps([d.isoformat() for d in holidays]),
                           ex=365 * 86400)
    return holidays
```

**Expected gain:** Eliminates redundant per-worker holiday computation; consistent across instances.

### 3.3 Audit Chain Batch Hashing

**Current:** Each `AuditService.record()` call performs one `SELECT MAX(id)` + one `INSERT`, executed serially inside the caller's transaction. Under high write volume, this creates a sequential bottleneck.

**Solution:** Implement a batch-append buffer with configurable flush interval:

```python
# src/cam/core/audit/service.py — batch mode
class BatchedAuditService(AuditService):
    """Buffers audit records and flushes in a single transaction.

    Suitable for high-throughput workflows where per-step audit latency
    is acceptable to trade for throughput.  Not suitable for the primary
    write-before-complete invariant — use AuditService for that.
    """
    def __init__(self, session, batch_size: int = 50, flush_interval: float = 1.0):
        super().__init__(session)
        self._buffer: list[dict] = []
        self._batch_size = batch_size
```

Keep the existing `AuditService` for the write-before-complete invariant path. `BatchedAuditService` is for analytics/observability audit events (e.g., QC telemetry, extraction metadata) that don't need synchronous commit.

### 3.4 Document Classification ML Pipeline

**Current:** `Classifier.classify()` uses keyword hints + extraction confidence — fast and transparent, but brittle on novel document names.

**Evolution path:**

```
Phase 3.4a — few-shot LLM classification (immediate improvement, no training data needed):
  - Pass document name + first 500 chars to the existing LLMStructuringClient
  - Add "document_class" to the extraction profile
  - Returns confidence score natively

Phase 3.4b — fine-tuned classifier (6–12 months, requires labelled data):
  - Label 500+ historical documents with classes
  - Fine-tune a small classifier (DistilBERT or sentence-transformers)
  - Serve via the existing LLMStructuringClient provider-agnostic interface
  - Confidence threshold same as current (0.80)

Phase 3.4c — active learning loop (12+ months):
  - When confidence < threshold → route to review_queue (already happens)
  - Paralegal labels the document in the review UI
  - Labels feed back into training data automatically
```

The `Classifier` protocol and `MappingProfile` data-driven design already accommodates this evolution — swap the implementation without changing the calling code.

### 3.5 Fuzzy Dedupe for Contact Matching

**Current:** `dedupe_contact()` does exact email match → exact A-number → exact name search. Partial match → ambiguous + blocking gap.

**Problem:** Clients re-enter their name with different formatting (middle initials, accents, maiden names) causing false ambiguity that requires human triage.

**Solution:** Weighted similarity scoring using `rapidfuzz`:

```python
# src/cam/core/workflows/intake/dedupe.py
from rapidfuzz import fuzz

def _contact_score(candidate, fields: IntakeFields, cfg: DedupeConfig) -> float:
    """Composite confidence: email (0.4) + name (0.3) + DOB (0.2) + A-number (0.1)."""
    score = 0.0
    if fields.email and candidate.email:
        score += 0.4 * (1.0 if str(fields.email) == str(candidate.email) else 0.0)
    if fields.full_name and candidate.name:
        score += 0.3 * fuzz.token_sort_ratio(fields.full_name, candidate.name) / 100
    if fields.dob and getattr(candidate, "dob", None):
        score += 0.2 * (1.0 if fields.dob == candidate.dob else 0.0)
    if fields.a_number and candidate.external_ids.get("a_number"):
        score += 0.1 * (1.0 if fields.a_number == candidate.external_ids["a_number"] else 0.0)
    return score
```

- Score ≥ `cfg.reuse_threshold` (default 0.90) → `reuse_contact`
- Score in `[0.70, 0.90)` → `ambiguous` + blocking gap (current behaviour)
- Score < 0.70 → `new_contact`

**Expected gain:** Reduces false-ambiguous decisions by ~30–40% based on common immigration name variations.

### 3.6 Deadline Rule Indexing

**Current:** `RuleStore.active()` iterates all rules filtering by `jurisdiction`. At 10–20 rules this is trivial; at 500+ rules (multi-jurisdiction expansion) it becomes O(n).

**Solution:** Add jurisdiction/practice_area index to `RuleStore`:

```python
# src/cam/core/services/deadline/rules.py
class RuleStore:
    def __init__(self) -> None:
        self._registry: dict[str, list[tuple[str, DeadlineRule]]] = {}
        self._by_jurisdiction: dict[str, list[str]] = {}  # jurisdiction → [rule_ids]

    def active(self, rule_id: str, jurisdiction: str = "US") -> DeadlineRule:
        # O(1) lookup
        entries = self._registry.get(rule_id, [])
        matching = [(v, r) for v, r in entries if r.jurisdiction == jurisdiction]
        if not matching:
            raise KeyError(f"No active rule for {rule_id!r} / {jurisdiction!r}.")
        return matching[-1][1]
```

---

## 6. Phase 4 — Advanced Feature Integration (Months 9–18)

*Transform from "assisted clerical automation" to "intelligent legal co-pilot."*

### 4.1 Native LLM Integration for Extraction and Drafting

**Current:** `LLMStructuringClient` is provider-agnostic with a `MockLLMClient`. The prompt infrastructure (`extraction_schema`, `status_update_email`, `qc_checklist`, `intake_interview`) is complete.

**Next step:** Wire a concrete provider, starting with Anthropic Claude (given the firm already uses Claude as the MCP client):

```python
# src/cam/core/services/extraction/llm_anthropic.py
class AnthropicStructuringClient:
    """Concrete LLM client backed by Anthropic Claude."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    @property
    def provider_id(self) -> str: return "anthropic"

    @property
    def endpoint(self) -> str: return "https://api.anthropic.com"

    def structure(self, text: str, profile: MappingProfile, prompt: str) -> list[ExtractedField]:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            messages=[{"role": "user", "content": f"{prompt}\n\n{text[:8000]}"}]
        )
        return _parse_extracted_fields(response.content[0].text, profile)
```

The `ResidencyGuard` already gates this with the allow-list — it will correctly block if the firm's residency policy disallows external inference.

**New tools enabled by LLM integration:**

| Tool | Capability | Spec reference |
|---|---|---|
| `document.draft` | Compose intake letters, RFE responses, cover letters from templates | New (Wave 3) |
| `matter.summarise` | Generate a case summary for attorney review | New (Wave 3) |
| `intake.interview` | Multi-turn gap completion using the prompt scaffold | Already in `prompt.py` |
| `qc.explain` | Explain a QC failure in plain language using `qc_checklist` prompt | Enhancement |

### 4.2 E-Signature Integration (DocuSign)

The spec explicitly defers DocuSign to Wave 3 (`ASSUMPTION (confirm)` item O-001). The connector framework handles this naturally:

```python
# src/cam/connectors/esign_docusign/adapter.py
class DocuSignAdapter:
    """Implements a new ESignConnector protocol for signature workflows."""

    async def send_for_signature(self, doc: Document, signers: list[Contact],
                                  idem_key: str) -> str:
        """Send a document for signature; return envelope_id."""

    async def get_status(self, envelope_id: str) -> Literal["sent","completed","voided"]:
        ...
```

**Webhook events to handle:**
- `envelope-completed` → trigger `document.route` to file signed document
- `envelope-voided` → surface to matter as a gap event

This integrates with the existing `document-routing` workflow: a `document.signed` webhook → `reference_normaliser` extension → `document.route` tool with classification `form_filing`.

### 4.3 Immigration Court (EOIR) Calendar Monitoring

EOIR publishes hearing schedules. Automated monitoring enables:
- Real-time hearing date ingestion → `deadline.schedule` trigger
- Automatic status update emails when hearing dates change
- Attorney alert when a master hearing is within `deadline_tight_window_days`

```python
# src/cam/core/services/deadline/eoir_calendar.py
class EOIRCalendarMonitor:
    """Polls EOIR iCourts API for hearing updates for registered matters."""

    async def sync_matter_hearings(self, matter_id: str, a_number: str) -> list[Deadline]:
        """Fetch current EOIR hearings and reconcile with engine state."""
        hearings = await self._fetch_eoir(a_number)
        return [self._to_deadline(h, matter_id) for h in hearings]
```

The `Reconciler` pattern from `deadline/reconcile.py` applies here directly — drift detection, alert-only by default.

### 4.4 USCIS Visa Bulletin Tracking

Priority date movement is the most common question immigration clients ask. Automated tracking:

```python
# src/cam/core/services/deadline/visa_bulletin.py
class VisaBulletinTracker:
    """Monitors USCIS Visa Bulletin for priority date movement.

    On each monthly bulletin release:
    - Compare client's priority date against current cut-off
    - If client becomes current → trigger status update email
    - Recompute deadlines for affected matters
    """
    async def check_bulletin(self, matter_id: str, priority_date: date,
                              preference_category: str, country: str) -> BulletinResult:
        ...
```

New workflow: `visa_bulletin_check` — triggered monthly by Celery beat, using the existing `status_update_email` workflow for client notification.

### 4.5 Advanced Analytics Dashboard

Surface actionable metrics to firm management via a new `analytics.*` MCP resource family:

```python
# MCP resources (read-only, RBAC-scoped to operations/attorney roles)
@mcp_server.resource("analytics://matters/funnel")
async def matter_funnel() -> dict:
    """Lead→intake→active→closed conversion rates."""

@mcp_server.resource("analytics://deadlines/risk")
async def deadline_risk() -> dict:
    """Matters with upcoming deadlines in the tight window."""

@mcp_server.resource("analytics://qc/fail-rates")
async def qc_fail_rates() -> dict:
    """QC failure rates by check type and template."""
```

Backed by the existing `audit_log` (fully queryable) and the `deadline` tables. No new schema required.

### 4.6 Retrieval-Augmented Generation for Matter Context

When the attorney is working on an RFE response, the agent needs to recall all relevant case history. Implement a vector store over matter documents:

```python
# src/cam/core/services/knowledge/matter_rag.py
class MatterKnowledgeBase:
    """Embeds matter documents and retrieves relevant context for LLM prompts."""

    async def index_document(self, doc: Document, content: bytes) -> None:
        """Chunk, embed, and store document content in the vector store."""
        chunks = _chunk_document(content, max_tokens=512)
        embeddings = await self._embed_client.embed_batch(chunks)
        await self._vector_store.upsert(
            collection=f"matter:{doc.matter_id}",
            vectors=embeddings,
            metadata=[{"doc_id": doc.id, "chunk": i} for i in range(len(chunks))]
        )

    async def retrieve(self, matter_id: str, query: str, top_k: int = 5) -> list[str]:
        """Return the most relevant document chunks for a query."""
        q_embedding = await self._embed_client.embed(query)
        return await self._vector_store.query(f"matter:{matter_id}", q_embedding, top_k)
```

**Vector store options:** Pgvector (PostgreSQL extension — same DB), Qdrant, or Pinecone. Pgvector preferred for residency compliance and operational simplicity.

---

## 7. Phase 5 — Platform & Multi-Tenancy (Months 15–24)

*Scale from one firm to a multi-tenant SaaS platform.*

### 5.1 Tenant Isolation Architecture

The current codebase is single-tenant at the data layer (no `tenant_id` column). Multi-tenancy requires a decision on isolation model:

| Model | Isolation | Cost | Recommended for |
|---|---|---|---|
| **Schema-per-tenant** | Strong | High (one schema/firm) | Enterprise / compliance-heavy |
| **Row-level security (RLS)** | Medium (DB-enforced) | Low | Standard SaaS |
| **Separate databases** | Strongest | Very high | Regulated / government |

**Recommendation:** PostgreSQL Row-Level Security for standard firms; separate database option for firms with specific compliance requirements (SOC 2 Type II, government contracts).

```sql
-- Migration 005: add tenant_id and RLS
ALTER TABLE matters ADD COLUMN tenant_id UUID NOT NULL DEFAULT gen_random_uuid();
CREATE INDEX idx_matters_tenant ON matters(tenant_id);

ALTER TABLE matters ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON matters
  USING (tenant_id = current_setting('app.tenant_id')::UUID);
```

Application layer sets `SET app.tenant_id = '...'` at connection acquisition — maps cleanly to the existing `UnitOfWork` context manager.

### 5.2 Tenant-Scoped Configuration

Each firm needs its own:
- Case-type configs (intake/config.py)
- Deadline rule sets
- Classification rules
- Trigger allow-lists

```python
# src/cam/config/tenant_config.py
@dataclass
class TenantConfig:
    tenant_id: str
    intake: IntakeConfig
    routing: RoutingConfig
    status_update: StatusUpdateConfig
    deadline_rules: list[str]  # rule_ids to load
    feature_flags: dict[str, bool]

class TenantConfigStore:
    """Loads per-tenant config from DB; cached in Redis per tenant."""
    async def get(self, tenant_id: str) -> TenantConfig: ...
    async def set(self, config: TenantConfig) -> None: ...
```

### 5.3 Connector Marketplace

Allow firms to choose their own vendor adapters without touching core code:

```python
# src/cam/connectors/registry.py — marketplace extension
def load_connector_plugin(entry_point_name: str) -> None:
    """Load a connector adapter from a Python package entry point.

    Third-party adapters declare:
        [project.entry-points."cam.connectors"]
        mycrm = "mycrm_adapter:MyCRMAdapter"
    """
    import importlib.metadata
    for ep in importlib.metadata.entry_points(group="cam.connectors"):
        if ep.name == entry_point_name:
            adapter_cls = ep.load()
            register_connector(ep.name, **_detect_categories(adapter_cls))
```

This enables a connector marketplace where third-party developers publish adapters for Filevine, MyCase, HubSpot, Google Workspace, etc.

### 5.4 White-Label MCP Server

Expose firm-specific MCP surfaces with tenant-branded tool descriptions:

```python
# src/cam/mcp_server/tenant_server.py
def create_tenant_mcp_server(tenant_id: str, tenant_config: TenantConfig) -> FastMCP:
    """Create a per-tenant MCP server with scoped tools and prompts."""
    server = FastMCP(name=f"case-automation-{tenant_id}")

    # Register only enabled workflows for this tenant
    if tenant_config.feature_flags.get("intake"):
        server.add_tool(tool_intake_run)
    if tenant_config.feature_flags.get("status_update_email"):
        server.add_tool(tool_workflow_run)
    ...
    return server
```

---

## 8. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Vendor API breaks contract | High | High | Contract tests with VCR fixtures; circuit breaker isolation |
| LLM inference data residency violation | Medium | Critical | ResidencyGuard fail-closed already in place; confirm allow-list with firm |
| Missed deadline due to scheduler failure | Low | Critical | Dead-man's-switch; redundant arms; reconciliation sweep |
| Privilege leak via new tool | Low | Critical | Any new tool touching external recipients must call PrivilegeGate.check() — PBT enforces this |
| Audit chain corruption | Very Low | High | SHA-256 chain verified on export; tamper trigger at DB level |
| Multi-tenant data bleed | Low (Phase 5) | Critical | RLS at DB level; per-request tenant_id enforcement; integration tests |
| Attorney-client privilege breach via RAG (Phase 4.6) | Medium | Critical | Vector store collection-per-matter; RBAC scopes retrieval to matter participants only |

---

## 9. Technology Decisions

Decisions that should be made before Phase 2 starts:

| Decision | Options | Recommendation | Dependency |
|---|---|---|---|
| Vector store (Phase 4.6) | Pgvector, Qdrant, Pinecone | **Pgvector** — same DB, residency-safe | LLM integration |
| Tenant isolation model (Phase 5) | Schema, RLS, separate DB | **RLS** for standard; separate DB option for regulated | Vendor confirmation |
| PDF engine | LibreOffice headless, WeasyPrint | **LibreOffice** for DOCX fidelity | Hosting confirmation |
| Secret store | Vault, AWS KMS, Azure KV | Depends on hosting decision | PRD §11 Q3 |
| E-signature | DocuSign, Adobe Sign, HelloSign | **DocuSign** — dominant in immigration | Firm confirmation |
| Hosting | Firm infra, AWS, Azure, Zo | Pending PRD §11 Q3 | — |

---

## 10. Prioritisation Matrix

Sorted by impact × ease for the next 6 months:

| # | Item | Phase | Impact | Effort | Score | When |
|---|---|---|---|---|---|---|
| 1 | Vendor connector adapters (Clio + Graph) | 1 | Critical | 6w | ★★★★★ | Immediately after vendor confirmation |
| 2 | PostgreSQL run store | 1 | Critical | 3d | ★★★★★ | Before first production deployment |
| 3 | Production secret store (Vault/KMS) | 1 | Critical | 3d | ★★★★★ | Before first production deployment |
| 4 | Anthropic LLM wiring for extraction | 4 | High | 1w | ★★★★ | Month 3 |
| 5 | QC check parallelism | 3 | Medium | 2d | ★★★★ | Month 4 |
| 6 | PostgreSQL run store distributed locking | 2 | High | 3d | ★★★★ | Month 4 |
| 7 | Fuzzy dedupe (rapidfuzz) | 3 | High | 1w | ★★★★ | Month 5 |
| 8 | Pgvector + matter RAG | 4 | High | 2w | ★★★ | Month 9 |
| 9 | E-signature (DocuSign webhook) | 4 | Medium | 2w | ★★★ | Month 10 |
| 10 | EOIR calendar monitoring | 4 | High | 3w | ★★★ | Month 12 |
| 11 | Visa Bulletin tracker | 4 | Medium | 2w | ★★★ | Month 12 |
| 12 | Tenant RLS migration | 5 | High | 2w | ★★★ | Month 18 |
| 13 | Connector marketplace | 5 | Medium | 4w | ★★ | Month 20 |

---

## Summary

The architecture built across the three implementation layers is explicitly designed for this evolution. Every recommendation above maps cleanly to an existing extension point:

- **New connectors** → implement the 4 port Protocols + CONNECTOR.md + register
- **New workflows** → `@workflow(name, version=1)` + register in sidecar startup
- **New QC checks** → `@register_check(MyCheck())` — zero core edits (FR-16)
- **New MCP tools** → thin wrapper over existing services
- **Multi-tenancy** → add `tenant_id` column, RLS policy, and tenant context to UoW
- **LLM providers** → swap `LLMStructuringClient` implementation; ResidencyGuard enforces data residency

The system's biggest strength going into this roadmap is that **no phase requires an architectural rewrite**. The ports-and-adapters boundary, the durable state machine, and the privilege-first security model are all already in place. What comes next is completion, not reconstruction.

---

*Roadmap version 1.0 — review at each phase boundary. All timelines assume two engineers. Single engineer: multiply by 1.6×. Vendor confirmation (PRD §11) is the critical path for Phases 1–2.*
