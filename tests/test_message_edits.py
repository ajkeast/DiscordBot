"""Original message text is kept when Discord messages are edited."""

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord.utils
import pytest

from bot import DinkBot
from utils.db import (
    INSERT_KNOWN_EDIT_SQL,
    INSERT_MESSAGE_SQL,
    UPDATE_EDITED_CONTENT_SQL,
    UPSERT_UNSEEN_EDIT_SQL,
    DataOperations,
    db_ops,
    message_edit_schema_statements,
)
from utils.sentiment_job import PRIORS_SQL, UNSCORED_SQL

SECTION = "Message edits"
CREATED = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


def _norm(sql: str) -> str:
    return " ".join(sql.split())


class MessageTable:
    """In-memory stand-in for the messages upsert/update statements."""

    def __init__(self):
        self.rows = {}
        self.clock = 0
        self.schema_statements = []
        self.rowcount = 0

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, query, params=None):
        norm = _norm(query)
        upper = norm.upper()
        if upper.startswith("ALTER ") or upper.startswith("COMMENT "):
            self.schema_statements.append(norm)
            self.rowcount = 0
            return
        kind = {
            _norm(INSERT_MESSAGE_SQL): "insert",
            _norm(INSERT_KNOWN_EDIT_SQL): "known_edit",
            _norm(UPSERT_UNSEEN_EDIT_SQL): "unseen_edit",
            _norm(UPDATE_EDITED_CONTENT_SQL): "update_edit",
        }.get(norm)
        if kind is None:
            raise AssertionError(f"unexpected SQL: {norm}")
        getattr(self, f"_apply_{kind}")(params or ())

    def _tick(self):
        self.clock += 1
        return self.clock

    def _current_text(self, row):
        if row["edited_content"] is not None:
            return row["edited_content"]
        return row["content"]

    def _apply_insert(self, params):
        message_id, member_id, channel_id, content, created_at = params
        row = self.rows.get(message_id)
        if row is None:
            self.rows[message_id] = {
                "member_id": member_id,
                "channel_id": channel_id,
                "content": content,
                "created_at": created_at,
                "edited_content": None,
                "edited_at": None,
            }
            self.rowcount = 1
            return
        if row["edited_content"] is None and row["content"] != content:
            row["edited_content"] = row["content"]
            row["edited_at"] = self._tick()
            row["content"] = content
            self.rowcount = 1
            return
        self.rowcount = 0

    def _apply_known_edit(self, params):
        message_id, member_id, channel_id, original, created_at, new_text = params
        row = self.rows.get(message_id)
        if row is None:
            self.rows[message_id] = {
                "member_id": member_id,
                "channel_id": channel_id,
                "content": original,
                "created_at": created_at,
                "edited_content": new_text,
                "edited_at": self._tick(),
            }
            self.rowcount = 1
            return
        if new_text != self._current_text(row):
            row["edited_content"] = new_text
            row["edited_at"] = self._tick()
            self.rowcount = 1
            return
        self.rowcount = 0

    def _apply_unseen_edit(self, params):
        message_id, member_id, channel_id, new_text, created_at, edited, compare = params
        assert edited == new_text
        assert compare == new_text
        row = self.rows.get(message_id)
        if row is None:
            self.rows[message_id] = {
                "member_id": member_id,
                "channel_id": channel_id,
                "content": new_text,
                "created_at": created_at,
                "edited_content": None,
                "edited_at": None,
            }
            self.rowcount = 1
            return
        if new_text != self._current_text(row):
            row["edited_content"] = new_text
            row["edited_at"] = self._tick()
            self.rowcount = 1
            return
        self.rowcount = 0

    def _apply_update_edit(self, params):
        new_text, message_id, compare = params
        assert compare == new_text
        row = self.rows.get(message_id)
        if row is None or new_text == self._current_text(row):
            self.rowcount = 0
            return
        row["edited_content"] = new_text
        row["edited_at"] = self._tick()
        self.rowcount = 1


@pytest.fixture
def messages():
    db_ops._message_edit_schema_ready = False
    table = MessageTable()
    with patch.object(db_ops.db, "execute", table.execute), patch.object(
        db_ops.db, "cursor", table.cursor
    ):
        yield table
    db_ops._message_edit_schema_ready = False


def _insert(message_id, content, *, member_id=10, channel_id=20):
    DataOperations.update_messages(
        db_ops,
        (message_id, member_id, channel_id, content, CREATED),
    )


def _setter_clause(sql: str) -> str:
    if "DO UPDATE SET" in sql:
        clause = sql.split("DO UPDATE SET", 1)[1]
    else:
        clause = sql.split("SET", 1)[1]
    return clause.split("WHERE", 1)[0].replace("edited_content", "")


