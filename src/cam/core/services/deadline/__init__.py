"""Deadline engine — liability-critical; never silent-fail."""

from cam.core.services.deadline.calendar import Calendar, CalendarError, get_calendar
from cam.core.services.deadline.compute import (
    MissingTriggerError,
    compute_due_date,
    preview_reminders,
    tool_deadline_compute,
)
from cam.core.services.deadline.deadman import DeadmanMonitor
from cam.core.services.deadline.firing import (
    MockNotificationPort,
    NotificationPort,
    escalate,
    fire_reminder,
    mark_done,
)
from cam.core.services.deadline.reconcile import Reconciler
from cam.core.services.deadline.resources import resource_calendar_upcoming, resource_deadline_rules
from cam.core.services.deadline.rules import RuleStore, RuleValidationError
from cam.core.services.deadline.schedule import (
    DeadlineStore,
    InMemoryScheduler,
    PastDueOnCreateError,
    Scheduler,
    tool_deadline_schedule,
)
from cam.core.services.deadline.types import (
    ComputationTrace,
    DeadlineRule,
    Drift,
    EscalationPolicy,
    Offset,
    ReminderOffset,
    RuleRef,
    ScheduledDeadline,
    ScheduledReminder,
)

__all__ = [
    "Calendar", "CalendarError", "ComputationTrace", "DeadlineRule", "DeadlineStore",
    "DeadmanMonitor", "Drift", "EscalationPolicy", "InMemoryScheduler",
    "MissingTriggerError", "MockNotificationPort", "NotificationPort", "Offset",
    "PastDueOnCreateError", "Reconciler", "ReminderOffset", "RuleRef", "RuleStore",
    "RuleValidationError", "ScheduledDeadline", "ScheduledReminder", "Scheduler",
    "compute_due_date", "escalate", "fire_reminder", "get_calendar", "mark_done",
    "preview_reminders", "resource_calendar_upcoming", "resource_deadline_rules",
    "tool_deadline_compute", "tool_deadline_schedule",
]
