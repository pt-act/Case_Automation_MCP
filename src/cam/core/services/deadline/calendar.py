"""Calendar abstraction — business-day math, versioned holiday tables — spec §4.4, G2.

No naive date arithmetic anywhere.  Every operation goes through this module.
Missing year → loud failure, never a guess (spec §6 edge case).
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Callable


class CalendarError(Exception):
    """Raised when the calendar lacks data needed for a computation."""


CALENDAR_VERSION_PREFIX = "usfed"


# ---------------------------------------------------------------------------
# US Federal holiday computation (algorithmic, not hardcoded per-year)
# ---------------------------------------------------------------------------

def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Return the nth occurrence of weekday (0=Mon..6=Sun) in the month."""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    result = first + timedelta(days=delta + (n - 1) * 7)
    if result.month != month:
        raise ValueError(f"No {n}th weekday={weekday} in {year}-{month:02d}")
    return result


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Return the last occurrence of weekday in the month."""
    next_month = date(year, month % 12 + 1, 1) if month < 12 else date(year + 1, 1, 1)
    last_day = next_month - timedelta(days=1)
    delta = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=delta)


def _observed(d: date) -> date:
    """Move a fixed holiday to the observed weekday if it falls on a weekend."""
    if d.weekday() == 5:  # Saturday → Friday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday → Monday
        return d + timedelta(days=1)
    return d


def us_federal_holidays(year: int) -> frozenset[date]:
    """Return the set of observed US federal holidays for a given year."""
    h: list[date] = [
        _observed(date(year, 1, 1)),    # New Year's Day
        _nth_weekday_of_month(year, 1, 0, 3),   # MLK Day (3rd Mon Jan)
        _nth_weekday_of_month(year, 2, 0, 3),   # Presidents Day (3rd Mon Feb)
        _last_weekday_of_month(year, 5, 0),     # Memorial Day (last Mon May)
        _observed(date(year, 6, 19)),   # Juneteenth
        _observed(date(year, 7, 4)),    # Independence Day
        _nth_weekday_of_month(year, 9, 0, 1),   # Labor Day (1st Mon Sep)
        _nth_weekday_of_month(year, 10, 0, 2),  # Columbus Day (2nd Mon Oct)
        _observed(date(year, 11, 11)),  # Veterans Day
        _nth_weekday_of_month(year, 11, 3, 4),  # Thanksgiving (4th Thu Nov)
        _observed(date(year, 12, 25)),  # Christmas Day
    ]
    return frozenset(h)


# ---------------------------------------------------------------------------
# Calendar abstraction
# ---------------------------------------------------------------------------


class Calendar:
    """Business-day calendar backed by versioned holiday tables.

    The `calendar_id` selects the holiday set.  The `calendar_version` is
    derived from the hash of the holiday data, so any change is detectable.
    """

    SUPPORTED = {"us_federal"}
    SUPPORTED_YEARS = range(2020, 2041)  # ASSUMPTION (confirm): extend as needed

    def __init__(self, calendar_id: str = "us_federal") -> None:
        if calendar_id not in self.SUPPORTED:
            raise CalendarError(f"Unknown calendar_id {calendar_id!r}.")
        self._id = calendar_id
        self._holiday_fn: Callable[[int], frozenset[date]] = us_federal_holidays

    @lru_cache(maxsize=50)
    def _holidays(self, year: int) -> frozenset[date]:
        if year not in self.SUPPORTED_YEARS:
            raise CalendarError(
                f"Holiday data for year {year} not available for calendar {self._id!r}. "
                "Extend SUPPORTED_YEARS before running computations for this year."
            )
        return self._holiday_fn(year)

    def version(self) -> str:
        """Hash of the holiday computation for a representative year set."""
        sample = sorted(self._holidays(2026))
        raw = ",".join(str(d) for d in sample)
        return CALENDAR_VERSION_PREFIX + "_" + hashlib.sha256(raw.encode()).hexdigest()[:8]

    def is_business_day(self, d: date) -> bool:
        if d.weekday() >= 5:  # Saturday or Sunday
            return False
        return d not in self._holidays(d.year)

    def next_business_day(self, d: date) -> date:
        nxt = d + timedelta(days=1)
        while not self.is_business_day(nxt):
            nxt += timedelta(days=1)
        return nxt

    def previous_business_day(self, d: date) -> date:
        prev = d - timedelta(days=1)
        while not self.is_business_day(prev):
            prev -= timedelta(days=1)
        return prev

    def add_business_days(self, d: date, n: int) -> date:
        """Add n business days (n may be negative)."""
        direction = 1 if n >= 0 else -1
        remaining = abs(n)
        current = d
        while remaining > 0:
            current += timedelta(days=direction)
            if self.is_business_day(current):
                remaining -= 1
        return current

    def adjust(
        self,
        d: date,
        mode: str,
    ) -> date:
        """Apply adjustment mode.  Returns d if already a business day and mode='none'."""
        if mode == "next_business_day":
            return d if self.is_business_day(d) else self.next_business_day(d)
        if mode == "previous_business_day":
            return d if self.is_business_day(d) else self.previous_business_day(d)
        if mode == "none":
            return d
        raise CalendarError(f"Unknown adjust mode {mode!r}.")


_DEFAULT_CALENDAR = Calendar("us_federal")


def get_calendar(calendar_id: str = "us_federal") -> Calendar:
    if calendar_id == "us_federal":
        return _DEFAULT_CALENDAR
    return Calendar(calendar_id)
