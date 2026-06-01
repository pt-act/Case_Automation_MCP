"""FastAPI webhook router — sidecar entry point for inbound vendor events.

Route: POST /webhooks/{connector}
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from cam.connectors.registry import ConnectorNotFound, get_normaliser, registered_connectors
from cam.connectors.webhook.pipeline import process_webhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/{connector_name}", status_code=202)
async def ingest_webhook(connector_name: str, request: Request) -> Response:
    """Receive, verify, normalise, deduplicate, and enqueue a vendor webhook.

    Returns:
      202  — accepted (enqueued or duplicate-ignored or unknown-type dropped)
      400  — malformed body
      401  — bad or missing signature
      404  — unknown connector
      413  — body too large
      503  — Redis unavailable (provider should retry)
    """
    from cam.connectors.webhook.pipeline import MAX_BODY_BYTES

    if connector_name not in registered_connectors():
        raise HTTPException(status_code=404, detail=f"Unknown connector: {connector_name!r}")

    # Read raw body (size cap enforced by pipeline)
    raw_body = await request.body()
    if len(raw_body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large.")

    headers = dict(request.headers)
    normaliser = get_normaliser(connector_name) or _noop_normaliser

    # Retrieve secret and clients from app state
    app_state = request.app.state
    secret: str = getattr(app_state, "webhook_secrets", {}).get(connector_name, "")
    redis_client = getattr(app_state, "redis_client", None)
    trigger_sink = getattr(app_state, "trigger_sink", _noop_trigger_sink)

    if redis_client is None:
        raise HTTPException(status_code=503, detail="Dedup store unavailable.")

    result = await process_webhook(
        connector_name=connector_name,
        raw_body=raw_body,
        headers=headers,
        secret=secret,
        normaliser=normaliser,  # caller must supply Callable[[str, dict], Event | None]
        trigger_sink=trigger_sink,
        redis_client=redis_client,
    )

    decision_to_status = {
        "enqueued": 202,
        "duplicate_ignored": 200,
        "dropped_unknown_type": 202,
        "rejected_bad_signature": 401,
        "rejected_oversized": 413,
        "redis_unavailable": 503,
        "enqueue_failed": 503,
        "rejected_bad_signature": 400,  # malformed body also maps here
    }
    status_code = decision_to_status.get(result.decision, 202)
    return Response(
        content=result.decision,
        status_code=status_code,
        media_type="text/plain",
    )


def _noop_normaliser(connector: str, raw: dict) -> None:
    return None


class _noop_trigger_sink:
    async def enqueue(self, event: object) -> str:
        return "noop"