def test_schema_is_additive_and_loaded(report):
    from pathlib import Path

    file_sql = Path("scripts/messages_edit_schema.sql").read_text(encoding="utf-8")
    lowered = file_sql.lower()
    statements = message_edit_schema_statements()
    loaded = " ".join(statements).lower()

    report.record("statement count", 4, len(statements), section=SECTION)
    assert "drop " not in lowered
    assert "rename " not in lowered
    assert "alter column" not in lowered
    assert "add column if not exists edited_content varchar(4000)" in lowered
    assert "add column if not exists edited_at timestamp" in lowered
    assert len(statements) == 4
    assert "add column if not exists edited_content" in loaded
    assert "add column if not exists edited_at" in loaded
    assert "comment on column messages.edited_content" in loaded
    assert "comment on column messages.edited_at" in loaded
    for statement in statements:
        assert statement.lower().startswith(("alter table", "comment on column"))
        assert _norm(statement) in _norm(file_sql)


def test_edit_statements_do_not_assign_content():
    for sql in (INSERT_KNOWN_EDIT_SQL, UPSERT_UNSEEN_EDIT_SQL, UPDATE_EDITED_CONTENT_SQL):
        setter = _setter_clause(sql)
        assert "content" not in setter.lower()


def test_insert_reclaims_original_only_when_unedited():
    clause = INSERT_MESSAGE_SQL.split("DO UPDATE SET", 1)[1]
    assert "messages.edited_content IS NULL" in clause
    assert "content = EXCLUDED.content" in clause
    assert "edited_content = messages.content" in clause


def test_ensure_runs_schema_once():
    db_ops._message_edit_schema_ready = False
    calls = []

    def _execute(query, params=None):
        calls.append(_norm(query))

    try:
        with patch.object(db_ops.db, "execute", side_effect=_execute):
            db_ops.ensure_message_edit_columns()
            db_ops.ensure_message_edit_columns()
        assert calls == [_norm(statement) for statement in message_edit_schema_statements()]
    finally:
        db_ops._message_edit_schema_ready = False


def test_sentiment_keeps_reading_original_content():
    assert "m.content" in UNSCORED_SQL
    assert "m.content" in PRIORS_SQL
    assert "edited_content" not in UNSCORED_SQL
    assert "edited_content" not in PRIORS_SQL


def test_repeated_edits_keep_original_and_latest_text(report, messages):
    _insert(1, "original")
    first = db_ops.record_message_edit(
        1,
        "edit one",
        member_id=10,
        channel_id=20,
        created_at=CREATED,
        original_content="original",
    )
    second = db_ops.record_message_edit(
        1,
        "edit two",
        member_id=10,
        channel_id=20,
        created_at=CREATED,
        original_content="edit one",
    )
    same = db_ops.record_message_edit(
        1,
        "edit two",
        member_id=10,
        channel_id=20,
        created_at=CREATED,
        original_content="edit two",
    )
    row = messages.rows[1]
    report.record("original content", "original", row["content"], section=SECTION)
    report.record("edited content", "edit two", row["edited_content"], section=SECTION)
    report.record("unchanged status", "unchanged", same, section=SECTION)
    assert first == "stored"
    assert second == "stored"
    assert same == "unchanged"
    assert row["content"] == "original"
    assert row["edited_content"] == "edit two"
    assert row["edited_at"] == 2
    assert len(messages.schema_statements) == 4


def test_identical_cached_text_does_not_touch_the_row(messages):
    _insert(1, "https://example.com")
    before = messages.clock
    status = db_ops.record_message_edit(
        1,
        "https://example.com",
        member_id=10,
        channel_id=20,
        created_at=CREATED,
        original_content="https://example.com",
    )
    row = messages.rows[1]
    assert status == "unchanged"
    assert row["edited_content"] is None
    assert row["edited_at"] is None
    assert messages.clock == before


def test_uncached_edit_updates_final_text_only(report, messages):
    _insert(1, "original")
    changed = db_ops.record_message_edit(
        1,
        "from raw",
        member_id=10,
        channel_id=20,
        created_at=CREATED,
        original_content=None,
    )
    preview = db_ops.record_message_edit(
        1,
        "from raw",
        member_id=10,
        channel_id=20,
        created_at=CREATED,
        original_content=None,
    )
    row = messages.rows[1]
    report.record("original kept", "original", row["content"], section=SECTION)
    report.record("raw edit stored", "from raw", row["edited_content"], section=SECTION)
    assert changed == "stored"
    assert preview == "unchanged"
    assert row["content"] == "original"
    assert row["edited_content"] == "from raw"
    assert row["edited_at"] == 1


def test_clearing_content_is_an_edit(messages):
    _insert(1, "hello")
    db_ops.record_message_edit(
        1,
        "",
        member_id=10,
        channel_id=20,
        created_at=CREATED,
        original_content="hello",
    )
    row = messages.rows[1]
    assert row["content"] == "hello"
    assert row["edited_content"] == ""


def test_reverting_to_original_updates_final_column_only(messages):
    _insert(1, "original")
    db_ops.record_message_edit(
        1,
        "changed",
        member_id=10,
        channel_id=20,
        created_at=CREATED,
        original_content="original",
    )
    db_ops.record_message_edit(
        1,
        "original",
        member_id=10,
        channel_id=20,
        created_at=CREATED,
        original_content="changed",
    )
    row = messages.rows[1]
    assert row["content"] == "original"
    assert row["edited_content"] == "original"
    assert row["edited_at"] == 2


