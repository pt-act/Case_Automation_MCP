"""Intake workflow registration + intake.run MCP tool — spec G7.

Workflow steps (exactly per PTD §7):
  parse_lead → dedupe_contact → create_contact → create_matter →
  compute_deadlines → open_tasks → draft_welcome → GATE:human_review →
  send_welcome

**Dependency injection:** services are passed explicitly to `register_intake_workflow(services)`.
The workflow class captures them via closure — no module-level mutable singleton.

Before (fragile):
    configure_services(svc)   # mutates global
    register_intake_workflow()

After (explicit):
    register_intake_workflow(services)  # services injected at registration time
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import structlog

from cam.core.orchestrator.dsl import GateConfig, StepContext, workflow
from cam.core.orchestrator.triggers import make_agent_trigger, start_run
from cam.core.workflows.intake.config import IntakeConfig, get_intake_config
from cam.core.workflows.intake.types import LeadPayload

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Service container  (plain dataclass — no globals)
# ---------------------------------------------------------------------------


@dataclass
class IntakeServices:
    """All dependencies for the intake workflow.  Pass to register_intake_workflow()."""

    extraction: Any           # ExtractionService | None
    crm: Any                  # CRMConnector
    case_connector: Any       # CaseConnector
    email: Any                # EmailConnector
    deadline_store: Any       # DeadlineStore
    scheduler: Any            # Scheduler
    rule_store: Any           # RuleStore
    config: IntakeConfig | None = None


# ---------------------------------------------------------------------------
# Workflow registration  (closure-based DI — no get_services())
# ---------------------------------------------------------------------------


def register_intake_workflow(services: IntakeServices) -> None:
    """Register the intake workflow with the orchestration engine.

    Args:
        services: All dependencies.  Captured via closure; no global state.

    Safe to call multiple times (registry guards against duplicate registration).
    """
    svc = services  # captured in closure; never mutated after this point

    @workflow("intake", version=1)
    class IntakeWorkflow:
        steps = [
            "parse_lead",
            "dedupe_contact",
            "create_contact",
            "create_matter",
            "compute_deadlines",
            "open_tasks",
            "draft_welcome",
            "GATE:human_review",
            "send_welcome",
        ]
        gates = {
            "human_review": GateConfig(
                required_role="attorney",
                channels=["mcp", "web", "email"],
                ttl_seconds=86400,
            )
        }

        async def parse_lead(self, ctx: StepContext) -> dict:
            cfg = svc.config or get_intake_config()
            lead_data = ctx.context.get("lead", {})
            case_type_hint = ctx.context.get("case_type_hint")
            lead = LeadPayload(**lead_data) if isinstance(lead_data, dict) else lead_data

            proposal = None
            if svc.extraction and lead.raw.get("body"):
                from cam.core.services.extraction.types import ExtractInput
                inp = ExtractInput(input_ref=ctx.run_id,
                    content_kind="email_body",
                    structuring=False)
                try:
                    proposal = svc.extraction.extract(inp, lead.raw["body"].encode())
                except Exception:
                    pass

            from cam.core.workflows.intake.parse import parse_lead
            fields, gaps = parse_lead(lead, proposal, case_type_hint, cfg)
            return {"fields": fields.model_dump(), "gaps": [g.model_dump() for g in gaps]}

        async def dedupe_contact(self, ctx: StepContext) -> dict:
            from cam.core.workflows.intake.dedupe import dedupe_contact
            from cam.core.workflows.intake.types import IntakeFields
            prior = ctx.output("parse_lead") or {}
            fields = IntakeFields(**prior.get("fields", {}))
            dedupe_result, gaps = await dedupe_contact(fields, svc.crm)
            return {"dedupe": dedupe_result.model_dump(), "gaps": [g.model_dump() for g in gaps]}

        async def create_contact(self, ctx: StepContext) -> dict:
            from cam.core.workflows.intake.create import create_contact
            from cam.core.workflows.intake.types import DedupeResult, IntakeFields
            parse_out = ctx.output("parse_lead") or {}
            dedupe_out = ctx.output("dedupe_contact") or {}
            fields = IntakeFields(**parse_out.get("fields", {}))
            dedupe = DedupeResult(**dedupe_out.get("dedupe", {"decision": "new_contact"}))
            contact = await create_contact(fields, dedupe, svc.crm, ctx.run_id)
            return {"contact": contact.model_dump()}

        async def create_matter(self, ctx: StepContext) -> dict:
            from cam.core.domain.models import Contact
            from cam.core.workflows.intake.create import create_matter
            from cam.core.workflows.intake.types import IntakeFields
            cfg = svc.config or get_intake_config()
            parse_out = ctx.output("parse_lead") or {}
            contact_out = ctx.output("create_contact") or {}
            fields = IntakeFields(**parse_out.get("fields", {}))
            contact = Contact(**contact_out["contact"])
            case_cfg = cfg.get_case_type(fields.case_type)
            matter = await create_matter(fields, contact, case_cfg, svc.case_connector, ctx.run_id)
            return {"matter": matter.model_dump()}

        async def compute_deadlines(self, ctx: StepContext) -> dict:
            from cam.core.domain.models import Matter
            from cam.core.workflows.intake.deadlines import compute_deadlines
            from cam.core.workflows.intake.types import IntakeFields
            cfg = svc.config or get_intake_config()
            parse_out = ctx.output("parse_lead") or {}
            matter_out = ctx.output("create_matter") or {}
            fields = IntakeFields(**parse_out.get("fields", {}))
            matter = Matter(**matter_out["matter"])
            case_cfg = cfg.get_case_type(fields.case_type)
            deadlines = await compute_deadlines(
                matter, fields, case_cfg, svc.rule_store, svc.deadline_store, svc.scheduler,
                    ctx.run_id
            )
            return {"deadline_ids": [d.id for d in deadlines]}

        async def open_tasks(self, ctx: StepContext) -> dict:
            from cam.core.domain.models import Matter
            from cam.core.workflows.intake.tasks import render_tasks
            from cam.core.workflows.intake.types import IntakeFields
            cfg = svc.config or get_intake_config()
            parse_out = ctx.output("parse_lead") or {}
            matter_out = ctx.output("create_matter") or {}
            fields = IntakeFields(**parse_out.get("fields", {}))
            matter = Matter(**matter_out["matter"])
            case_cfg = cfg.get_case_type(fields.case_type)
            tasks = render_tasks(matter.id, case_cfg, ctx.run_id)
            return {"task_ids": [t.id for t in tasks], "task_count": len(tasks)}

        async def draft_welcome(self, ctx: StepContext) -> dict:
            from cam.core.domain.models import Contact, Matter
            from cam.core.workflows.intake.types import IntakeFields
            from cam.core.workflows.intake.welcome import draft_welcome
            cfg = svc.config or get_intake_config()
            parse_out = ctx.output("parse_lead") or {}
            matter_out = ctx.output("create_matter") or {}
            contact_out = ctx.output("create_contact") or {}
            fields = IntakeFields(**parse_out.get("fields", {}))
            matter = Matter(**matter_out["matter"])
            contact = Contact(**contact_out["contact"])
            case_cfg = cfg.get_case_type(fields.case_type)
            draft_id, gaps = await draft_welcome(matter,
                contact,
                fields,
                case_cfg,
                svc.email,
                ctx.run_id)
            return {"draft_id": draft_id, "gaps": [g.model_dump() for g in gaps]}

        async def send_welcome(self, ctx: StepContext) -> dict:
            from cam.core.workflows.intake.welcome import send_welcome
            draft_out = ctx.output("draft_welcome") or {}
            draft_id = draft_out.get("draft_id", "")
            message_id = await send_welcome(draft_id, ctx.run_id, svc.email)
            return {"message_id": message_id}


# ---------------------------------------------------------------------------
# intake.run MCP tool
# ---------------------------------------------------------------------------


async def tool_intake_run(
    lead: LeadPayload,
    run_store: Any,
    case_type_hint: str | None = None,
    idempotency_key: str | None = None,
    actor: str = "agent_service",
) -> dict:
    """intake.run — start or locate an intake workflow run.

    Idempotent: the same lead re-submitted returns the existing run.
    Risk tier: composite (write/confirm steps inside; send is gated).
    """
    if idempotency_key is None:
        base = f"{lead.source_channel}:{lead.provider_event_id or ''}"
        if not lead.provider_event_id:
            raw_email = lead.raw.get("email", "")
            raw_name = lead.raw.get("full_name", lead.raw.get("name", ""))
            base = f"{lead.source_channel}:{raw_email}:{raw_name}"
        idempotency_key = hashlib.sha256(base.encode()).hexdigest()[:16]

    trigger = make_agent_trigger(actor, idem_key=idempotency_key)
    context = {"lead": lead.model_dump(mode="json"), "case_type_hint": case_type_hint}
    run = await start_run(
        workflow_name="intake", context=context,
        trigger=trigger, store=run_store, idem_key=idempotency_key,
    )
    return {"run_id": run.id, "status": run.status, "current_step": run.current_step,
            "is_new": run.trigger.dedupe_key == idempotency_key}


# ---------------------------------------------------------------------------
# Backward-compatibility shim  (deprecated — use register_intake_workflow(services))
# ---------------------------------------------------------------------------

_services: IntakeServices | None = None


def configure_services(svc: IntakeServices) -> None:
    """Deprecated: pass services directly to register_intake_workflow(services)."""
    global _services
    _services = svc
    log.warning("intake.configure_services.deprecated",
                msg="Use register_intake_workflow(services) directly.")


def get_services() -> IntakeServices:
    """Deprecated: services are now closure-injected at registration time."""
    if _services is None:
        raise RuntimeError(
            "Intake services not configured. "
            "Call register_intake_workflow(services)."
        )
    return _services
