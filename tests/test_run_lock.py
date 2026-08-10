"""RunLock tests — distributed run locking via Redis SETNX (mocked)."""

from __future__ import annotations

from unittest import mock

from cam.core.orchestrator.locking import RunLock

# -- No Redis (single-worker mode) -----------------------------------------

async def test_acquire_no_redis_returns_true() -> None:
    """Without Redis, acquire always succeeds (no-op)."""
    lock = RunLock(redis_client=None)
    assert await lock.acquire("run-1") is True


async def test_release_no_redis_is_noop() -> None:
    """Without Redis, release does nothing."""
    lock = RunLock(redis_client=None)
    await lock.release("run-1")  # should not raise


async def test_extend_no_redis_is_noop() -> None:
    """Without Redis, extend does nothing."""
    lock = RunLock(redis_client=None)
    await lock.extend("run-1")  # should not raise


# -- With Redis (mocked) ---------------------------------------------------

def _make_redis(set_result=True, expire_result=True, delete_result=1) -> mock.AsyncMock:
    """Create a mock async Redis client."""
    r = mock.AsyncMock()
    r.set = mock.AsyncMock(return_value=set_result)
    r.delete = mock.AsyncMock(return_value=delete_result)
    r.expire = mock.AsyncMock(return_value=expire_result)
    return r


async def test_acquire_redis_success() -> None:
    """Redis SETNX succeeds → lock acquired."""
    redis = _make_redis(set_result=True)
    lock = RunLock(redis_client=redis, ttl_seconds=60)
    assert await lock.acquire("run-1") is True
    redis.set.assert_called_once_with("cam:run_lock:run-1", "1", nx=True, ex=60)


async def test_acquire_redis_already_locked() -> None:
    """Redis SETNX returns None → lock already held by another worker."""
    redis = _make_redis(set_result=None)
    lock = RunLock(redis_client=redis)
    assert await lock.acquire("run-1") is False


async def test_release_redis() -> None:
    """Release deletes the lock key."""
    redis = _make_redis()
    lock = RunLock(redis_client=redis)
    await lock.release("run-1")
    redis.delete.assert_called_once_with("cam:run_lock:run-1")


async def test_extend_redis() -> None:
    """Extend resets the TTL."""
    redis = _make_redis()
    lock = RunLock(redis_client=redis, ttl_seconds=120)
    await lock.extend("run-1")
    redis.expire.assert_called_once_with("cam:run_lock:run-1", 120)


async def test_acquire_redis_error_degrades_to_true() -> None:
    """Redis error → degrade to no-op (returns True)."""
    redis = mock.AsyncMock()
    redis.set = mock.AsyncMock(side_effect=ConnectionError("redis down"))
    lock = RunLock(redis_client=redis)
    assert await lock.acquire("run-1") is True


async def test_release_redis_error_does_not_raise() -> None:
    """Redis error on release → logged, not raised."""
    redis = mock.AsyncMock()
    redis.delete = mock.AsyncMock(side_effect=ConnectionError("redis down"))
    lock = RunLock(redis_client=redis)
    await lock.release("run-1")  # should not raise


async def test_extend_redis_error_does_not_raise() -> None:
    """Redis error on extend → logged, not raised."""
    redis = mock.AsyncMock()
    redis.expire = mock.AsyncMock(side_effect=ConnectionError("redis down"))
    lock = RunLock(redis_client=redis)
    await lock.extend("run-1")  # should not raise


# -- Engine integration ----------------------------------------------------

async def test_engine_execute_with_lock() -> None:
    """Engine acquires lock before execute, releases after."""
    from datetime import UTC, datetime

    from cam.core.orchestrator.dsl import StepContext, clear_registry, workflow
    from cam.core.orchestrator.engine import WorkflowEngine
    from cam.core.orchestrator.locking import RunLock
    from cam.core.orchestrator.states import (
        RunStatus,
        StepState,
        TriggerRef,
        WorkflowRun,
    )
    from cam.core.orchestrator.store import InMemoryRunStore

    clear_registry()

    @workflow("lock_test", version=1)
    class LockTestWorkflow:
        steps = ["step_a"]

        async def step_a(self, ctx: StepContext) -> dict:
            return {"done": True}

    store = InMemoryRunStore()
    redis = _make_redis(set_result=True)
    lock = RunLock(redis_client=redis)
    engine = WorkflowEngine(store=store, run_lock=lock)

    now = datetime.now(tz=UTC)
    run = WorkflowRun(
        id="run-lock-1",
        workflow="lock_test",
        workflow_version=1,
        trigger=TriggerRef(kind="agent", source="test"),
        created_at=now,
        updated_at=now,
    )
    steps = [StepState(run_id="run-lock-1", step="step_a", seq=0, idem_key="run-lock-1:step_a")]
    await store.create_run(run, steps)

    result = await engine.execute("run-lock-1")
    assert result.status == RunStatus.SUCCEEDED

    # Lock was acquired and released
    redis.set.assert_called_once()
    redis.delete.assert_called_once_with("cam:run_lock:run-lock-1")


async def test_engine_execute_skips_when_locked() -> None:
    """Engine returns current run state when lock is held by another worker."""
    from datetime import UTC, datetime

    from cam.core.orchestrator.engine import WorkflowEngine
    from cam.core.orchestrator.locking import RunLock
    from cam.core.orchestrator.states import (
        RunStatus,
        TriggerRef,
        WorkflowRun,
    )
    from cam.core.orchestrator.store import InMemoryRunStore

    store = InMemoryRunStore()
    redis = _make_redis(set_result=None)  # lock already held
    lock = RunLock(redis_client=redis)
    engine = WorkflowEngine(store=store, run_lock=lock)

    now = datetime.now(tz=UTC)
    run = WorkflowRun(
        id="run-locked-1",
        workflow="test_wf",
        workflow_version=1,
        status=RunStatus.RUNNING,
        trigger=TriggerRef(kind="agent", source="test"),
        created_at=now,
        updated_at=now,
    )
    await store.create_run(run, [])

    result = await engine.execute("run-locked-1")
    assert result.id == "run-locked-1"
    assert result.status == RunStatus.RUNNING  # unchanged — another worker has it

    # Lock was NOT released (we never acquired it)
    redis.delete.assert_not_called()
