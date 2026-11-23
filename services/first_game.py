"""Pure-Python streak and juice analytics for the daily /1st game."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from config import EASTERN
from db.first_game import FirstEntry


@dataclass(frozen=True)
class JuicePoint:
    user_id: str
    timesent_eastern: datetime
    juice: float


@dataclass(frozen=True)
class JuiceRanking:
    user_id: str
    total_juice: float


def _as_eastern(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(EASTERN)


def _sorted_entries(entries: Sequence[FirstEntry]) -> list[FirstEntry]:
    return sorted(entries, key=lambda e: _as_eastern(e.timesent))


def leaderboard_counts(entries: Sequence[FirstEntry]) -> list[tuple[str, int]]:
    """Return ``[(user_id, wins), ...]`` sorted highest wins first."""
    counts = Counter(e.user_id for e in entries)
    return counts.most_common()


def calculate_current_streak(entries: Sequence[FirstEntry]) -> int:
    """Return the consecutive win streak of the most recent winner."""
    ordered = _sorted_entries(entries)
    if not ordered:
        return 0
    latest_user = ordered[-1].user_id
    streak = 0
    for entry in reversed(ordered):
        if entry.user_id != latest_user:
            break
        streak += 1
    return streak


def calculate_user_longest_streak(
    entries: Sequence[FirstEntry], user_id: str | int
) -> int:
    """Return the longest consecutive win streak ever achieved by ``user_id``."""
    target = str(user_id)
    ordered = _sorted_entries(entries)
    longest = 0
    current = 0
    for entry in ordered:
        if entry.user_id == target:
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
    return longest


def compute_daily_juice(entries: Sequence[FirstEntry]) -> list[JuicePoint]:
    """Compute Eastern timestamps and per-claim juice minutes in chronological order.

    Juice = minutes past midnight Eastern + 1440 per full missed day since the
    previous claim. Higher juice is better.
    """
    ordered = _sorted_entries(entries)
    points: list[JuicePoint] = []
    prev_date = None

    for entry in ordered:
        eastern_dt = _as_eastern(entry.timesent)
        within_day = (
            eastern_dt.hour * 60
            + eastern_dt.minute
            + eastern_dt.second / 60.0
        )
        missed_days = 0
        if prev_date is not None:
            day_gap = (eastern_dt.date() - prev_date).days
            missed_days = max(0, day_gap - 1)
        prev_date = eastern_dt.date()
        points.append(
            JuicePoint(
                user_id=entry.user_id,
                timesent_eastern=eastern_dt,
                juice=within_day + missed_days * 1440.0,
            )
        )
    return points


def calculate_juice_board(
    entries: Sequence[FirstEntry],
) -> tuple[list[JuiceRanking], str, float]:
    """Return ``(rankings, single_day_highscore_user_id, single_day_highscore_value)``."""
    points = compute_daily_juice(entries)
    if not points:
        return [], "", 0.0

    best_point = max(points, key=lambda p: p.juice)
    totals: dict[str, float] = {}
    for point in points:
        totals[point.user_id] = totals.get(point.user_id, 0.0) + point.juice

    rankings = [
        JuiceRanking(user_id=uid, total_juice=total)
        for uid, total in sorted(totals.items(), key=lambda pair: -pair[1])
    ]
    return rankings, best_point.user_id, best_point.juice


def calculate_user_juice(entries: Sequence[FirstEntry], user_id: str | int) -> float:
    target = str(user_id)
    return sum(p.juice for p in compute_daily_juice(entries) if p.user_id == target)
