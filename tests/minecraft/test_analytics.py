"""Tests for Minecraft playday analytics, server events, and Grok JSON payloads."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from config import EASTERN
from minecraft.analytics import summarize_day
from minecraft.models import Death, EntityDeath, ParsedLogs, Prelogin, Session


def test_summarize_day_computes_playtime_afk_peak_and_new_player():
    day = date(2026, 10, 25)
    t_start = datetime(2026, 10, 25, 19, 0, tzinfo=EASTERN)
    prior_day = datetime(2026, 10, 20, 12, 0, tzinfo=EASTERN)

    parsed = ParsedLogs(
        sessions=[
            Session("Veteran", prior_day, prior_day + timedelta(hours=1), "leave"),
            Session("Veteran", t_start, t_start + timedelta(hours=2), "leave"),
            Session("AfkKing", t_start, t_start + timedelta(hours=7), "still_online"),
            Session("Newbie", t_start + timedelta(minutes=30), t_start + timedelta(hours=2), "leave"),
        ],
        deaths=[
            Death("Veteran", t_start + timedelta(minutes=45), "was slain by Wither Skeleton"),
            Death("Newbie", t_start + timedelta(minutes=50), "fell from a high place"),
        ],
        uuids={"Veteran": "u-vet", "AfkKing": "u-afk", "Newbie": "u-new"},
    )

    recap = summarize_day(
        parsed,
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
    assert by_name["AfkKing"].new_player is False  # seen in Postgres prior_players
    assert by_name["Veteran"].new_player is False  # seen in earlier log session
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
    parsed = ParsedLogs(sessions=[Session("Regular", t0, t0 + timedelta(hours=1), "leave")])

    recap = summarize_day(parsed, day, history_known=False)
    assert recap.players[0].new_player is False


def test_server_events_use_actual_entity_name_not_hardcoded_villagers():
    day = date(2026, 10, 25)
    t0 = datetime(2026, 10, 25, 14, 0, tzinfo=EASTERN)
    parsed = ParsedLogs(
        sessions=[Session("Steve", t0, t0 + timedelta(hours=1), "leave")],
        entity_deaths=[
            EntityDeath("Cow", t0 + timedelta(minutes=i), "was squished too much", x=10.0, z=-20.0)
            for i in range(6)
        ],
        prelogins=[Prelogin("BotScanner", t0 + timedelta(minutes=i)) for i in range(5)],
    )

    recap = summarize_day(parsed, day)
    assert any("Cow was squished too much (6 times near x 10, z -20)" in n for n in recap.notable_server_events)
    assert not any("Villager" in n for n in recap.notable_server_events)
    assert any("BotScanner" in n for n in recap.notable_server_events)


def test_grok_payload_truncates_extreme_death_volume_cleanly():
    day = date(2026, 10, 25)
    t0 = datetime(2026, 10, 25, 14, 0, tzinfo=EASTERN)
    parsed = ParsedLogs(
        sessions=[Session("Steve", t0, t0 + timedelta(hours=2), "leave")],
        deaths=[Death("Steve", t0 + timedelta(seconds=i * 10), "fell from a high place") for i in range(75)],
    )
    payload = summarize_day(parsed, day).to_grok_payload()
    assert payload["summary"]["total_deaths"] == 75
    assert len(payload["deaths"]) == 60
    assert payload["deaths_truncated"] is True
    assert payload["top_death_counts"][0]["count"] == 75
