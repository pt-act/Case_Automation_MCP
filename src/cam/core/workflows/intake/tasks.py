"""open_tasks step — render TaskTemplates into Task records — spec G5.2.

Completeness invariant: creates EXACTLY the configured set — no missing, no extra.
Idempotent on (run_id, step): re-running produces the same output without duplicates.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from cam.core.domain.models import Task
from cam.core.workflows.intake.types import CaseTypeConfig

ROLE_ASSIGNEE_MAP: dict[str, str] = {
    "paralegal": "paralegal_default",
    "attorney": "attorney_default",
    "intake_coordinator": "intake_coordinator_default",
}


def render_tasks(
    matter_id: str,
    case_cfg: CaseTypeConfig,
    run_id: str,
    now: datetime | None = None,
) -> list[Task]:
    """Render the case type's task templates into Task records.

    The output set is exactly `case_cfg.opening_tasks` — no more, no fewer.
    Idempotent: the same (run_id, matter_id, template.title) always maps to
    the same task id (via deterministic uuid5 derivation).
    """
    now = now or datetime.now(tz=UTC)
    tasks: list[Task] = []

    for template in sorted(case_cfg.opening_tasks, key=lambda t: t.sort):
        # Derive a stable task id so re-runs don't create duplicate rows
        task_id = _stable_task_id(run_id, matter_id, template.title)
        due_at = (
            now + timedelta(days=template.due_offset_days)
            if template.due_offset_days is not None
            else None
        )
        assignee = ROLE_ASSIGNEE_MAP.get(template.assignee_role, template.assignee_role)
        tasks.append(Task(
            id=task_id,
            matter_id=matter_id,
            title=template.title,
            assignee=assignee,
            due_at=due_at,
            status="open",
        ))

    return tasks


def _stable_task_id(run_id: str, matter_id: str, title: str) -> str:
    namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    return str(uuid.uuid5(namespace, f"{run_id}:{matter_id}:{title}"))
