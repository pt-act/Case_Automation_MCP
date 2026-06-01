"""Gate abstraction core — token issuance, verification, resolve_gate — spec §4.4, §5.6, §7.

Token wire format (raw token, never persisted):
    base64url(json_payload) + "." + hex(hmac_sha256(signing_key, base64url(payload)))

Only SHA-256 of the raw token is stored in the DB (token_hash).
The raw token lives in the delivered channel (email link / web URL / MCP arg).

Single-use enforcement is atomic: `mark_used` returns False if already used,
preventing double-resolution under concurrent channel submissions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from cam.core.orchestrator.states import (
    ApprovalDecision,
    ApprovalToken,
    GateRequest,
    RunStatus,
    WorkflowRun,
)

DEFAULT_TOKEN_TTL_SECONDS = 86400  # 24 h (ASSUMPTION confirm)
KEY_ID = "v1"  # signing key version tag


# ---------------------------------------------------------------------------
# Token issuance
# ---------------------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _sign(key: bytes, payload_b64: str) -> str:
    return hmac.new(key, payload_b64.encode(), hashlib.sha256).hexdigest()


def issue_token(
    gate_request_id: str,
    run_id: str,
    step: str,
    channel: Literal["mcp", "web", "email"],
    signing_key: bytes,
    ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
) -> tuple[str, ApprovalToken]:
    """Issue a signed, single-use approval token.

    Returns (raw_token, ApprovalToken).  Only the ApprovalToken is stored
    (with token_hash, not the raw token).  The raw token is delivered to the
    approver via the channel.
    """
    token_id = str(uuid.uuid4())
    expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=ttl_seconds)

    payload = {
        "tid": token_id,
        "gid": gate_request_id,
        "rid": run_id,
        "step": step,
        "exp": expires_at.timestamp(),
    }
    payload_b64 = _b64(json.dumps(payload, sort_keys=True).encode())
    signature = _sign(signing_key, payload_b64)
    raw_token = f"{payload_b64}.{signature}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    approval_token = ApprovalToken(
        id=token_id,
        gate_request_id=gate_request_id,
        run_id=run_id,
        step=step,
        key_id=KEY_ID,
        token_hash=token_hash,
        channel=channel,
        expires_at=expires_at,
    )
    return raw_token, approval_token


def verify_token(raw_token: str, signing_key: bytes) -> tuple[dict[str, Any] | None, str | None]:
    """Verify the token signature and decode the payload.

    Returns (payload_dict, None) on success, or (None, error_reason) on failure.
    Does NOT check expiry or single-use — the caller does those.
    """
    parts = raw_token.split(".", 1)
    if len(parts) != 2:
        return None, "malformed"
    payload_b64, provided_sig = parts
    expected_sig = _sign(signing_key, payload_b64)
    if not hmac.compare_digest(expected_sig, provided_sig):
        return None, "tampered"
    try:
        missing = 4 - len(payload_b64) % 4
        padded = payload_b64 + "=" * (missing % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None, "malformed"
    return payload, None


# ---------------------------------------------------------------------------
# In-memory RunStore protocol (production uses DB, tests use this)
# ---------------------------------------------------------------------------


class RunStore:
    """Abstract base — concrete implementations in repository.py and tests."""

    async def get_run(self, run_id: str) -> WorkflowRun | None: ...
    async def update_run_status(self, run_id: str, status: RunStatus, **kwargs: Any) -> None: ...
    async def get_gate_request(self, gate_id: str) -> GateRequest | None: ...
    async def update_gate_request(self, gate: GateRequest) -> None: ...
    async def get_token(self, token_id: str) -> ApprovalToken | None: ...
    async def mark_token_used(self, token_id: str, used_at: datetime) -> bool: ...  # True = first use
    async def save_approval_decision(self, decision: ApprovalDecision) -> None: ...


# ---------------------------------------------------------------------------
# resolve_gate — the single authority for all approval/rejection
# ---------------------------------------------------------------------------


class GateResolutionError(Exception):
    def __init__(self, reason: str, status_code: int = 400) -> None:
        self.reason = reason
        self.status_code = status_code
        super().__init__(reason)


async def resolve_gate(
    raw_token: str,
    decision: Literal["approve", "reject"],
    actor: str,
    channel: Literal["mcp", "web", "email"],
    signing_key: bytes,
    store: Any,  # RunStore protocol
    audit_fn: Any | None = None,  # AuditService.record — optional
) -> ApprovalDecision:
    """Verify token → expiry → single-use → authz → record → resume/reject.

    All failure paths are audited.  Returns the recorded ApprovalDecision.
    Raises GateResolutionError on any invalid condition.
    """
    from cam.security.rbac import Permission, Principal, Role, authorize

    # 1. Verify signature
    payload, err = verify_token(raw_token, signing_key)
    if err:
        await _maybe_audit(audit_fn, actor, "gate.token_verify_failed", {"reason": err})
        raise GateResolutionError(f"Token verification failed: {err}", 401)

    token_id: str = payload["tid"]
    gate_request_id: str = payload["gid"]
    run_id: str = payload["rid"]
    step: str = payload["step"]
    exp_ts: float = payload["exp"]

    # 2. Check expiry
    if datetime.now(tz=timezone.utc).timestamp() > exp_ts:
        await _maybe_audit(audit_fn, actor, "gate.token_expired", {"token_id": token_id})
        raise GateResolutionError("Token has expired.", 401)

    # 3. Load token record
    token_record = await store.get_token(token_id)
    if token_record is None:
        raise GateResolutionError("Token not found.", 404)

    # 4. Single-use (atomic mark_used)
    first_use = await store.mark_token_used(token_id, datetime.now(tz=timezone.utc))
    if not first_use:
        await _maybe_audit(audit_fn, actor, "gate.token_reused", {"token_id": token_id})
        raise GateResolutionError("Token has already been used.", 409)

    # 5. Load gate request
    gate = await store.get_gate_request(gate_request_id)
    if gate is None:
        raise GateResolutionError("Gate request not found.", 404)
    if gate.status != "pending":
        raise GateResolutionError(f"Gate is already {gate.status!r}.", 409)

    # 6. AuthZ — actor must hold the gate's required_role AND have GATE_APPROVE permission.
    # We treat the actor string as their role identifier (in production the role comes
    # from the authenticated session; here it is passed directly for simplicity).
    try:
        actor_role = Role(actor)
    except ValueError:
        actor_role = None  # unknown role → deny

    if actor_role is None or actor_role != Role(gate.required_role):
        await _maybe_audit(audit_fn, actor, "gate.authz_denied", {"required_role": gate.required_role})
        raise GateResolutionError("Insufficient role to approve this gate.", 403)

    principal = Principal(identity=actor, roles=frozenset([actor_role]))
    if not authorize(principal, Permission.GATE_APPROVE):
        await _maybe_audit(audit_fn, actor, "gate.authz_denied", {"required_role": gate.required_role})
        raise GateResolutionError("Insufficient role to approve this gate.", 403)

    # 7. Record decision
    decided_at = datetime.now(tz=timezone.utc)
    approval_decision = ApprovalDecision(
        gate_request_id=gate_request_id,
        run_id=run_id,
        step=step,
        decision=decision,
        actor=actor,
        channel=channel,
        token_id=token_id,
        decided_at=decided_at,
    )
    await store.save_approval_decision(approval_decision)

    # 8. Update gate status
    gate = gate.model_copy(update={"status": "approved" if decision == "approve" else "rejected"})
    await store.update_gate_request(gate)

    # 9. Audit + resume/reject the run
    action = "gate.approved" if decision == "approve" else "gate.rejected"
    await _maybe_audit(
        audit_fn, actor, action,
        {"run_id": run_id, "step": step, "channel": channel, "token_id": token_id}
    )

    new_run_status = RunStatus.RUNNING if decision == "approve" else RunStatus.REJECTED
    await store.update_run_status(run_id, new_run_status)

    # On approval: mark the gate step as SUCCEEDED so the engine skips it on resume.
    # Gate steps never execute handlers — they go PENDING → SUCCEEDED when resolved.
    if decision == "approve" and hasattr(store, "advance_gate_step"):
        await store.advance_gate_step(run_id, step)

    return approval_decision


async def _maybe_audit(audit_fn: Any, actor: str, action: str, inputs: dict) -> None:
    if audit_fn is None:
        return
    try:
        await audit_fn(actor=actor, action=action, inputs=inputs, outputs={})
    except Exception:
        pass  # never let audit failure block gate resolution
