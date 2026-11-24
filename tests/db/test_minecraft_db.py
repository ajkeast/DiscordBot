"""Unit tests for MinecraftRepository collector reads and post claims."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from unittest.mock import MagicMock

from config import EASTERN
from db.minecraft import MinecraftRepository, _death_message, _normalize_end_reason
from minecraft.day import day_window, pretty_date
from minecraft.models import DayRecap, Death, SessionSlice


def _mock_pool(cursor: MagicMock) -> MagicMock:
    pool = MagicMock()

    @contextmanager
    def _tx():
        yield cursor

    pool.transaction.side_effect = _tx
    return pool


def test_claim_daily_post_states():
    day = date(2026, 10, 25)

    cur = MagicMock()
    cur.fetchone.return_value = None
    repo = MinecraftRepository(_mock_pool(cur))
    assert repo.claim_daily_post(day, 999) == "claimed"

    cur = MagicMock()
    cur.fetchone.return_value = ("posted",)
    repo = MinecraftRepository(_mock_pool(cur))
    assert repo.claim_daily_post(day, 999) == "already_posted"

    cur = MagicMock()
    cur.fetchone.return_value = ("pending",)
    cur.rowcount = 1
    repo = MinecraftRepository(_mock_pool(cur))
    assert repo.claim_daily_post(day, 999) == "claimed"


def test_persist_day_writes_only_session_slices():
    day = date(2026, 10, 25)
    ws, we = day_window(day)
    t1 = datetime(2026, 10, 25, 10, 0, tzinfo=EASTERN)
    t2 = datetime(2026, 10, 25, 11, 0, tzinfo=EASTERN)

    dup_slice = SessionSlice("Steve", "u1", t1, t2, t1, t2, 3600, 3600, "leave", False)
    recap = DayRecap(
        day=day,
        pretty_date=pretty_date(day),
        window_start=ws,
        window_end=we,
        partial_day=False,
        players=[],
        slices=[dup_slice, dup_slice],
        deaths=[Death("Steve", t1, "fell from a high place")],
        player_uuids={"Steve": "u1"},
    )

    cur = MagicMock()
    pool = _mock_pool(cur)
    repo = MinecraftRepository(pool)
    repo.persist_day(recap)

    assert pool.transaction.call_count == 1
    assert cur.execute.call_count == 1  # delete sessions only
    assert cur.executemany.call_count == 1
    assert len(cur.executemany.call_args.args[1]) == 1


def test_fetch_sessions_maps_open_and_disconnect_reasons():
    pool = MagicMock()
    ws = datetime(2026, 10, 25, 5, 0, tzinfo=EASTERN)
    we = datetime(2026, 10, 26, 5, 0, tzinfo=EASTERN)
    joined = datetime(2026, 10, 25, 12, 0, tzinfo=EASTERN)
    pool.fetchall.return_value = [
        ("Steve", "u1", joined, None, None),
        (
            "Alex",
            "u2",
            joined,
            datetime(2026, 10, 25, 13, 0, tzinfo=EASTERN),
            "Disconnected",
        ),
        (
            "Bob",
            "u3",
            joined,
            datetime(2026, 10, 25, 12, 30, tzinfo=EASTERN),
            "server_stop",
        ),
    ]
    repo = MinecraftRepository(pool)
    sessions = repo.fetch_sessions(ws, we, as_of=datetime(2026, 10, 25, 14, 0, tzinfo=EASTERN))
    assert sessions[0].end_reason == "still_online"
    assert sessions[0].end.hour == 14
    assert sessions[1].end_reason == "leave"
    assert sessions[2].end_reason == "server_stop"


def test_fetch_deaths_strips_player_prefix_and_keeps_killer():
    pool = MagicMock()
    ws = datetime(2026, 10, 25, 5, 0, tzinfo=EASTERN)
    we = datetime(2026, 10, 26, 5, 0, tzinfo=EASTERN)
    pool.fetchall.return_value = [
        (
            "Steve",
            datetime(2026, 10, 25, 12, 0, tzinfo=EASTERN),
            "Steve was slain by Alex",
            "Alex",
        ),
    ]
    repo = MinecraftRepository(pool)
    deaths = repo.fetch_deaths(ws, we)
    assert deaths[0].message == "was slain by Alex"
    assert deaths[0].killer_player == "Alex"


def test_helpers_normalize_end_reason_and_death_message():
    assert _normalize_end_reason("Timed out", still_online=False) == "leave"
    assert _normalize_end_reason("rejoin", still_online=False) == "rejoin"
    assert _normalize_end_reason(None, still_online=True) == "still_online"
    assert _death_message("Steve", "Steve fell from a high place") == (
        "fell from a high place"
    )
    assert _death_message("Steve", "fell from a high place") == "fell from a high place"
