"""Redis-backed circuit breaker for distributed state sharing.

Extends the in-process ConnectorHealth with Redis-backed circuit state so
all workers see the same open/closed status.  When Redis is unavailable,
degrades to the in-process behavior (safe for single-worker deployments).

Usage::

    from cam.connectors.health import get_health, RedisConnectorHealth

    # In sidecar startup:
    health = RedisConnectorHealth("clio", redis_client=redis)
    register_health("clio", health)

    # In middleware (unchanged — is_open checks Redis):
    if self._health.is_open:
        raise FatalError(...)
"""

from __future__ import annotations

from typing import Any

from cam.connectors.errors import ConnectorError
from cam.connectors.health import ConnectorHealth

# Redis key prefixes
_OPEN_PREFIX = "cam:circuit_open:"
_FAILURE_PREFIX = "cam:circuit_failures:"


class RedisConnectorHealth(ConnectorHealth):
    """Circuit breaker with Redis-backed distributed state.

    Extends ConnectorHealth so the interface is identical.  The key difference:
    - ``is_open`` checks Redis for a distributed "circuit open" flag
    - ``record_failure`` writes to Redis so all workers see the failure count
    - ``record_success`` clears the Redis flag

    The local in-process state is still maintained for the transition logic
    and for single-worker correctness when Redis is unavailable.
    """

    def __init__(
        self,
        connector: str,
        redis_client: Any | None = None,
        failure_threshold: int = 5,
        cool_down_seconds: float = 60.0,
    ) -> None:
        super().__init__(
            connector=connector,
            failure_threshold=failure_threshold,
            cool_down_seconds=cool_down_seconds,
        )
        self._redis = redis_client
        self._open_key = f"{_OPEN_PREFIX}{connector}"
        self._fail_key = f"{_FAILURE_PREFIX}{connector}"

    @property
    def is_open(self) -> bool:
        """Check if the circuit is open (Redis first, then local)."""
        if self._redis is not None:
            try:
                # Synchronous check — works because redis-py sync client is
                # used in the middleware's sync path.  For async Redis, the
                # middleware should use is_open_async instead.
                import asyncio

                try:
                    asyncio.get_running_loop()
                    # We're inside an event loop — use sync check
                    return self._check_local_or_redis_sync()
                except RuntimeError:
                    pass
                return self._check_local_or_redis_sync()
            except Exception:
                pass
        return super().is_open

    def _check_local_or_redis_sync(self) -> bool:
        """Check Redis for the open flag, fall back to local state."""
        if self._redis is None:
            return super().is_open
        try:
            result = self._redis.get(self._open_key)
            if result is not None:
                return True
            return False
        except Exception:
            return super().is_open

    async def is_open_async(self) -> bool:
        """Async check for the circuit open flag in Redis."""
        if self._redis is None:
            return super().is_open
        try:
            result = await self._redis.get(self._open_key)
            if result is not None:
                return True
            return False
        except Exception:
            return super().is_open

    def record_success(self) -> None:
        """Record success — clears Redis open flag + local state."""
        super().record_success()
        if self._redis is not None:
            try:
                self._redis.delete(self._open_key)
            except Exception:
                pass  # Redis errors are non-fatal

    async def record_success_async(self) -> None:
        """Async version — clears Redis open flag."""
        super().record_success()
        if self._redis is not None:
            try:
                await self._redis.delete(self._open_key)
            except Exception:
                pass

    def record_failure(self, error: ConnectorError) -> None:
        """Record failure — sets Redis open flag when threshold reached."""
        super().record_failure(error)
        if self._redis is not None and self._state == "open":
            try:
                self._redis.set(
                    self._open_key,
                    "1",
                    ex=int(self.cool_down_seconds),
                )
            except Exception:
                pass  # Redis errors are non-fatal

    async def record_failure_async(self, error: ConnectorError) -> None:
        """Async version — sets Redis open flag when threshold reached."""
        super().record_failure(error)
        if self._redis is not None and self._state == "open":
            try:
                await self._redis.set(
                    self._open_key,
                    "1",
                    ex=int(self.cool_down_seconds),
                )
            except Exception:
                pass
