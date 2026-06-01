"""Status-update-emails workflow — registration + send + one-send seal — spec G3/G5/G6.

Workflow steps:
  detect_change → compute_delta → resolve_recipients → draft_email →
  qc_recipient_integrity → GATE:human_review → send_email → record_outcome

One-send guarantee: change_id → delivered flag, set only after send + audit.

**Dependency injection:** services are passed to register_status_update_workflow(services).
Closure captures them — no module-level mutable singleton.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog

from cam.core.orchestrator.dsl import GateConfig, StepContext, workflow
from cam.core.orchestrator.triggers import make_agent_trigger, start_run
from cam.core.workflows.status_update.change_id import sweep_change_id, webhook_change_id
from cam.core.workflows.status_update.store import LastKnownStatusStore, StatusUpdateConfig
from cam.core.workflows.status_update.types import MatterStatusDelta

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Service container
# ---------------------------------------------------------------------------


@dataclass
class StatusUpdateServices:
    case_connector: Any    # CaseConnector port (read)
    email: Any             # EmailConnector port
    status_store: LastKnownStatusStore
    config: StatusUpdateConfig
    audit_fn: Any | None = None


# ---------------------------------------------------------------------------
# Workflow definition  (closure-based DI — no module-level singleton)
# ---------------------------------------------------------------------------


def register_status_update_workflow(services: StatusUpdateServices | None = None) -> None:
    """Register the status-update-emails workflow with the orchestration engine.

    Args:
        services: Dependencies captured via closure.  If None, falls back to
                  the deprecated configure_status_update_services() singleton
                  for backward compatibility.
    """
    svc: StatusUpdateServices = services or get_services()

    @workflow("status_update_email", version=1)
    class StatusUpdateWorkflow:
        steps = [
            "detect_change",
            "compute_delta",
            "resolve_recipients",
            "draft_email",
            "qc_recipient_integrity",
            "GATE:human_review",
            "send_email",
            "record_outcome",
        ]
        gates = {
            "human_review": GateConfig(
                required_role="attorney",
                channels=["mcp", "web", "email"],
                ttl_seconds=86400,
            )
        }

        async def detect_change(self, ctx: StepContext) -> dict:
            delta = MatterStatusDelta(**ctx.context.get("delta", {}))
            triggered = svc.status_store.should_trigger(delta.to_status, svc.config)
            return {"triggered": triggered, "change_id": delta.change_id, "to_status": delta.to_status}

        async def compute_delta(self, ctx: StepContext) -> dict:
            delta = MatterStatusDelta(**ctx.context.get("delta", {}))
            svc.status_store.update_last_status(delta.matter_id, delta.to_status)
            return {"matter_id": delta.matter_id, "from_status": delta.from_status,
                    "to_status": delta.to_status}

        async def resolve_recipients(self, ctx: StepContext) -> dict:
            delta = MatterStatusDelta(**ctx.context.get("delta", {}))
            matter = await svc.case_connector.get_matter(delta.matter_id)
            from cam.core.workflows.status_update.draft import resolve_recipients
            recipients, gaps = resolve_recipients(matter)
            return {
                "recipient_ids": [r.id for r in recipients],
                "recipient_emails": [str(r.email) for r in recipients if r.email],
                "gaps": gaps,
                "matter_ref": matter.reference,
            }

        async def draft_email(self, ctx: StepContext) -> dict:
            delta = MatterStatusDelta(**ctx.context.get("delta", {}))
            matter = await svc.case_connector.get_matter(delta.matter_id)
            gaps = (ctx.output("resolve_recipients") or {}).get("gaps", [])
            if gaps:
                return {"draft_id": "", "gaps": gaps, "blocked": True}

            from cam.core.workflows.status_update.draft import (
                create_status_update_draft, resolve_recipients,
            )
            recipients, _ = resolve_recipients(matter)
            draft_id, prompt_ver = await create_status_update_draft(
                delta, matter, recipients, svc.email, ctx.run_id
            )
            if svc.audit_fn:
                try:
                    await svc.audit_fn(actor="status_update_workflow", action="draft_created",
                                       inputs={"run_id": ctx.run_id}, outputs={"draft_id": draft_id},
                                       run_id=ctx.run_id)
                except Exception:
                    pass
            return {"draft_id": draft_id, "prompt_version": prompt_ver, "blocked": False}

        async def qc_recipient_integrity(self, ctx: StepContext) -> dict:
            delta = MatterStatusDelta(**ctx.context.get("delta", {}))
            matter = await svc.case_connector.get_matter(delta.matter_id)
            if (ctx.output("draft_email") or {}).get("blocked"):
                return {"qc_result": "fail", "reason": "No valid recipients"}

            from cam.core.workflows.status_update.draft import resolve_recipients
            from cam.core.workflows.status_update.qc import run_recipient_integrity_qc
            recipients, _ = resolve_recipients(matter)
            result = await run_recipient_integrity_qc(matter, recipients, ctx.run_id, svc.audit_fn)
            if svc.audit_fn:
                try:
                    await svc.audit_fn(actor="status_update_workflow", action="qc_result",
                                       inputs={"run_id": ctx.run_id}, outputs={"result": result},
                                       run_id=ctx.run_id)
                except Exception:
                    pass
            return {"qc_result": result}

        async def send_email(self, ctx: StepContext) -> dict:
            draft_out = ctx.output("draft_email") or {}
            qc_out = ctx.output("qc_recipient_integrity") or {}
            delta = MatterStatusDelta(**ctx.context.get("delta", {}))
            if qc_out.get("qc_result") == "fail":
                return {"sent": False, "reason": "QC failed — no send"}
            if not draft_out.get("draft_id"):
                return {"sent": False, "reason": "No draft id — no send"}

            idem_key = f"status_update:{delta.change_id}"
            try:
                message_id = await svc.email.send(draft_out["draft_id"], idem_key)
                if svc.audit_fn:
                    try:
                        await svc.audit_fn(actor="status_update_workflow", action="email_sent",
                                           inputs={"idem_key": idem_key},
                                           outputs={"message_id": message_id}, run_id=ctx.run_id)
                    except Exception:
                        pass
                log.info("status_update.sent", message_id=message_id, run_id=ctx.run_id)
                return {"sent": True, "message_id": message_id}
            except Exception as exc:
                log.error("status_update.send_failed", error=str(exc), run_id=ctx.run_id)
                raise

        async def record_outcome(self, ctx: StepContext) -> dict:
            send_out = ctx.output("send_email") or {}
            delta = MatterStatusDelta(**ctx.context.get("delta", {}))
            if send_out.get("sent"):
                svc.status_store.mark_delivered(delta.change_id, ctx.run_id)
            return {"delivered": send_out.get("sent", False)}


# ---------------------------------------------------------------------------
# Backward-compatibility shim  (deprecated)
# ---------------------------------------------------------------------------

_services: StatusUpdateServices | None = None


def configure_status_update_services(svc: StatusUpdateServices) -> None:
    """Deprecated: pass services directly to register_status_update_workflow(services)."""
    global _services
    _services = svc


def get_services() -> StatusUpdateServices:
    """Deprecated: services are now closure-injected at registration time."""
    if _services is None:
        raise RuntimeError("Status-update services not configured.")
    return _services


# ---------------------------------------------------------------------------
# Trigger: webhook event → start run
# ---------------------------------------------------------------------------


async def handle_status_change_event(
    matter_id: str,
    from_status: str,
    to_status: str,
    provider_event_id: str,
    run_store: Any,
    services: StatusUpdateServices,
) -> dict | None:
    """Start a status-update run from a webhook event.

    Returns None if the status is not allow-listed (skip).
    Returns run summary dict if a run was started.
    """
    if not services.status_store.should_trigger(to_status, services.config):
        log.info("status_update.skipped", matter_id=matter_id, to_status=to_status)
        return None

    change_id = webhook_change_id(matter_id, from_status, to_status, provider_event_id)

    if services.status_store.is_delivered(change_id):
        log.info("status_update.already_delivered", change_id=change_id)
        return {"noop": True, "change_id": change_id}

    delta = MatterStatusDelta(
        matter_id=matter_id,
        from_status=from_status,
        to_status=to_status,
        change_id=change_id,
        source="webhook",
        detected_at=datetime.now(tz=timezone.utc),
    )
    trigger = make_agent_trigger("status_update_webhook", idem_key=change_id)
    run = await start_run(
        workflow_name="status_update_email",
        context={"delta": delta.model_dump(mode="json")},
        trigger=trigger,
        store=run_store,
        idem_key=change_id,
    )
    return {"run_id": run.id, "status": run.status, "change_id": change_id}


# ---------------------------------------------------------------------------
# Trigger: sweep
# ---------------------------------------------------------------------------


async def sweep_matters(
    matter_ids: list[str],
    run_store: Any,
    services: StatusUpdateServices,
) -> list[dict]:
    """Compare last-known vs current status for each matter; start runs on diffs."""
    results = []
    for matter_id in matter_ids:
        try:
            matter = await services.case_connector.get_matter(matter_id)
            current_status = matter.status
            last_status = services.status_store.get_last_status(matter_id)
            if last_status is None or last_status == current_status:
                continue  # no diff or no baseline

            counter = services.status_store.next_transition_counter(matter_id)
            change_id = sweep_change_id(matter_id, last_status, current_status, counter)

            if services.status_store.is_delivered(change_id):
                continue

            result = await handle_status_change_event(
                matter_id, last_status, current_status,
                provider_event_id=f"sweep-{change_id}",
                run_store=run_store,
                services=services,
            )
            if result:
                services.status_store.update_last_status(matter_id, current_status)
                results.append(result)
        except Exception as exc:
            log.error("status_update.sweep_error", matter_id=matter_id, error=str(exc))
    return results
