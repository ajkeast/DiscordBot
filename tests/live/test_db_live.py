"""Live PostgreSQL integration tests (connectivity + real SQL execution)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from db.dinkcoin import DinkCoinRepository, InsufficientBalanceError
from db.first_game import FirstGameRepository
from db.messages import MessagesRepository
from db.pool import DatabasePool
from db.schema import apply_full_schema
from db.sentiment import SentimentRepository
from tests.reporting import SECTION_LIVE_DB

pytestmark = pytest.mark.live

_SQL_VARS = ("SQL_HOST", "SQL_USER", "SQL_PASSWORD", "SQL_DATABASE")


@pytest.fixture(scope="module")
def live_pool():
    missing = [var for var in _SQL_VARS if not os.getenv(var)]
    if missing:
        pytest.fail(f"Missing SQL env vars: {', '.join(missing)}")
    pool = DatabasePool()
    apply_full_schema(pool)
    try:
        yield pool
    finally:
        pool.close()


def test_postgres_pool_connectivity(report, live_pool):
    row = live_pool.fetchone("SELECT 1")
    value = row[0] if row else None
    report.record("SELECT 1", 1, value, section=SECTION_LIVE_DB)
    assert value == 1


def test_firstlist_readable(report, live_pool):
    repo = FirstGameRepository(live_pool)
    entries = repo.get_all_entries()
    report.record("firstlist_id rows", ">= 0", len(entries), section=SECTION_LIVE_DB)
    assert isinstance(entries, list)


def test_message_edits_and_sentiment_lateral_sql_against_real_postgres(live_pool):
    msg_repo = MessagesRepository(live_pool)
    sent_repo = SentimentRepository(live_pool)
    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    m1, m2 = 900_000_001, 900_000_002
    chan = 800_000_001

    try:
        msg_repo.insert_message(m1, 10, chan, "prior context", t0)
        msg_repo.insert_message(m2, 11, chan, "original text", t0 + timedelta(seconds=5))
        status = msg_repo.record_edit(
            m2,
            "edited text",
            member_id=11,
            channel_id=chan,
            created_at=t0 + timedelta(seconds=5),
            original_content="original text",
        )
        assert status == "stored"

        row = live_pool.fetchone("SELECT content, edited_content FROM messages WHERE id = %s", (m2,))
        assert row == ("original text", "edited text")

        unscored = sent_repo.fetch_unscored_with_priors(limit=10, context_size=3)
        by_id = {u.message_id: u for u in unscored}
        assert m2 in by_id
        assert by_id[m2].content == "original text"
        assert ("10", "prior context") in by_id[m2].priors
    finally:
        live_pool.execute("DELETE FROM messages WHERE id IN (%s, %s)", (m1, m2))


def test_dinkcoin_atomic_mint_transfer_and_request_against_real_postgres(live_pool):
    repo = DinkCoinRepository(live_pool)
    u1, u2 = 700_000_001, 700_000_002
    req_id = None
    try:
        repo.mint(u1, 10)
        assert repo.get_balance(u1) >= 10

        new_bal = repo.transfer(u1, u2, 3)
        assert new_bal >= 7
        assert repo.get_balance(u2) >= 3

        with pytest.raises(InsufficientBalanceError):
            repo.transfer(u2, u1, 999_999)

        req_id = repo.create_request(
            requester_id=u1,
            payer_id=u2,
            amount=2,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        accepted = repo.accept_request(req_id, actor_id=u2)
        assert accepted.status == "accepted"
    finally:
        if req_id is not None:
            live_pool.execute("DELETE FROM dinkcoin_requests WHERE id = %s", (req_id,))
        live_pool.execute(
            "DELETE FROM dinkcoin_transactions WHERE from_user_id IN (%s, %s) OR to_user_id IN (%s, %s)",
            (str(u1), str(u2), str(u1), str(u2)),
        )
        live_pool.execute(
            "DELETE FROM dinkcoin_balances WHERE user_id IN (%s, %s)",
            (str(u1), str(u2)),
        )
