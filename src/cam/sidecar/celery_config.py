"""Celery configuration for the CAM deadline engine sidecar — spec §5, G4–G7.

All values are read from environment variables; no hard-coded secrets.
Broker and result backend are both Redis, sourced from ``CAM_REDIS_URL``.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Connection URLs
# ---------------------------------------------------------------------------

#: Redis URL used for both broker and result backend.
#: Override via ``CAM_REDIS_URL`` environment variable.
REDIS_URL: str = os.environ.get("CAM_REDIS_URL", "redis://localhost:6379/0")

# ---------------------------------------------------------------------------
# Celery application settings (assigned to the Celery app in celery_app.py)
# ---------------------------------------------------------------------------

broker_url: str = REDIS_URL
result_backend: str = REDIS_URL

# Serialization — JSON only; never pickle (security)
task_serializer: str = "json"
result_serializer: str = "json"
accept_content: list[str] = ["json"]

# Timezone
timezone: str = "UTC"
enable_utc: bool = True

# Result expiry — keep results 24 h for debugging; reduce if memory is tight
result_expires: int = 86_400  # seconds

# Task time limits
task_soft_time_limit: int = int(os.environ.get("CAM_TASK_SOFT_TIMEOUT", "55"))   # seconds
task_time_limit: int = int(os.environ.get("CAM_TASK_HARD_TIMEOUT", "120"))        # seconds

# Retry / acks
task_acks_late: bool = True          # ack only after completion (safer for idempotent tasks)
task_reject_on_worker_lost: bool = True
worker_prefetch_multiplier: int = 1  # one task at a time per worker process

# ---------------------------------------------------------------------------
# Beat schedule  (periodic tasks)
# ---------------------------------------------------------------------------

#: How often (seconds) the dead-man check fires.
DEADMAN_CHECK_INTERVAL: int = int(os.environ.get("CAM_DEADMAN_INTERVAL", "60"))

#: How often (seconds) the sweep-reconcile task fires.
SWEEP_RECONCILE_INTERVAL: int = int(os.environ.get("CAM_SWEEP_RECONCILE_INTERVAL", "300"))

beat_schedule: dict = {
    "cam.deadline.deadman_check-periodic": {
        "task": "cam.deadline.deadman_check",
        "schedule": DEADMAN_CHECK_INTERVAL,
        "options": {"expires": DEADMAN_CHECK_INTERVAL - 5},
    },
    "cam.deadline.sweep_reconcile-periodic": {
        "task": "cam.deadline.sweep_reconcile",
        "schedule": SWEEP_RECONCILE_INTERVAL,
        # matter_ids is intentionally empty here; production bootstrap injects it
        # via beat_schedule override or a dedicated matter-list Redis key.
        "args": ([],),
        "options": {"expires": SWEEP_RECONCILE_INTERVAL - 5},
    },
}

# ---------------------------------------------------------------------------
# Routing (optional, kept for clarity)
# ---------------------------------------------------------------------------

task_routes: dict = {
    "cam.deadline.*": {"queue": "cam_deadline"},
}
