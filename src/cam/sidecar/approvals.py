"""Web UI approval endpoint — GET/POST /approvals/{token} — spec G8.3."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalSubmission(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = None


@router.get("/{raw_token}", response_class=HTMLResponse)
async def approval_page(raw_token: str, request: Request) -> HTMLResponse:
    """Render the gate context for the approver."""
    from cam.core.orchestrator.gates import verify_token

    app_state = request.app.state
    signing_key: bytes = getattr(app_state, "signing_key", b"")

    payload, err = verify_token(raw_token, signing_key)
    if err:
        raise HTTPException(status_code=401, detail=f"Invalid token: {err}")

    import json
    from datetime import datetime, timezone

    exp = payload.get("exp", 0)
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Approval Required</title>
<style>body{{font-family:sans-serif;max-width:600px;margin:4rem auto;padding:0 1rem}}
.gate{{background:#f8f9fa;border-radius:8px;padding:1.5rem;margin-bottom:1.5rem}}
.approve{{background:#198754;color:#fff;border:none;padding:.75rem 2rem;
  border-radius:4px;cursor:pointer;font-size:1rem;margin-right:1rem}}
.reject{{background:#dc3545;color:#fff;border:none;padding:.75rem 2rem;
  border-radius:4px;cursor:pointer;font-size:1rem}}</style></head>
<body>
<h1>Workflow approval required</h1>
<div class="gate">
  <p><strong>Run:</strong> {payload.get("rid","")}</p>
  <p><strong>Step:</strong> {payload.get("step","")}</p>
  <p><strong>Expires:</strong> {expires_at}</p>
</div>
<form method="POST" action="/approvals/{raw_token}">
  <button class="approve" name="decision" value="approve" type="submit">Approve</button>
  <button class="reject" name="decision" value="reject" type="submit">Reject</button>
</form>
</body></html>"""
    return HTMLResponse(html)


@router.post("/{raw_token}")
async def submit_approval(
    raw_token: str,
    request: Request,
    decision: Literal["approve", "reject"] | None = None,
) -> JSONResponse:
    """Submit an approval decision via the web channel."""
    from cam.core.orchestrator.gates import GateResolutionError, resolve_gate

    app_state = request.app.state
    signing_key: bytes = getattr(app_state, "signing_key", b"")
    store = getattr(app_state, "run_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Run store unavailable.")

    # Support both form POST and query param
    if decision is None:
        form = await request.form()
        decision_raw = form.get("decision")
    decision = str(decision_raw) if decision_raw is not None else None
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'.")
    from typing import cast as _cast
    _decision: Literal["approve", "reject"] = _cast(Literal["approve", "reject"], decision)

    actor = getattr(request.state, "user_identity", "web_anonymous")

    try:
        result = await resolve_gate(
            raw_token=raw_token,
            decision=_decision,
            actor=actor,
            channel="web",
            signing_key=signing_key,
            store=store,
        )
    except GateResolutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.reason)

    return JSONResponse(
        {"decision": result.decision, "run_id": result.run_id, "gate": result.gate_request_id}
    )
