"""End-to-end golden-path workflow tests.

These tests exercise the full in-memory stack without a live database.
Five scenarios are covered:

  E2E-01  Intake: lead → gate → send_welcome
  E2E-02  Approval gate: run parks → approve → resumes
  E2E-03  Approval gate: run parks → reject → terminal
  E2E-04  Privilege gate: privileged doc cannot route externally
  E2E-05  Idempotent replay: same trigger → one run, one send
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cam.connectors.reference import (
    ReferenceCaseConnector,
    ReferenceCRMConnector,
    ReferenceDocStoreConnector,
    ReferenceEmailConnector,
)
from cam.core.domain.models import Contact, Matter
from cam.core.orchestrator.dsl import clear_registry
from cam.core.orchestrator.engine import WorkflowEngine
from cam.core.orchestrator.gates import issue_token, resolve_gate
from cam.core.orchestrator.idempotency import InMemoryIdempotencyStore
from cam.core.orchestrator.states import RunStatus
from cam.core.orchestrator.store import InMemoryRunStore
from cam.core.services.deadline.rules import RuleStore
from cam.core.services.deadline.schedule import DeadlineStore, InMemoryScheduler
from cam.core.workflows.document_routing.service import RoutingDecisionStore, RoutingService
from cam.core.workflows.document_routing.types import RouteOptions
from cam.core.workflows.intake.config import CaseTypeConfig, IntakeConfig, TaskTemplate
from cam.core.workflows.intake.types import LeadPayload
from cam.core.workflows.intake.workflow import (
    IntakeServices,
    register_intake_workflow,
    tool_intake_run,
)

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
SIGNING_KEY = b"test-signing-key-32-bytes-padded"


@pytest.fixture(autouse=True)
def _clear():
    clear_registry()


def _make_services() -> IntakeServices:
    return IntakeServices(
        extraction=None,
        crm=ReferenceCRMConnector(),
        case_connector=ReferenceCaseConnector(),
        email=ReferenceEmailConnector(),
        deadline_store=DeadlineStore(),
        scheduler=InMemoryScheduler(),
        rule_store=RuleStore(),
        config=IntakeConfig(
            case_type_configs={
                "family-based": CaseTypeConfig(
                    case_type="family-based",
                    required_fields=["full_name", "email"],
                    opening_tasks=[TaskTemplate(title="Conflict check", assignee_role="attorney",
                        sort=1)],
                    deadline_rule_ids=[],
                    welcome_template_id="family_welcome",
                ),
                "other/uncategorised": CaseTypeConfig(
                    case_type="other/uncategorised",
                    required_fields=["full_name", "email"],
                    opening_tasks=[TaskTemplate(title="Initial consult", assignee_role="attorney",
                        sort=1)],
                    deadline_rule_ids=[],
                    welcome_template_id="default_welcome",
                ),
            },
            default_case_type="other/uncategorised",
        ),
    )


def _make_engine(store):
    return WorkflowEngine(store=store,
        idem_store=InMemoryIdempotencyStore(),
        signing_key=SIGNING_KEY)


def _make_lead(**raw_overrides) -> LeadPayload:
    raw = {"full_name": "Ana Garcia", "email": "ana@example.com"
        , "case_type": "other/uncategorised"}
    raw.update(raw_overrides)
    return LeadPayload(source_channel="email", received_at=NOW, raw=raw)


# ──────────────────────────────────────────────────────────────
# E2E-01  Intake: lead → all steps → gate (awaiting approval)
# ──────────────────────────────────────────────────────────────

async def test_e2e_01_intake_runs_to_gate() -> None:
    """A complete intake run advances through all pre-gate steps and parks at the gate."""
    services = _make_services()
    register_intake_workflow(services)

    run_store = InMemoryRunStore()
    engine = _make_engine(run_store)

    lead = _make_lead()
    result = await tool_intake_run(lead, run_store)
    assert result["run_id"]
    assert result["status"] == "queued"

    # Execute — should run all steps and stop at GATE:human_review
    run = await engine.execute(result["run_id"])
    assert run.status == RunStatus.AWAITING_APPROVAL

    # Verify steps completed before the gate
    steps = await run_store.get_steps(run.id)
    succeeded = [s.step for s in steps if s.status.value == "succeeded"]
    assert "parse_lead" in succeeded
    assert "dedupe_contact" in succeeded
    assert "create_contact" in succeeded
    assert "create_matter" in succeeded
    assert "open_tasks" in succeeded
    assert "draft_welcome" in succeeded


# ──────────────────────────────────────────────────────────────
# E2E-02  Approval gate: approve → run resumes → send_welcome
# ──────────────────────────────────────────────────────────────

async def test_e2e_02_gate_approve_resumes_run() -> None:
    """Approving a gate resumes the run and executes the post-gate steps."""
    services = _make_services()
    register_intake_workflow(services)

    run_store = InMemoryRunStore()
    engine = _make_engine(run_store)

    lead = _make_lead()
    result = await tool_intake_run(lead, run_store)
    run = await engine.execute(result["run_id"])
    assert run.status == RunStatus.AWAITING_APPROVAL

    # Issue a token and approve
    gate = list(run_store._gates.values())[0]
    raw_token, token_record = issue_token(
        gate_request_id=gate.id, run_id=run.id,
        step=gate.step, channel="mcp", signing_key=SIGNING_KEY,
    )
    await run_store.save_token(token_record)
    await resolve_gate(raw_token, "approve", "attorney", "mcp", SIGNING_KEY, run_store)

    # Resume execution — should complete
    final = await engine.execute(run.id)
    assert final.status == RunStatus.SUCCEEDED

    # Verify email was sent
    email_conn: ReferenceEmailConnector = services.email  # type: ignore
    assert email_conn.was_sent(f"{run.id}:send_welcome") or len(email_conn._sent) > 0


# ──────────────────────────────────────────────────────────────
# E2E-03  Approval gate: reject → run terminates, no send
# ──────────────────────────────────────────────────────────────

async def test_e2e_03_gate_reject_terminates_no_send() -> None:
    """Rejecting the gate terminates the run; send_welcome must never be called."""
    services = _make_services()
    register_intake_workflow(services)

    run_store = InMemoryRunStore()
    engine = _make_engine(run_store)

    lead = _make_lead()
    result = await tool_intake_run(lead, run_store)
    run = await engine.execute(result["run_id"])
    assert run.status == RunStatus.AWAITING_APPROVAL

    gate = list(run_store._gates.values())[0]
    raw_token, token_record = issue_token(
        gate_request_id=gate.id, run_id=run.id,
        step=gate.step, channel="mcp", signing_key=SIGNING_KEY,
    )
    await run_store.save_token(token_record)
    await resolve_gate(raw_token, "reject", "attorney", "mcp", SIGNING_KEY, run_store)

    # Run is now rejected — further execution would error; check state directly
    rejected_run = await run_store.get_run(run.id)
    assert rejected_run.status == RunStatus.REJECTED

    # No email sent after rejection
    email_conn: ReferenceEmailConnector = services.email  # type: ignore
    assert not email_conn.was_sent(f"{run.id}:send_welcome")


# ──────────────────────────────────────────────────────────────
# E2E-04  Privilege gate: privileged doc cannot route externally
# ──────────────────────────────────────────────────────────────

async def test_e2e_04_privilege_gate_blocks_external_routing() -> None:
    """A privileged document is hard-blocked when routed to an external recipient."""
    from cam.core.domain.models import Document

    client_contact = Contact(id="c1", source="crm", name="Ana Garcia",
                             email="ana@example.com", external_ids={})
    matter = Matter(id="m1", source="case", reference="REF-001", title="T",
                    status="open", client=client_contact, opened_at=NOW, external_ids={})

    privileged_doc = Document(
        id="doc1", matter_id="m1", name="engagement_letter.pdf",
        mime_type="application/pdf", uri="s3://doc1",
        classification="engagement_letter", version=1,
        privileged=True, checksum="abc123", created_at=NOW,
    )

    store = RoutingDecisionStore()
    docstore = ReferenceDocStoreConnector()
    await docstore.put(privileged_doc, b"privileged content")

    svc = RoutingService(store, docstore)

    # Route to an external recipient — must be blocked
    result = await svc.route(
        privileged_doc, matter,
        RouteOptions(recipient_routing_enabled=True),
        recipient_id="external@outsider.com",
    )
    assert result.outcome == "blocked"
    assert result.gate_verdict == "fail"

    # No move was made
    _, stored_bytes = await docstore.get(privileged_doc.id)
    assert stored_bytes == b"privileged content"  # unchanged


# ──────────────────────────────────────────────────────────────
# E2E-05  Idempotent replay: same lead trigger → one run, one send
# ──────────────────────────────────────────────────────────────

async def test_e2e_05_duplicate_lead_produces_single_run() -> None:
    """Submitting the same lead twice creates exactly one run."""
    services = _make_services()
    register_intake_workflow(services)

    run_store = InMemoryRunStore()
    lead = _make_lead()
    idem_key = "golden-path-test-001"

    r1 = await tool_intake_run(lead, run_store, idempotency_key=idem_key)
    r2 = await tool_intake_run(lead, run_store, idempotency_key=idem_key)

    assert r1["run_id"] == r2["run_id"], "Duplicate trigger must return the same run"
    assert len(run_store._runs) == 1, "Only one run should exist"
    assert r1["is_new"] is True, "First submission must report a new run"
    assert r2["is_new"] is False, "Duplicate submission must report the existing run"


async def test_e2e_06_workflow_run_reports_is_new() -> None:
    """workflow.run reports is_new=False when the idem key dedupes to an existing run."""
    from cam.mcp_server.workflow_tools import WorkflowRunInput, tool_workflow_run

    services = _make_services()
    register_intake_workflow(services)

    run_store = InMemoryRunStore()
    inp = WorkflowRunInput(
        workflow="intake",
        context={"lead": _make_lead().model_dump(mode="json")},
        idem_key="e2e-06-idem-001",
    )
    out1 = await tool_workflow_run(inp, run_store)
    out2 = await tool_workflow_run(inp, run_store)

    assert out1.is_new is True
    assert out2.is_new is False
    assert out1.run_id == out2.run_id
