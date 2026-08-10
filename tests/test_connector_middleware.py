"""G3 — OutboundClient middleware focused tests (no live HTTP)."""

from __future__ import annotations

import httpx
import pytest
import respx

from cam.connectors.errors import AuthError, FatalError, TransientError
from cam.connectors.health import reset_health
from cam.connectors.middleware import OutboundClient, RetryPolicy


@pytest.fixture(autouse=True)
def _reset_circuit() -> None:
    reset_health("test_mw")


def make_client(redis=None) -> OutboundClient:  # type: ignore[return]
    http = httpx.AsyncClient()
    return OutboundClient(
        "test_mw",
        http,
        redis_client=redis,
        failure_threshold=5,
        cool_down_seconds=60,
    )


# a. Transient retried then succeeds
@pytest.mark.asyncio
async def test_transient_retried_then_succeeds() -> None:
    with respx.mock(assert_all_called=False) as rx:
        rx.get("https://api.test/resource").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, json={"ok": True}),
            ]
        )
        client = make_client()
        retry = RetryPolicy(max_attempts=3, base_delay=0.001, max_delay=0.01, total_deadline=5)
        resp = await client.request(
            "GET", "https://api.test/resource", retry=retry
        )
        assert resp.status_code == 200


# b. Retry exhaustion → FatalError
@pytest.mark.asyncio
async def test_retry_exhaustion_raises_fatal() -> None:
    with respx.mock:
        respx.get("https://api.test/always-fail").mock(
            side_effect=[httpx.Response(503)] * 10
        )
        client = make_client()
        retry = RetryPolicy(max_attempts=2, base_delay=0.001, max_delay=0.01, total_deadline=5)
        with pytest.raises(FatalError):
            await client.request("GET", "https://api.test/always-fail", retry=retry)


# c. AuthError is NOT retried
@pytest.mark.asyncio
async def test_auth_error_not_retried() -> None:
    with respx.mock:
        route = respx.get("https://api.test/auth-fail").mock(
            side_effect=[httpx.Response(401), httpx.Response(200)]
        )
        client = make_client()
        retry = RetryPolicy(max_attempts=3, base_delay=0.001, total_deadline=5)
        with pytest.raises(AuthError):
            await client.request("GET", "https://api.test/auth-fail", retry=retry)
        # Should have been called only once (no retry on AuthError)
        assert route.call_count == 1


# d. Open circuit fails fast (no HTTP call made)
@pytest.mark.asyncio
async def test_open_circuit_fails_fast() -> None:
    from cam.connectors.health import get_health

    health = get_health("test_mw", failure_threshold=1)
    health.record_failure(TransientError("test_mw", "forced"))

    with respx.mock(assert_all_called=False) as rx:
        rx.get("https://api.test/anything").mock(return_value=httpx.Response(200))
        client = make_client()
        with pytest.raises((FatalError, TransientError)):
            await client.request("GET", "https://api.test/anything")
        # The HTTP route must not have been called
        assert rx.calls.call_count == 0


# e. Successful request records health
@pytest.mark.asyncio
async def test_success_records_healthy() -> None:
    with respx.mock:
        respx.get("https://api.test/ok").mock(return_value=httpx.Response(200, json={}))
        client = make_client()
        from cam.connectors.health import get_health

        h = get_health("test_mw")
        h.record_failure(TransientError("test_mw", "one"))  # degrade but not trip
        await client.request(
            "GET", "https://api.test/ok",
            retry=RetryPolicy(max_attempts=3, base_delay=0.001, total_deadline=5)
        )
        assert h.state == "healthy"
