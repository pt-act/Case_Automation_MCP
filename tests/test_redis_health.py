"""RedisConnectorHealth tests — distributed circuit breaker (mocked Redis)."""

from __future__ import annotations

from unittest import mock

from cam.connectors.errors import TransientError
from cam.connectors.health import ConnectorHealth
from cam.connectors.redis_health import RedisConnectorHealth


def _make_error(connector: str = "test") -> TransientError:
    return TransientError(connector, "test failure")


# -- Basic inheritance + local behavior ------------------------------------

def test_redis_health_extends_connector_health() -> None:
    """RedisConnectorHealth is a ConnectorHealth subclass."""
    h = RedisConnectorHealth("test", redis_client=None)
    assert isinstance(h, ConnectorHealth)
    assert h.connector == "test"
    assert h.state == "healthy"


def test_redis_health_no_redis_acts_like_parent() -> None:
    """Without Redis, behaves identically to ConnectorHealth."""
    h = RedisConnectorHealth("test", redis_client=None)
    assert not h.is_open

    for _ in range(5):
        h.record_failure(_make_error())
    assert h.is_open


# -- Redis-backed is_open --------------------------------------------------

def test_is_open_checks_redis() -> None:
    """is_open returns True when Redis has the open flag."""
    redis = mock.MagicMock()
    redis.get = mock.MagicMock(return_value=b"1")
    h = RedisConnectorHealth("test", redis_client=redis)
    assert h.is_open is True


def test_is_open_redis_no_flag() -> None:
    """is_open returns False when Redis has no open flag."""
    redis = mock.MagicMock()
    redis.get = mock.MagicMock(return_value=None)
    h = RedisConnectorHealth("test", redis_client=redis)
    assert h.is_open is False


# -- record_failure sets Redis flag ----------------------------------------

def test_record_failure_sets_redis_flag_on_open() -> None:
    """When threshold reached, Redis open flag is set with TTL."""
    redis = mock.MagicMock()
    redis.get = mock.MagicMock(return_value=None)
    redis.set = mock.MagicMock()
    h = RedisConnectorHealth("test", redis_client=redis, failure_threshold=3, cool_down_seconds=60)

    for _ in range(3):
        h.record_failure(_make_error())

    # Redis set should have been called with the open key
    redis.set.assert_called_once()
    args, kwargs = redis.set.call_args
    assert args[0] == "cam:circuit_open:test"
    assert kwargs.get("ex") == 60


def test_record_failure_does_not_set_redis_before_threshold() -> None:
    """Before threshold, Redis flag is NOT set."""
    redis = mock.MagicMock()
    redis.get = mock.MagicMock(return_value=None)
    redis.set = mock.MagicMock()
    h = RedisConnectorHealth("test", redis_client=redis, failure_threshold=5)

    h.record_failure(_make_error())  # 1 failure, below threshold
    redis.set.assert_not_called()


# -- record_success clears Redis flag --------------------------------------

def test_record_success_clears_redis_flag() -> None:
    """record_success deletes the Redis open flag."""
    redis = mock.MagicMock()
    redis.delete = mock.MagicMock()
    h = RedisConnectorHealth("test", redis_client=redis)

    h.record_success()
    redis.delete.assert_called_once_with("cam:circuit_open:test")


# -- Redis error degradation -----------------------------------------------

def test_is_open_redis_error_falls_back_to_local() -> None:
    """Redis error on is_open → falls back to local state."""
    redis = mock.MagicMock()
    redis.get = mock.MagicMock(side_effect=ConnectionError("redis down"))
    h = RedisConnectorHealth("test", redis_client=redis, failure_threshold=3)

    # Open the circuit locally
    for _ in range(3):
        h.record_failure(_make_error())

    # is_open should fall back to local state (which is open)
    assert h.is_open is True


def test_record_success_redis_error_does_not_raise() -> None:
    """Redis error on record_success → silent (local state still updated)."""
    redis = mock.MagicMock()
    redis.delete = mock.MagicMock(side_effect=ConnectionError("redis down"))
    h = RedisConnectorHealth("test", redis_client=redis)

    h.record_success()  # should not raise


def test_record_failure_redis_error_does_not_raise() -> None:
    """Redis error on record_failure → silent (local state still updated)."""
    redis = mock.MagicMock()
    redis.get = mock.MagicMock(return_value=None)
    redis.set = mock.MagicMock(side_effect=ConnectionError("redis down"))
    h = RedisConnectorHealth("test", redis_client=redis, failure_threshold=2)

    for _ in range(2):
        h.record_failure(_make_error())  # should not raise

    # Local state should still be open
    assert h._state == "open"


# -- Async methods ---------------------------------------------------------

async def test_is_open_async_checks_redis() -> None:
    """is_open_async checks Redis."""
    redis = mock.AsyncMock()
    redis.get = mock.AsyncMock(return_value=b"1")
    h = RedisConnectorHealth("test", redis_client=redis)
    assert await h.is_open_async() is True


async def test_is_open_async_no_redis() -> None:
    """is_open_async without Redis → local state."""
    h = RedisConnectorHealth("test", redis_client=None, failure_threshold=2)
    for _ in range(2):
        h.record_failure(_make_error())
    assert await h.is_open_async() is True


async def test_record_success_async_clears_redis() -> None:
    """record_success_async clears Redis flag."""
    redis = mock.AsyncMock()
    redis.delete = mock.AsyncMock()
    h = RedisConnectorHealth("test", redis_client=redis)
    await h.record_success_async()
    redis.delete.assert_called_once_with("cam:circuit_open:test")


async def test_record_failure_async_sets_redis() -> None:
    """record_failure_async sets Redis flag on open."""
    redis = mock.AsyncMock()
    redis.set = mock.AsyncMock()
    h = RedisConnectorHealth("test", redis_client=redis, failure_threshold=2, cool_down_seconds=30)

    for _ in range(2):
        await h.record_failure_async(_make_error())

    redis.set.assert_called_once()
    args, kwargs = redis.set.call_args
    assert args[0] == "cam:circuit_open:test"
    assert kwargs.get("ex") == 30