def test_unseen_edit_then_create_keeps_both_versions(messages):
    db_ops.record_message_edit(
        5,
        "edited first",
        member_id=10,
        channel_id=20,
        created_at=CREATED,
        original_content=None,
    )
    assert messages.rows[5]["content"] == "edited first"
    assert messages.rows[5]["edited_content"] is None
    _insert(5, "true original")
    row = messages.rows[5]
    assert row["content"] == "true original"
    assert row["edited_content"] == "edited first"


def test_create_does_not_overwrite_a_recorded_original(messages):
    db_ops.record_message_edit(
        7,
        "new",
        member_id=10,
        channel_id=20,
        created_at=CREATED,
        original_content="orig",
    )
    _insert(7, "orig")
    _insert(7, "someone tried to replace it")
    row = messages.rows[7]
    assert row["content"] == "orig"
    assert row["edited_content"] == "new"


def test_edit_without_author_updates_existing_row_only(messages):
    _insert(1, "original")
    updated = db_ops.record_message_edit(
        1,
        "changed",
        member_id=None,
        channel_id=None,
        created_at=None,
        original_content=None,
    )
    missing = db_ops.record_message_edit(
        99,
        "changed",
        member_id=None,
        channel_id=None,
        created_at=None,
        original_content=None,
    )
    assert updated == "updated"
    assert messages.rows[1]["content"] == "original"
    assert messages.rows[1]["edited_content"] == "changed"
    assert missing == "missing"
    assert 99 not in messages.rows


def _user_message(content, *, bot=False, message_id=1):
    author = SimpleNamespace(id=10, bot=bot)
    channel = SimpleNamespace(id=20)
    return SimpleNamespace(
        id=message_id,
        author=author,
        channel=channel,
        content=content,
        created_at=CREATED,
    )


@pytest.fixture
def dink_bot():
    return DinkBot()


async def test_on_message_stores_original(mock_db_ops, dink_bot):
    dink_bot.process_commands = AsyncMock()
    await dink_bot.on_message(_user_message("hello"))
    mock_db_ops.update_messages.assert_called_once()
    assert mock_db_ops.update_messages.call_args.args[0][3] == "hello"


async def test_cached_edit_stores_original_and_new_text(mock_db_ops, dink_bot):
    before = _user_message("hello")
    after = _user_message("hello there")
    payload = SimpleNamespace(
        cached_message=before,
        data={"content": "hello there", "author": {"id": "10"}},
        message_id=1,
        channel_id=20,
        message=after,
    )
    with patch.object(db_ops, "record_message_edit", return_value="stored") as record:
        await dink_bot.on_raw_message_edit(payload)
        await dink_bot.on_message_edit(before, after)
    record.assert_called_once()
    assert record.call_args.args == (1, "hello there")
    assert record.call_args.kwargs["original_content"] == "hello"
    assert record.call_args.kwargs["member_id"] == 10
    mock_db_ops.update_messages.assert_not_called()


async def test_embed_only_cached_edit_skips_the_database(mock_db_ops, dink_bot):
    before = _user_message("https://example.com")
    after = _user_message("https://example.com")
    with patch.object(db_ops, "record_message_edit") as record:
        await dink_bot.on_message_edit(before, after)
    record.assert_not_called()
    mock_db_ops.update_messages.assert_not_called()


async def test_bot_edits_are_ignored(dink_bot):
    before = _user_message("hello", bot=True)
    after = _user_message("hello!", bot=True)
    payload = SimpleNamespace(
        cached_message=None,
        data={"content": "hello!", "author": {"id": "10", "bot": True}},
        message_id=1,
        channel_id=20,
        message=after,
    )
    with patch.object(db_ops, "record_message_edit") as record:
        await dink_bot.on_message_edit(before, after)
        await dink_bot.on_raw_message_edit(payload)
    record.assert_not_called()


async def test_raw_edit_without_content_is_ignored(dink_bot):
    payload = SimpleNamespace(
        cached_message=None,
        data={"pinned": True, "id": "1"},
        message_id=1,
        channel_id=20,
        message=None,
    )
    with patch.object(db_ops, "record_message_edit") as record:
        await dink_bot.on_raw_message_edit(payload)
    record.assert_not_called()


async def test_uncached_raw_edit_passes_unknown_original(report, dink_bot):
    payload = SimpleNamespace(
        cached_message=None,
        data={"content": "from raw", "author": {"id": "55"}},
        message_id=123,
        channel_id=9,
        message=None,
    )
    with patch.object(db_ops, "record_message_edit", return_value="stored") as record:
        await dink_bot.on_raw_message_edit(payload)
    record.assert_called_once()
    assert record.call_args.args == (123, "from raw")
    assert record.call_args.kwargs["original_content"] is None
    assert record.call_args.kwargs["member_id"] == 55
    assert record.call_args.kwargs["channel_id"] == 9
    created_at = record.call_args.kwargs["created_at"]
    report.record(
        "created_at from snowflake",
        discord.utils.snowflake_time(123),
        created_at,
        section=SECTION,
    )
    assert created_at == discord.utils.snowflake_time(123)
