"""Approval channel adapters — MCP callback, web UI, email action — spec §4.4, G8.

All three channels converge on resolve_gate() (gates.py).  This module owns
the emit side: how each channel is notified when a gate is created.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

import structlog

from cam.core.orchestrator.states import ApprovalToken, GateRequest

log = structlog.get_logger(__name__)


@runtime_checkable
class ApprovalChannel(Protocol):
    """Port for a gate notification channel."""

    name: Literal["mcp", "web", "email"]

    async def emit(self, gate: GateRequest, token: ApprovalToken, raw_token: str) -> None:
        """Deliver the gate notification + token to the approver."""
        ...


# ---------------------------------------------------------------------------
# MCP channel — logs the token for the agent to pick up via approval.decide
# ---------------------------------------------------------------------------


class MCPApprovalChannel:
    """Notifies via structured log (the agent reads it from workflow.status)."""

    name: Literal["mcp"] = "mcp"

    async def emit(self, gate: GateRequest, token: ApprovalToken, raw_token: str) -> None:
        log.info(
            "gate.mcp_token_ready",
            run_id=gate.run_id,
            step=gate.step,
            gate_id=gate.id,
            token_id=token.id,
            expires_at=gate.expires_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# Web UI channel — emits approval URL (sidecar serves GET/POST /approvals/{token})
# ---------------------------------------------------------------------------


class WebApprovalChannel:
    """Emits an approval URL for the sidecar web UI."""

    name: Literal["web"] = "web"

    def __init__(self, base_url: str = "http://localhost:8001") -> None:
        self._base_url = base_url.rstrip("/")

    async def emit(self, gate: GateRequest, token: ApprovalToken, raw_token: str) -> None:
        url = f"{self._base_url}/approvals/{raw_token}"
        log.info(
            "gate.web_approval_url",
            run_id=gate.run_id,
            step=gate.step,
            gate_id=gate.id,
            approval_url=url,
            expires_at=gate.expires_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# Email channel — emits approve/reject action links via the email connector
# ---------------------------------------------------------------------------


class EmailApprovalChannel:
    """Sends an approval email with signed action links."""

    name: Literal["email"] = "email"

    def __init__(
        self,
        email_connector: Any | None = None,
        base_url: str = "http://localhost:8001",
    ) -> None:
        self._email = email_connector
        self._base_url = base_url.rstrip("/")

    async def emit(self, gate: GateRequest, token: ApprovalToken, raw_token: str) -> None:
        approve_url = f"{self._base_url}/approvals/{raw_token}?decision=approve"
        reject_url = f"{self._base_url}/approvals/{raw_token}?decision=reject"

        log.info(
            "gate.email_approval_links",
            run_id=gate.run_id,
            step=gate.step,
            gate_id=gate.id,
            approve_url=approve_url,
            reject_url=reject_url,
            expires_at=gate.expires_at.isoformat(),
        )

        if self._email is not None:
            try:
                import uuid

                from cam.core.domain.models import Communication

                comm = Communication(
                    id=str(uuid.uuid4()),
                    matter_id=None,
                    direction="out",
                    channel="email",
                    subject=f"Action required: {gate.step!r} approval",
                    body=(
                        f"A workflow is awaiting your approval.\n\n"
                        f"Approve: {approve_url}\n"
                        f"Reject:  {reject_url}\n\n"
                        f"This link expires at {gate.expires_at.isoformat()}."
                    ),
                    status="draft",
                )
                await self._email.create_draft(comm)
            except Exception as exc:
                log.warning("gate.email_draft_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Channel fan-out
# ---------------------------------------------------------------------------


async def emit_gate(
    gate: GateRequest,
    tokens: dict[str, tuple[str, ApprovalToken]],   # channel → (raw_token, ApprovalToken)
    channels: list[ApprovalChannel],
) -> None:
    """Emit the gate notification on every configured channel."""
    channel_map = {ch.name: ch for ch in channels}
    for channel_name, (raw_token, token_record) in tokens.items():
        ch = channel_map.get(channel_name)
        if ch is not None:
            try:
                await ch.emit(gate, token_record, raw_token)
            except Exception as exc:
                log.warning("gate.channel_emit_error", channel=channel_name, error=str(exc))
