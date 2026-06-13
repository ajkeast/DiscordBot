"""Unit tests for streak and juice calculators in utils/db.py."""

import pandas as pd

from tests.reporting import SECTION_UNIT, assert_eq
from utils.db import juice_calc, streak_calc


def test_calculate_streak_consecutive_same_user(report):
    df = pd.DataFrame({
        "user_id": ["111", "111", "111"],
        "timesent": pd.to_datetime([
            "2024-01-01 12:00:00",
            "2024-01-02 12:00:00",
            "2024-01-03 12:00:00",
        ]),
    })
    assert_eq(report, SECTION_UNIT, "current streak", 3, streak_calc.calculate_streak(df))


def test_calculate_streak_resets_on_different_user(report):
    df = pd.DataFrame({
        "user_id": ["111", "111", "222"],
        "timesent": pd.to_datetime([
            "2024-01-01 12:00:00",
            "2024-01-02 12:00:00",
            "2024-01-03 12:00:00",
        ]),
    })
    assert_eq(report, SECTION_UNIT, "current streak", 1, streak_calc.calculate_streak(df))


def test_calculate_user_streak_longest_run(report):
    df = pd.DataFrame({
        "user_id": ["111", "111", "222", "111", "111"],
        "timesent": pd.to_datetime([
            "2024-01-01 12:00:00",
            "2024-01-02 12:00:00",
            "2024-01-03 12:00:00",
            "2024-01-04 12:00:00",
            "2024-01-05 12:00:00",
        ]),
    })
    assert_eq(
        report,
        SECTION_UNIT,
        "longest streak for user 111",
        2,
        streak_calc.calculate_user_streak(df, "111"),
    )


def test_calculate_user_streak_unknown_user(report):
    df = pd.DataFrame({
        "user_id": ["111"],
        "timesent": pd.to_datetime(["2024-01-01 12:00:00"]),
    })
    assert_eq(
        report,
        SECTION_UNIT,
        "streak for unknown user",
        0,
        streak_calc.calculate_user_streak(df, "999"),
    )


def test_juice_within_day_at_noon_est(report):
    df = pd.DataFrame({
        "user_id": ["111"],
        "timesent": pd.to_datetime(["2024-01-15 17:00:00"]),
    })
    assert_eq(
        report,
        SECTION_UNIT,
        "juice at noon EST (minutes)",
        720,
        juice_calc.calculate_user_juice(df, "111"),
    )


def test_juice_missed_day_rollover(report):
    df = pd.DataFrame({
        "user_id": ["111", "111"],
        "timesent": pd.to_datetime([
            "2024-01-01 17:00:00",
            "2024-01-03 17:00:00",
        ]),
    })
    assert_eq(
        report,
        SECTION_UNIT,
        "juice with missed day rollover",
        2880,
        juice_calc.calculate_user_juice(df, "111"),
    )


def test_calculate_juice_leaderboard(report, sample_first_df):
    juice_df, highscore_user, highscore_value = juice_calc.calculate_juice(sample_first_df)
    report.record("leaderboard user count", 2, len(juice_df), section=SECTION_UNIT)
    report.record(
        "highscore user in dataset",
        True,
        highscore_user in sample_first_df["user_id"].values,
        section=SECTION_UNIT,
    )
    report.record("highscore value > 0", True, highscore_value > 0, section=SECTION_UNIT)
    assert len(juice_df) == 2
    assert highscore_user in sample_first_df["user_id"].values
    assert highscore_value > 0


def test_daily_juice_series_columns(report, sample_first_df):
    series = juice_calc.daily_juice_series(sample_first_df)
    report.record("columns", ["timesent", "juice"], list(series.columns), section=SECTION_UNIT)
    report.record("row count", len(sample_first_df), len(series), section=SECTION_UNIT)
    assert list(series.columns) == ["timesent", "juice"]
    assert len(series) == len(sample_first_df)
