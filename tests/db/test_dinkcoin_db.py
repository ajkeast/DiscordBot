"""Unit tests for DinkCoinRepository and FirstGameRepository atomic SQL flows."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from db.dinkcoin import (
    DinkCoinRepository,
    InsufficientBalanceError,
    RequestStateError,
)
from db.first_game import FirstGameRepository


def _mock_pool_with_cursor(cursor: MagicMock) -> MagicMock:
    pool = MagicMock()

    @contextmanager
    def _tx():
        yield cursor

    pool.transaction.side_effect = _tx
    return pool


def test_dink_balance_and_circulation():
    pool = MagicMock()
    repo = DinkCoinRepository(pool)

    pool.fetchone.return_value = None
    assert repo.get_balance(123) == 0.0

    pool.fetchone.return_value = (4.25,)
    assert repo.get_balance(123) == 4.25

    pool.fetchone.return_value = (12.5,)
    assert repo.get_total_circulation() == 12.5


def test_transfer_is_atomic_and_raises_when_insufficient():
    cursor = MagicMock()
    cursor.fetchone.return_value = (8.0,)
    pool = _mock_pool_with_cursor(cursor)
    repo = DinkCoinRepository(pool)

    new_bal = repo.transfer(111, 222, 2)
    assert new_bal == 8.0
    assert pool.transaction.call_count == 1
    assert cursor.execute.call_count == 3  # debit, credit, transaction log

    cursor.reset_mock()
    cursor.fetchone.return_value = None
    with pytest.raises(InsufficientBalanceError):
        repo.transfer(111, 222, 50)
    assert cursor.execute.call_count == 1


def test_accept_request_checks_owner_expiry_and_settles_atomically():
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    future = now + timedelta(hours=12)
    past = now - timedelta(hours=1)

    # Wrong user
    cursor = MagicMock()
    cursor.fetchone.return_value = (10, 111, 222, 3, "pending", future)
    repo = DinkCoinRepository(_mock_pool_with_cursor(cursor))
    with pytest.raises(RequestStateError, match="Only the requested user"):
        repo.accept_request(10, actor_id=999, now=now)

    # Expired request
    cursor = MagicMock()
    cursor.fetchone.return_value = (10, 111, 222, 3, "pending", past)
    repo = DinkCoinRepository(_mock_pool_with_cursor(cursor))
    with pytest.raises(RequestStateError, match="expired"):
        repo.accept_request(10, actor_id=222, now=now)

    # Happy path settles inside one transaction
    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        (10, 111, 222, 3, "pending", future),
        (7.0,),  # debit RETURNING balance
    ]
    pool = _mock_pool_with_cursor(cursor)
    repo = DinkCoinRepository(pool)
    rec = repo.accept_request(10, actor_id=222, now=now)
    assert rec.status == "accepted"
    assert pool.transaction.call_count == 1


def test_claim_first_for_day_atomic_lock_and_mint():
    # Already claimed on same Eastern day -> returns False without inserting
    cursor = MagicMock()
    cursor.fetchone.return_value = (datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc),)
    repo = FirstGameRepository(_mock_pool_with_cursor(cursor))
    assert repo.claim_first_for_day(111, date(2026, 6, 11), 1.0) is False
    assert cursor.execute.call_count == 2  # lock + select latest

    # New day -> inserts firstlist_id + mints DINK + logs mint in one transaction
    cursor = MagicMock()
    cursor.fetchone.return_value = (datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),)
    pool = _mock_pool_with_cursor(cursor)
    repo = FirstGameRepository(pool)
    assert repo.claim_first_for_day(111, date(2026, 6, 11), 1.0) is True
    assert pool.transaction.call_count == 1
    assert cursor.execute.call_count == 5  # lock, select, insert 1st, upsert bal, log mint
