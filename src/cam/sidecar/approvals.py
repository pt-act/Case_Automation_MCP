"""Web UI approval endpoint — GET/POST /approvals/{token} — spec G8.3.

Human-facing pages: the approver arrives from an email/web link and must see
what they are approving, record a reason, and get a readable confirmation —
never raw JSON.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalSubmission(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = None


_PAGE_STYLE = """body{font-family:sans-serif;max-width:600px;margin:4rem auto;padding:0 1rem}
.card{background:#f8f9fa;border-radius:8px;padding:1.5rem;margin-bottom:1.5rem}
.approve{background:#198754;color:#fff;border:none;padding:.75rem 2rem;
  border-radius:4px;cursor:pointer;font-size:1rem;margin-right:1rem}
.reject{background:#dc3545;color:#fff;border:none;padding:.75rem 2rem;
  border-radius:4px;cursor:pointer;font-size:1rem}
textarea{width:100%;box-sizing:border-box;margin:1rem 0;padding:.5rem;
  border:1px solid #ccc;border-radius:4px;font-family:inherit}
.ok{color:#198754}.err{color:#dc3545}"""


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>{escape(title)}</title>
<style>{_PAGE_STYLE}</style></head>
<body>
{body}
</body></html>"""
    return HTMLResponse(html, status_code=status_code)


def _error_page(reason: str, status_code: int) -> HTMLResponse:
    body = f"""<h1 class="err">Approval not recorded</h1>
<div class="card"><p>{escape(reason)}</p>
<p>This link is single-use and expires 24 hours after it was issued.
Ask the requester to re-trigger the approval if you still need to act on it.</p></div>"""
    return _page("Approval not recorded", body, status_code=status_code)


@router.get("/{raw_token}", response_class=HTMLResponse)
async def approval_page(raw_token: str, request: Request) -> HTMLResponse:
    """Render the gate context for the approver."""
    from cam.core.orchestrator.gates import verify_token

    app_state = request.app.state
    signing_key: bytes = getattr(app_state, "signing_key", b"")

    payload, err = verify_token(raw_token, signing_key)
    if err:
        return _error_page(f"This approval link is not valid ({err}).", 401)

    assert payload is not None  # verified above
    run_id = str(payload.get("rid", ""))
    step = str(payload.get("step", ""))
    exp = payload.get("exp", 0)
    expires_at = datetime.fromtimestamp(exp, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    # Enrich with the workflow name when a run store is wired (fail-soft).
    workflow_name = ""
    store = getattr(app_state, "run_store", None)
    if store is not None and run_id:
        try:
            run = await store.get_run(run_id)
            if run is not None:
                workflow_name = str(run.workflow)
        except Exception:
            workflow_name = ""

    workflow_line = (
        f'<p><strong>Workflow:</strong> {escape(workflow_name)}</p>' if workflow_name else ""
    )
    body = f"""<h1>Workflow approval required</h1>
<div class="card">
  {workflow_line}
  <p><strong>Run:</strong> {escape(run_id)}</p>
  <p><strong>Step:</strong> {escape(step)}</p>
  <p><strong>Expires:</strong> {escape(expires_at)}</p>
</div>
<form method="POST" action="/approvals/{escape(raw_token)}">
  <label for="reason">Reason (optional, recorded with your decision):</label>
  <textarea id="reason" name="reason" rows="3"
    placeholder="e.g. Reviewed the draft; ready to send"></textarea>
  <button class="approve" name="decision" value="approve" type="submit">Approve</button>
  <button class="reject" name="decision" value="reject" type="submit">Reject</button>
</form>"""
    return _page("Approval required", body)


@router.post("/{raw_token}")
async def submit_approval(raw_token: str, request: Request) -> HTMLResponse:
    """Submit an approval decision via the web channel."""
    from cam.core.orchestrator.gates import GateResolutionError, resolve_gate

    app_state = request.app.state
    signing_key: bytes = getattr(app_state, "signing_key", b"")
    store = getattr(app_state, "run_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Run store unavailable.")

    # Support both form POST and query param
    decision: str | None = request.query_params.get("decision")
    if decision is None:
        form = await request.form()
        decision = form.get("decision")  # type: ignore[assignment]
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'.")
    _decision: Literal["approve", "reject"] = decision  # type: ignore[assignment]

    form = await request.form()
    reason_value = form.get("reason")
    reason = str(reason_value).strip() or None if isinstance(reason_value, str) else None

    actor = getattr(request.state, "user_identity", "web_anonymous")

    try:
        result = await resolve_gate(
            raw_token=raw_token,
            decision=_decision,
            actor=actor,
            channel="web",
            signing_key=signing_key,
            store=store,
            reason=reason,
        )
    except GateResolutionError as exc:
        return _error_page(exc.reason, exc.status_code)

    outcome = "approved" if result.decision == "approve" else "rejected"
    reason_line = f"<p><strong>Reason recorded:</strong> {escape(reason)}</p>" if reason else ""
    body = f"""<h1 class="ok">Decision recorded</h1>
<div class="card">
  <p>The workflow step was <strong>{escape(outcome)}</strong>.</p>
  <p><strong>Run:</strong> {escape(result.run_id)}</p>
  <p><strong>Gate:</strong> {escape(result.gate_request_id)}</p>
  {reason_line}
</div>
<p>You can close this window — the workflow continues automatically.</p>"""
    return _page("Decision recorded", body)
