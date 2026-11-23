"""Tests for pure-Python first-game streak/juice analytics and thread-safe charts."""

from __future__ import annotations

from datetime import datetime

from db.first_game import FirstEntry
from services.charts import render_firsts_chart, render_juice_chart
from services.first_game import (
    calculate_current_streak,
    calculate_juice_board,
    calculate_user_juice,
    calculate_user_longest_streak,
    compute_daily_juice,
)
from tests.reporting import SECTION_UNIT, assert_eq


def test_streaks(report):
    entries = [
        FirstEntry("111", datetime(2024, 1, 1, 12, 0, 0)),
        FirstEntry("111", datetime(2024, 1, 2, 12, 0, 0)),
        FirstEntry("222", datetime(2024, 1, 3, 12, 0, 0)),
        FirstEntry("111", datetime(2024, 1, 4, 12, 0, 0)),
        FirstEntry("111", datetime(2024, 1, 5, 12, 0, 0)),
        FirstEntry("111", datetime(2024, 1, 6, 12, 0, 0)),
    ]
    assert_eq(report, SECTION_UNIT, "current streak", 3, calculate_current_streak(entries))
    assert_eq(report, SECTION_UNIT, "longest streak 111", 3, calculate_user_longest_streak(entries, "111"))
    assert_eq(report, SECTION_UNIT, "unknown user streak", 0, calculate_user_longest_streak(entries, "999"))


def test_juice_within_day_and_missed_day_rollover(report):
    noon_est = [FirstEntry("111", datetime(2024, 1, 15, 17, 0, 0))]
    assert_eq(report, SECTION_UNIT, "juice at noon EST", 720.0, calculate_user_juice(noon_est, "111"))

    with_gap = [
        FirstEntry("111", datetime(2024, 1, 1, 17, 0, 0)),
        FirstEntry("111", datetime(2024, 1, 3, 17, 0, 0)),
    ]
    assert_eq(report, SECTION_UNIT, "juice with missed day", 2880.0, calculate_user_juice(with_gap, "111"))


def test_juice_board_and_charts(sample_first_entries):
    rankings, high_uid, high_val = calculate_juice_board(sample_first_entries)
    assert len(rankings) == 2
    assert high_uid in {"111", "222"}
    assert high_val > 0

    first_png = render_firsts_chart(sample_first_entries, lambda uid: f"User {uid}")
    juice_png = render_juice_chart(compute_daily_juice(sample_first_entries))
    assert first_png.startswith(b"\x89PNG")
    assert juice_png.startswith(b"\x89PNG")
