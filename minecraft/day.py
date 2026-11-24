"""Play-day window helpers (05:00–05:00 America/New_York)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from config import EASTERN

PLAY_DAY_START = time(5, 0)


def pretty_date(day: date) -> str:
    return f"{day.strftime('%A, %B')} {day.day}, {day.year}"


def _at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock.replace(tzinfo=None), tzinfo=EASTERN)


def day_window(day: date) -> tuple[datetime, datetime]:
    """``[05:00, 05:00)`` America/New_York, named by the morning it starts."""
    return _at(day, PLAY_DAY_START), _at(day + timedelta(days=1), PLAY_DAY_START)


def playday_date(moment: datetime) -> date:
    """The play day containing ``moment`` (04:59 belongs to the previous day)."""
    local = moment.astimezone(EASTERN)
    opened = _at(local.date(), PLAY_DAY_START)
    if local < opened:
        return local.date() - timedelta(days=1)
    return local.date()


def activity_date_due(now: datetime) -> date | None:
    """Return the finished play day due at/after 08:00 America/New_York, else None."""
    local = now.astimezone(EASTERN)
    if (local.hour, local.minute) < (8, 0):
        return None
    return local.date() - timedelta(days=1)


def yesterday_et(now: datetime) -> date:
    return now.astimezone(EASTERN).date() - timedelta(days=1)
