"""Tests for message edit-history SQL invariants and bot event routing."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from bot import DinkBot
from db.messages import (
    INSERT_KNOWN_EDIT_SQL,
    INSERT_MESSAGE_SQL,
    UPDATE_EDITED_CONTENT_SQL,
    UPSERT_UNSEEN_EDIT_SQL,
    MessagesRepository,
)
from db.sentiment import UNSCORED_WITH_PRIORS_SQL


def _setter_clause(sql: str) -> str:
    clause = sql.split("DO UPDATE SET", 1)[1] if "DO UPDATE SET" in sql else sql.split("SET", 1)[1]
    return clause.split("WHERE", 1)[0].replace("edited_content", "")


def test_edit_sql_statements_never_overwrite_original_content():
    for sql in (INSERT_KNOWN_EDIT_SQL, UPSERT_UNSEEN_EDIT_SQL, UPDATE_EDITED_CONTENT_SQL):
        assert "content" not in _setter_clause(sql).lower()


def test_insert_reclaims_original_only_when_unedited():
    clause = INSERT_MESSAGE_SQL.split("DO UPDATE SET", 1)[1]
    assert "messages.edited_content IS NULL" in clause
    assert "content = EXCLUDED.content" in clause
    assert "edited_content = messages.content" in clause


def test_sentiment_lateral_sql_reads_original_content_not_edits():
    assert "edited_content" not in UNSCORED_WITH_PRIORS_SQL
    assert "LATERAL" in UNSCORED_WITH_PRIORS_SQL


def test_record_edit_ignores_identical_known_original():
    pool = MagicMock()
    repo = MessagesRepository(pool)
    status = repo.record_edit(
        1,
        "same",
        member_id=10,
        channel_id=20,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        original_content="same",
    )
    assert status == "unchanged"
    pool.transaction.assert_not_called()


async def test_bot_edit_handlers_filter_bots_and_embed_only_updates():
    bot = DinkBot.__new__(DinkBot)
    bot.messages_repo = MagicMock()

    human = SimpleNamespace(id=10, bot=False)
    bot_user = SimpleNamespace(id=99, bot=True)
    channel = SimpleNamespace(id=20)
    ts = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    # Identical text (link preview unfurl) is ignored
    before = SimpleNamespace(id=1, content="https://example.com", author=human, channel=channel, created_at=ts)
    after = SimpleNamespace(id=1, content="https://example.com", author=human, channel=channel, created_at=ts)
    await bot.on_message_edit(before, after)
    bot.messages_repo.record_edit.assert_not_called()

    # Bot edit ignored
    after_bot = SimpleNamespace(id=2, content="edited", author=bot_user, channel=channel, created_at=ts)
    await bot.on_message_edit(before, after_bot)
    bot.messages_repo.record_edit.assert_not_called()

    # Real human edit forwarded
    after_edit = SimpleNamespace(id=3, content="typo fixed", author=human, channel=channel, created_at=ts)
    await bot.on_message_edit(before, after_edit)
    bot.messages_repo.record_edit.assert_called_once()

    # Cached raw edit skipped (handled by on_message_edit)
    bot.messages_repo.record_edit.reset_mock()
    cached_payload = SimpleNamespace(cached_message=before, message_id=3, channel_id=20, data={"content": "x"})
    await bot.on_raw_message_edit(cached_payload)
    bot.messages_repo.record_edit.assert_not_called()

    # Uncached raw edit forwarded
    raw_payload = SimpleNamespace(
        cached_message=None,
        message=None,
        message_id=123456789012345678,
        channel_id=20,
        data={"content": "uncached edit", "author": {"id": "42", "bot": False}},
    )
    await bot.on_raw_message_edit(raw_payload)
    bot.messages_repo.record_edit.assert_called_once()
    assert bot.messages_repo.record_edit.call_args.kwargs["original_content"] is None
    assert bot.messages_repo.record_edit.call_args.kwargs["member_id"] == 42
