"""Unit tests for MinecraftRepository persistence and daily post claim locking."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from unittest.mock import MagicMock

from config import EASTERN
from db.minecraft import MinecraftRepository
from minecraft.models import Advancement, DayRecap, Death, SessionSlice
from minecraft.parser import day_window, pretty_date


def _mock_pool(cursor: MagicMock) -> MagicMock:
    pool = MagicMock()

    @contextmanager
    def _tx():
        yield cursor

    pool.transaction.side_effect = _tx
    return pool


def test_claim_daily_post_states():
    day = date(2026, 10, 25)

    # First claim inserts pending
    cur = MagicMock()
    cur.fetchone.return_value = None
    repo = MinecraftRepository(_mock_pool(cur))
    assert repo.claim_daily_post(day, 999) == "claimed"

    # Already posted returns already_posted
    cur = MagicMock()
    cur.fetchone.return_value = ("posted",)
    repo = MinecraftRepository(_mock_pool(cur))
    assert repo.claim_daily_post(day, 999) == "already_posted"

    # Leftover pending claim is reclaimed after crash
    cur = MagicMock()
    cur.fetchone.return_value = ("pending",)
    cur.rowcount = 1
    repo = MinecraftRepository(_mock_pool(cur))
    assert repo.claim_daily_post(day, 999) == "claimed"


def test_persist_day_replaces_and_dedupes_in_single_transaction():
    day = date(2026, 10, 25)
    ws, we = day_window(day)
    t1 = datetime(2026, 10, 25, 10, 0, tzinfo=EASTERN)
    t2 = datetime(2026, 10, 25, 11, 0, tzinfo=EASTERN)

    dup_slice = SessionSlice("Steve", "u1", t1, t2, t1, t2, 3600, 3600, "leave", False)
    dup_death = Death("Steve", t1, "fell from a high place")
    dup_adv = Advancement("Steve", t1, "advancement", "Stone Age")
    recap = DayRecap(
        day=day,
        pretty_date=pretty_date(day),
        window_start=ws,
        window_end=we,
        partial_day=False,
        players=[],
        slices=[dup_slice, dup_slice],
        deaths=[dup_death, dup_death],
        advancements=[dup_adv, dup_adv],
        notable_server_events=[],
        player_uuids={"Steve": "u1"},
    )

    cur = MagicMock()
    pool = _mock_pool(cur)
    repo = MinecraftRepository(pool)
    repo.persist_day(recap)

    assert pool.transaction.call_count == 1
    # 3 deletes + 3 executemany inserts (deduped to 1 row each)
    assert cur.execute.call_count == 3
    assert cur.executemany.call_count == 3
    for call in cur.executemany.call_args_list:
        assert len(call.args[1]) == 1
