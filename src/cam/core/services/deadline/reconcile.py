"""Reconciler — detect drift between engine and case system; alert-only — spec §5, G7.

Never auto-mutates the source of record.
ConnectorError → park reconciliation + alert (absence of data ≠ no drift).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from cam.core.services.deadline.schedule import DeadlineStore
from cam.core.services.deadline.types import Drift

log = structlog.get_logger(__name__)


class Reconciler:
    """Compare engine deadlines against the case system of record."""

    def __init__(
        self,
        store: DeadlineStore,
        case_connector: Any | None = None,  # CaseConnector port
        alert_fn: Any | None = None,
        audit_fn: Any | None = None,
    ) -> None:
        self._store = store
        self._case = case_connector
        self._alert = alert_fn
        self._audit = audit_fn

    async def reconcile(self, matter_id: str) -> list[Drift]:
        """Compare engine records vs case system for a matter.

        Returns Drift findings.  On ConnectorError → parks + alerts, never treats
        absence of case data as "no drift".
        """
        now = datetime.now(tz=UTC)
        engine_deadlines = {
            sd.deadline_id: sd for sd in self._store.list_for_matter(matter_id)
        }

        # Fetch from case system
        case_deadlines: list[Any] = []
        if self._case is not None:
            try:
                case_deadlines = await self._case.list_deadlines(matter_id)
            except Exception as exc:
                # ConnectorError or any error → park reconciliation, alert
                log.error(
                    "reconcile.connector_error",
                    matter_id=matter_id,
                    error=str(exc),
                )
                await self._maybe_alert(
                    "reconciliation_connector_error",
                    {"matter_id": matter_id, "error": str(exc)},
                )
                await self._maybe_audit("reconcile.connector_error", matter_id, {})
                return []  # Return empty (parked); do NOT treat as no-drift

        findings: list[Drift] = []
        {str(d.id): d for d in case_deadlines}

        # Missing in case system
        for did, sd in engine_deadlines.items():
            rule_id = sd.rule_ref.rule_id
            match = next(
                (d for d in case_deadlines if getattr(d, "rule_id", None) == rule_id), None
            )
            if match is None:
                findings.append(Drift(
                    matter_id=matter_id,
                    deadline_id=did,
                    kind="missing_in_case",
                    detail=f"Rule {rule_id!r} scheduled in engine but not found in case system.",
                    detected_at=now,
                ))
            elif abs((match.due_at - sd.trace.due_at).total_seconds()) > 86400:
                findings.append(Drift(
                    matter_id=matter_id,
                    deadline_id=did,
                    kind="divergent_due_at",
                    detail=(
                        f"Engine due_at={sd.trace.due_at.isoformat()}, "
                        f"case due_at={match.due_at.isoformat()}"
                    ),
                    detected_at=now,
                ))

        # Missing in engine
        for case_d in case_deadlines:
            case_rule_id: str | None = getattr(case_d, "rule_id", None)
            in_engine = any(sd.rule_ref.rule_id == case_rule_id for sd in engine_deadlines.values())
            if not in_engine and case_rule_id:
                findings.append(Drift(
                    matter_id=matter_id,
                    kind="missing_in_engine",
                    detail=f"Case system has deadline with rule_id={case_rule_id!r} not in engine.",
                    detected_at=now,
                ))

        if findings:
            log.warning("reconcile.drift_found", matter_id=matter_id, count=len(findings))
            for f in findings:
                await self._maybe_alert("reconciliation_drift",
                    {"kind": f.kind, "matter_id": matter_id})

        await self._maybe_audit("reconcile.complete", matter_id, {"drift_count": len(findings)})
        return findings

    async def _maybe_alert(self, event: str, payload: dict[str, Any]) -> None:
        if self._alert:
            try:
                await self._alert(event=event, **payload)
            except Exception:
                pass

    async def _maybe_audit(self, action: str, matter_id: str, outputs: dict[str, Any]) -> None:
        if self._audit:
            try:
                await self._audit(
                    actor="deadline_reconciler",
                    action=action,
                    inputs={"matter_id": matter_id},
                    outputs=outputs,
                )
            except Exception:
                pass
