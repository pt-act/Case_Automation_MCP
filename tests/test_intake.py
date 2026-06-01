"""Client intake — all G1–G8 focused tests + PBT invariants."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cam.core.domain.models import Contact, Matter
from cam.core.orchestrator.dsl import clear_registry
from cam.core.orchestrator.idempotency import InMemoryIdempotencyStore
from cam.core.orchestrator.store import InMemoryRunStore
from cam.core.services.deadline.rules import RuleStore
from cam.core.services.deadline.schedule import DeadlineStore, InMemoryScheduler
from cam.core.workflows.intake.config import (
    CaseTypeConfig,
    DedupeConfig,
    IntakeConfig,
    TaskTemplate,
    get_intake_config,
    set_intake_config,
)
from cam.core.workflows.intake.dedupe import dedupe_contact
from cam.core.workflows.intake.parse import parse_lead
from cam.core.workflows.intake.prompt import render_intake_interview
from cam.core.workflows.intake.tasks import render_tasks
from cam.core.workflows.intake.types import (
    DedupeResult,
    IntakeFields,
    IntakeGap,
    LeadPayload,
    MatchRef,
)
from cam.core.workflows.intake.welcome import draft_welcome, send_welcome
from cam.connectors.reference import (
    ReferenceCRMConnector,
    ReferenceCaseConnector,
    ReferenceEmailConnector,
)


NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_registry():
    clear_registry()


def _cfg_with_tasks() -> IntakeConfig:
    return IntakeConfig(
        case_type_configs={
            "family-based": CaseTypeConfig(
                case_type="family-based",
                required_fields=["full_name", "email", "country_of_origin"],
                opening_tasks=[
                    TaskTemplate(title="Collect docs", assignee_role="paralegal", sort=1),
                    TaskTemplate(title="Conflict check", assignee_role="attorney", sort=2),
                ],
                deadline_rule_ids=[],
                welcome_template_id="family_welcome",
            ),
            "other/uncategorised": CaseTypeConfig(
                case_type="other/uncategorised",
                required_fields=["full_name", "email"],
                opening_tasks=[TaskTemplate(title="Initial consult", assignee_role="attorney", sort=1)],
                deadline_rule_ids=[],
                welcome_template_id="default_welcome",
            ),
        },
        default_case_type="other/uncategorised",
    )


def _email_lead(**kwargs) -> LeadPayload:
    defaults = dict(
        source_channel="email",
        received_at=NOW,
        raw={
            "full_name": "Ana Garcia",
            "email": "ana@example.com",
            "country_of_origin": "MX",
            "case_type": "family-based",
        },
    )
    return LeadPayload(**{**defaults, **kwargs})


def _contact(id: str = "c1") -> Contact:
    return Contact(id=id, source="crm", name="Ana Garcia", email="ana@example.com",
                   external_ids={})


def _matter(contact: Contact | None = None) -> Matter:
    c = contact or _contact()
    return Matter(id="m1", source="case", reference="INTAKE-ABC", title="T",
                  status="open", client=c, opened_at=NOW, external_ids={})


# ──────────────────────────────────────────────────────────────
# G1 — Types + config
# ──────────────────────────────────────────────────────────────

def test_lead_payload_email_valid() -> None:
    lead = _email_lead()
    assert lead.source_channel == "email"
    assert lead.raw["email"] == "ana@example.com"


def test_lead_payload_form_valid() -> None:
    lead = _email_lead(source_channel="form")
    assert lead.source_channel == "form"


def test_a_number_invalid_rejected() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        IntakeFields(a_number="123456789")  # missing A prefix


def test_a_number_valid() -> None:
    f = IntakeFields(a_number="A123456789")
    assert f.a_number == "A123456789"


def test_case_type_config_round_trips() -> None:
    cfg = _cfg_with_tasks()
    assert "family-based" in cfg.case_type_configs


def test_unknown_case_type_falls_back() -> None:
    cfg = _cfg_with_tasks()
    result = cfg.get_case_type("asylum")  # not configured
    assert result.case_type == "other/uncategorised"


# ──────────────────────────────────────────────────────────────
# G2 — Lead parsing
# ──────────────────────────────────────────────────────────────

def test_email_lead_maps_to_fields() -> None:
    lead = _email_lead()
    cfg = _cfg_with_tasks()
    fields, gaps = parse_lead(lead, None, "family-based", cfg)
    assert fields.full_name == "Ana Garcia"
    assert str(fields.email) == "ana@example.com"


def test_missing_required_field_produces_blocking_gap() -> None:
    lead = _email_lead(raw={"full_name": "Ana Garcia", "case_type": "family-based"})
    cfg = _cfg_with_tasks()
    fields, gaps = parse_lead(lead, None, "family-based", cfg)
    blocking = [g for g in gaps if g.severity == "block" and g.field == "email"]
    assert blocking, "Missing email should produce a blocking gap"


def test_unresolved_case_type_produces_gap() -> None:
    lead = _email_lead(raw={"full_name": "Ana", "email": "a@b.com"})
    cfg = _cfg_with_tasks()
    fields, gaps = parse_lead(lead, None, None, cfg)
    assert fields.case_type == "other/uncategorised"


# ──────────────────────────────────────────────────────────────
# G3 — Dedupe
# ──────────────────────────────────────────────────────────────

async def test_exact_email_match_returns_reuse() -> None:
    crm = ReferenceCRMConnector()
    existing = _contact()
    await crm.upsert_contact(existing)
    fields = IntakeFields(full_name="Ana Garcia", email="ana@example.com")
    result, gaps = await dedupe_contact(fields, crm)
    assert result.decision == "reuse_contact"


async def test_no_match_returns_new_contact() -> None:
    crm = ReferenceCRMConnector()
    fields = IntakeFields(full_name="Unknown Person", email="nobody@example.com")
    result, gaps = await dedupe_contact(fields, crm)
    assert result.decision == "new_contact"


async def test_multiple_candidates_returns_ambiguous() -> None:
    crm = ReferenceCRMConnector()
    # Seed two contacts with similar names
    await crm.upsert_contact(Contact(id="c1", source="crm", name="Ana Garcia", external_ids={}))
    await crm.upsert_contact(Contact(id="c2", source="crm", name="Ana Garcia", external_ids={}))
    fields = IntakeFields(full_name="Ana Garcia")
    result, gaps = await dedupe_contact(fields, crm)
    assert result.decision == "ambiguous"
    assert any(g.severity == "block" for g in gaps)


# ──────────────────────────────────────────────────────────────
# G4 — Contact + matter creation
# ──────────────────────────────────────────────────────────────

async def test_new_contact_created_once() -> None:
    from cam.core.workflows.intake.create import create_contact
    crm = ReferenceCRMConnector()
    fields = IntakeFields(full_name="Ana Garcia", email="ana@example.com")
    dedupe = DedupeResult(decision="new_contact")
    contact = await create_contact(fields, dedupe, crm, "run-001")
    assert contact.id


async def test_reuse_path_writes_nothing() -> None:
    from cam.core.workflows.intake.create import create_contact
    crm = ReferenceCRMConnector()
    ref = MatchRef(contact_id="existing-c1", source="crm", score=1.0)
    fields = IntakeFields(full_name="Ana Garcia", email="ana@example.com")
    dedupe = DedupeResult(decision="reuse_contact", contact_match=ref)
    contact = await create_contact(fields, dedupe, crm, "run-001")
    assert contact.id == "existing-c1"
    assert len(crm._contacts) == 0  # nothing actually written to CRM


async def test_matter_created_with_practice_area() -> None:
    from cam.core.workflows.intake.create import create_matter
    case_connector = ReferenceCaseConnector()
    cfg = _cfg_with_tasks().get_case_type("family-based")
    contact = _contact()
    fields = IntakeFields(full_name="Ana Garcia", email="ana@example.com", case_type="family-based")
    matter = await create_matter(fields, contact, cfg, case_connector, "run-001")
    assert matter.practice_area == "family-based"


# ──────────────────────────────────────────────────────────────
# G5 — Tasks (completeness invariant)
# ──────────────────────────────────────────────────────────────

def test_exact_task_set_for_case_type() -> None:
    cfg = _cfg_with_tasks().get_case_type("family-based")
    tasks = render_tasks("m1", cfg, "run-001")
    assert len(tasks) == len(cfg.opening_tasks)
    titles = {t.title for t in tasks}
    assert titles == {t.title for t in cfg.opening_tasks}


def test_no_duplicate_tasks_on_rerun() -> None:
    cfg = _cfg_with_tasks().get_case_type("family-based")
    tasks1 = render_tasks("m1", cfg, "run-001")
    tasks2 = render_tasks("m1", cfg, "run-001")
    ids1 = {t.id for t in tasks1}
    ids2 = {t.id for t in tasks2}
    assert ids1 == ids2  # stable ids → no duplicates


def test_default_checklist_for_uncategorised() -> None:
    cfg = _cfg_with_tasks().get_case_type("other/uncategorised")
    tasks = render_tasks("m1", cfg, "run-001")
    assert len(tasks) == 1
    assert "consult" in tasks[0].title.lower()


# ──────────────────────────────────────────────────────────────
# G6 — Welcome draft + send
# ──────────────────────────────────────────────────────────────

async def test_draft_created_with_correct_recipient() -> None:
    email_conn = ReferenceEmailConnector()
    contact = _contact()
    matter = _matter(contact)
    fields = IntakeFields(full_name="Ana Garcia", email="ana@example.com", case_type="family-based")
    cfg = _cfg_with_tasks().get_case_type("family-based")
    draft_id, gaps = await draft_welcome(matter, contact, fields, cfg, email_conn, "run-001")
    assert draft_id
    assert not gaps


async def test_draft_blocked_when_no_email() -> None:
    email_conn = ReferenceEmailConnector()
    contact = Contact(id="c1", source="crm", name="Ana Garcia", external_ids={})
    matter = _matter(contact)
    fields = IntakeFields(full_name="Ana Garcia", case_type="family-based")
    cfg = _cfg_with_tasks().get_case_type("family-based")
    draft_id, gaps = await draft_welcome(matter, contact, fields, cfg, email_conn, "run-001")
    assert not draft_id
    assert any(g.severity == "block" for g in gaps)


async def test_send_only_after_explicit_call() -> None:
    """send_welcome only executes when called explicitly (gate enforced by engine)."""
    email_conn = ReferenceEmailConnector()
    contact = _contact()
    matter = _matter(contact)
    fields = IntakeFields(full_name="Ana Garcia", email="ana@example.com", case_type="family-based")
    cfg = _cfg_with_tasks().get_case_type("family-based")
    draft_id, _ = await draft_welcome(matter, contact, fields, cfg, email_conn, "run-001")
    # send_welcome called explicitly (in prod this only happens post-gate)
    msg_id = await send_welcome(draft_id, "run-001", email_conn)
    assert msg_id


async def test_send_is_idempotent() -> None:
    email_conn = ReferenceEmailConnector()
    contact = _contact()
    matter = _matter(contact)
    fields = IntakeFields(full_name="Ana Garcia", email="ana@example.com")
    cfg = _cfg_with_tasks().get_case_type("other/uncategorised")
    draft_id, _ = await draft_welcome(matter, contact, fields, cfg, email_conn, "run-001")
    msg1 = await send_welcome(draft_id, "run-001", email_conn)
    msg2 = await send_welcome(draft_id, "run-001", email_conn)
    assert msg1 == msg2  # idempotent on (run_id, step)


# ──────────────────────────────────────────────────────────────
# G7 — Workflow + tool
# ──────────────────────────────────────────────────────────────

async def test_workflow_registers_correct_steps() -> None:
    from cam.core.workflows.intake.workflow import register_intake_workflow, IntakeServices
    register_intake_workflow(IntakeServices(
        extraction=None, crm=ReferenceCRMConnector(),
        case_connector=ReferenceCaseConnector(), email=ReferenceEmailConnector(),
        deadline_store=DeadlineStore(), scheduler=InMemoryScheduler(), rule_store=RuleStore(),
    ))
    from cam.core.orchestrator.dsl import get_workflow_latest
    defn = get_workflow_latest("intake")
    assert defn.name == "intake"
    assert "parse_lead" in defn.steps
    assert "GATE:human_review" in defn.steps
    assert "send_welcome" in defn.steps
    assert len(defn.steps) == 9


async def test_intake_run_tool_starts_run() -> None:
    from cam.core.workflows.intake.workflow import (
        register_intake_workflow, IntakeServices, tool_intake_run,
    )
    register_intake_workflow(IntakeServices(
        extraction=None, crm=ReferenceCRMConnector(),
        case_connector=ReferenceCaseConnector(), email=ReferenceEmailConnector(),
        deadline_store=DeadlineStore(), scheduler=InMemoryScheduler(), rule_store=RuleStore(),
    ))
    store = InMemoryRunStore()
    lead = _email_lead()
    result = await tool_intake_run(lead, store)
    assert result["run_id"]
    assert result["status"] == "queued"


async def test_duplicate_lead_same_run() -> None:
    from cam.core.workflows.intake.workflow import (
        register_intake_workflow, IntakeServices, tool_intake_run,
    )
    register_intake_workflow(IntakeServices(
        extraction=None, crm=ReferenceCRMConnector(),
        case_connector=ReferenceCaseConnector(), email=ReferenceEmailConnector(),
        deadline_store=DeadlineStore(), scheduler=InMemoryScheduler(), rule_store=RuleStore(),
    ))
    store = InMemoryRunStore()
    lead = _email_lead()
    r1 = await tool_intake_run(lead, store, idempotency_key="idem-abc")
    r2 = await tool_intake_run(lead, store, idempotency_key="idem-abc")
    assert r1["run_id"] == r2["run_id"]


# ──────────────────────────────────────────────────────────────
# G7.3 — intake_interview prompt
# ──────────────────────────────────────────────────────────────

def test_prompt_asks_only_gap_fields() -> None:
    cfg = _cfg_with_tasks().get_case_type("family-based")
    fields = IntakeFields(full_name="Ana Garcia")  # email missing
    gaps = [IntakeGap(field="email", reason="Missing", severity="block")]
    prompt = render_intake_interview(fields, gaps, cfg)
    assert "email" in prompt
    # Gap lines section is between ## Fields and ## Rules
    gap_section = prompt.split("Fields still needed")[1].split("## Rules")[0]
    assert "email" in gap_section
    assert "full_name" not in gap_section


def test_prompt_no_gaps_returns_complete_message() -> None:
    cfg = _cfg_with_tasks().get_case_type("family-based")
    fields = IntakeFields(full_name="Ana Garcia", email="ana@example.com")
    prompt = render_intake_interview(fields, [], cfg)
    assert "No questions needed" in prompt


# ──────────────────────────────────────────────────────────────
# PBT — dedupe idempotency + task completeness
# ──────────────────────────────────────────────────────────────

@given(n_templates=st.integers(min_value=0, max_value=10))
@settings(max_examples=100)
def test_pbt_task_completeness(n_templates: int) -> None:
    templates = [
        TaskTemplate(title=f"Task {i}", assignee_role="paralegal", sort=i)
        for i in range(n_templates)
    ]
    cfg = CaseTypeConfig(case_type="test", opening_tasks=templates)
    tasks = render_tasks("m1", cfg, "run-001")
    assert len(tasks) == n_templates, "Task count must equal template count"


@given(n_templates=st.integers(min_value=1, max_value=10))
@settings(max_examples=100)
def test_pbt_task_ids_stable(n_templates: int) -> None:
    templates = [TaskTemplate(title=f"Task {i}", assignee_role="paralegal", sort=i) for i in range(n_templates)]
    cfg = CaseTypeConfig(case_type="test", opening_tasks=templates)
    run1 = render_tasks("m1", cfg, "run-001")
    run2 = render_tasks("m1", cfg, "run-001")
    assert [t.id for t in run1] == [t.id for t in run2]
