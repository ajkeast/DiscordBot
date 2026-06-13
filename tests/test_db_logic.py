"""Unit tests for streak and juice calculators in utils/db.py."""

from datetime import datetime

import pandas as pd
import pytz

from utils.db import juice_calc, streak_calc


def test_calculate_streak_consecutive_same_user():
    df = pd.DataFrame({
        "user_id": ["111", "111", "111"],
        "timesent": pd.to_datetime([
            "2024-01-01 12:00:00",
            "2024-01-02 12:00:00",
            "2024-01-03 12:00:00",
        ]),
    })
    assert streak_calc.calculate_streak(df) == 3


def test_calculate_streak_resets_on_different_user():
    df = pd.DataFrame({
        "user_id": ["111", "111", "222"],
        "timesent": pd.to_datetime([
            "2024-01-01 12:00:00",
            "2024-01-02 12:00:00",
            "2024-01-03 12:00:00",
        ]),
    })
    assert streak_calc.calculate_streak(df) == 1


def test_calculate_user_streak_longest_run():
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
    assert streak_calc.calculate_user_streak(df, "111") == 2


def test_calculate_user_streak_unknown_user():
    df = pd.DataFrame({
        "user_id": ["111"],
        "timesent": pd.to_datetime(["2024-01-01 12:00:00"]),
    })
    assert streak_calc.calculate_user_streak(df, "999") == 0


def test_juice_within_day_at_noon_est():
    # 17:00 UTC = 12:00 EST (during standard time)
    df = pd.DataFrame({
        "user_id": ["111"],
        "timesent": pd.to_datetime(["2024-01-15 17:00:00"]),
    })
    juice = juice_calc.calculate_user_juice(df, "111")
    assert juice == 12 * 60  # 720 minutes since midnight


def test_juice_missed_day_rollover():
    df = pd.DataFrame({
        "user_id": ["111", "111"],
        "timesent": pd.to_datetime([
            "2024-01-01 17:00:00",  # noon EST
            "2024-01-03 17:00:00",  # skip Jan 2 entirely
        ]),
    })
    juice = juice_calc.calculate_user_juice(df, "111")
    # Day 1: 720 min; Day 3: 720 + 1440 missed day
    assert juice == 720 + 720 + 1440


def test_calculate_juice_leaderboard(sample_first_df):
    juice_df, highscore_user, highscore_value = juice_calc.calculate_juice(sample_first_df)
    assert len(juice_df) == 2
    assert highscore_user in sample_first_df["user_id"].values
    assert highscore_value > 0


def test_daily_juice_series_columns(sample_first_df):
    series = juice_calc.daily_juice_series(sample_first_df)
    assert list(series.columns) == ["timesent", "juice"]
    assert len(series) == len(sample_first_df)
