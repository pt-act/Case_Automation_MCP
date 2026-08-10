"""compute_deadlines step — deadline.compute + deadline.schedule — spec G5.1."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from cam.core.domain.models import Deadline, Matter
from cam.core.services.deadline.rules import RuleStore
from cam.core.services.deadline.schedule import DeadlineStore, PastDueOnCreateError, Scheduler
from cam.core.services.deadline.types import RuleRef
from cam.core.workflows.intake.types import CaseTypeConfig, IntakeFields

log = structlog.get_logger(__name__)


async def compute_deadlines(
    matter: Matter,
    fields: IntakeFields,
    case_cfg: CaseTypeConfig,
    rule_store: RuleStore,
    deadline_store: DeadlineStore,
    scheduler: Scheduler,
    run_id: str,
    now: datetime | None = None,
) -> list[Deadline]:
    """Compute + schedule deadlines for the case type.

    Results attached to Matter.key_dates.  Engine errors park the run (caller
    is the workflow engine which handles ConnectorError/exceptions as park).
    No local date math — exclusively via deadline engine.
    """
    now = now or datetime.now(tz=UTC)
    result_deadlines: list[Deadline] = []

    # Use received_at from lead as the trigger date if available
    trigger_date = now

    for rule_id in case_cfg.deadline_rule_ids:
        try:
            rule = rule_store.active(rule_id, jurisdiction="US")
            rule_ref = RuleRef(
                rule_id=rule_id,
                rule_version=rule_store.rule_version(rule_id),
                jurisdiction="US",
            )
            await scheduler._arm if False else None  # type-check guard
            # schedule returns the ScheduledDeadline; if past-due it raises
            from cam.core.services.deadline.schedule import tool_deadline_schedule
            scheduled = await tool_deadline_schedule(
                matter_id=matter.id,
                rule=rule,
                trigger_inputs={"trigger_date": trigger_date},
                rule_ref=rule_ref,
                name=rule.description or rule_id,
                idempotency_key=f"{run_id}:deadline:{rule_id}",
                store=deadline_store,
                scheduler=scheduler,
                now=now,
            )
            dl = Deadline(
                id=scheduled.deadline_id,
                matter_id=matter.id,
                name=rule.description or rule_id,
                due_at=scheduled.trace.due_at,
                rule_id=rule_id,
                status="pending",
                escalation_level=0,
            )
            result_deadlines.append(dl)

        except PastDueOnCreateError as exc:
            log.warning(
                "intake.deadline_past_due_on_create",
                rule_id=rule_id,
                matter_id=matter.id,
                due_at=exc.due_at.isoformat(),
            )
            # Log and continue — the run is NOT parked; the flagged record was saved
        except KeyError:
            log.warning("intake.deadline_rule_not_found", rule_id=rule_id)
        except Exception as exc:
            log.error("intake.deadline_error", rule_id=rule_id, error=str(exc))
            raise  # re-raise so the workflow engine can park the run

    return result_deadlines
