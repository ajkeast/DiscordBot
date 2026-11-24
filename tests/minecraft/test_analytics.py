"""Tests for Minecraft playday analytics and Grok JSON payloads."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from config import EASTERN
from minecraft.analytics import summarize_day
from minecraft.models import Death, Session


def test_summarize_day_computes_playtime_afk_peak_and_new_player():
    day = date(2026, 10, 25)
    t_start = datetime(2026, 10, 25, 19, 0, tzinfo=EASTERN)
    prior_day = datetime(2026, 10, 20, 12, 0, tzinfo=EASTERN)

    sessions = [
        Session("Veteran", prior_day, prior_day + timedelta(hours=1), "leave", "u-vet"),
        Session("Veteran", t_start, t_start + timedelta(hours=2), "leave", "u-vet"),
        Session("AfkKing", t_start, t_start + timedelta(hours=7), "still_online", "u-afk"),
        Session(
            "Newbie",
            t_start + timedelta(minutes=30),
            t_start + timedelta(hours=2),
            "leave",
            "u-new",
        ),
    ]
    deaths = [
        Death("Veteran", t_start + timedelta(minutes=45), "was slain by Wither Skeleton"),
        Death("Newbie", t_start + timedelta(minutes=50), "fell from a high place"),
    ]

    recap = summarize_day(
        sessions,
        deaths,
        day,
        as_of=datetime(2026, 10, 26, 8, 0, tzinfo=EASTERN),
        prior_players=({"AfkKing"}, set()),
        history_known=True,
    )

    assert not recap.nobody_played
    assert recap.partial_day is False
    by_name = {p.name: p for p in recap.players}

    assert by_name["AfkKing"].likely_afk is True
    assert by_name["AfkKing"].still_online is True
    assert by_name["AfkKing"].new_player is False
    assert by_name["Veteran"].new_player is False
    assert by_name["Newbie"].new_player is True

    assert recap.peak_stretch is not None
    assert recap.peak_stretch.peak_players == 3
    assert set(recap.peak_stretch.players) == {"Veteran", "AfkKing", "Newbie"}

    payload = recap.to_grok_payload()
    assert payload["summary"]["total_deaths"] == 2
    assert len(payload["deaths"]) == 2
    assert payload["deaths"][0]["message"] == "was slain by Wither Skeleton"
    assert "advancements" not in payload


def test_history_unknown_never_falsely_welcomes_players():
    day = date(2026, 10, 25)
    t0 = datetime(2026, 10, 25, 12, 0, tzinfo=EASTERN)
    sessions = [Session("Regular", t0, t0 + timedelta(hours=1), "leave")]

    recap = summarize_day(sessions, [], day, history_known=False)
    assert recap.players[0].new_player is False


def test_pvp_deaths_flagged_in_grok_payload():
    day = date(2026, 10, 25)
    t0 = datetime(2026, 10, 25, 14, 0, tzinfo=EASTERN)
    sessions = [Session("Steve", t0, t0 + timedelta(hours=1), "leave")]
    deaths = [
        Death("Steve", t0 + timedelta(minutes=10), "was slain by Alex", killer_player="Alex"),
        Death("Steve", t0 + timedelta(minutes=20), "fell from a high place"),
    ]
    payload = summarize_day(sessions, deaths, day).to_grok_payload()
    assert payload["deaths"][0]["pvp"] is True
    assert payload["deaths"][0]["killer"] == "Alex"
    assert "pvp" not in payload["deaths"][1]


def test_grok_payload_truncates_extreme_death_volume_cleanly():
    day = date(2026, 10, 25)
    t0 = datetime(2026, 10, 25, 14, 0, tzinfo=EASTERN)
    sessions = [Session("Steve", t0, t0 + timedelta(hours=2), "leave")]
    deaths = [
        Death("Steve", t0 + timedelta(seconds=i * 10), "fell from a high place")
        for i in range(75)
    ]
    payload = summarize_day(sessions, deaths, day).to_grok_payload()
    assert payload["summary"]["total_deaths"] == 75
    assert len(payload["deaths"]) == 60
    assert payload["deaths_truncated"] is True
    assert payload["top_death_counts"][0]["count"] == 75
