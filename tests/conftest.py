"""Shared pytest fixtures for Discord bot tests."""

import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

os.environ.setdefault("MPLBACKEND", "Agg")

from tests.reporting import get_collector, reset_collector, write_report  # noqa: E402
from utils.constants import GENERAL_CHANNEL_ID  # noqa: E402

EXPECTED_COMMANDS = frozenset({
    "1st", "score", "stats", "juice", "graph", "juicegraph",
    "balance", "ledger", "pay",
    "ask", "imagine", "clear", "voice",
    "members", "emojis", "channels",
    "hello", "ping", "simonsays", "dashboard",
    "donation",
})


@pytest.fixture
def report(request):
    """Record expected/actual pairs for the CI job summary."""

    class Reporter:
        def record(self, field, expected, actual, section="General"):
            get_collector().add(
                test=request.node.name,
                section=section,
                field=field,
                expected=expected,
                actual=actual,
            )

    return Reporter()


def pytest_configure(config):
    reset_collector()


def pytest_sessionfinish(session, exitstatus):
    write_report()


@pytest.fixture
def mock_author():
    author = MagicMock()
    author.id = 123456789
    author.mention = "<@123456789>"
    author.display_name = "TestUser"
    avatar = MagicMock()
    avatar.__str__ = lambda _self: "https://cdn.discordapp.com/avatars/123.png"
    author.display_avatar.with_size.return_value = avatar
    return author


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.get_user = MagicMock(side_effect=lambda uid: _make_discord_user(uid))
    bot.get_channel = MagicMock(return_value=AsyncMock())
    return bot


def _make_discord_user(user_id):
    user = MagicMock()
    user.id = int(user_id)
    user.name = f"User{user_id}"
    user.display_name = f"User{user_id}"
    avatar = MagicMock()
    avatar.__str__ = lambda _self: f"https://cdn.discordapp.com/avatars/{user_id}.png"
    user.display_avatar.with_size.return_value = avatar
    return user


@pytest.fixture
def mock_ctx(mock_author):
    ctx = AsyncMock()
    ctx.author = mock_author
    ctx.channel = AsyncMock()
    ctx.channel.id = GENERAL_CHANNEL_ID
    ctx.channel.send = AsyncMock()
    ctx.send = AsyncMock()
    ctx.message = MagicMock()
    ctx.message.id = 987654321
    ctx.message.author = mock_author
    ctx.message.mentions = []
    ctx.message.attachments = []
    ctx.interaction = None

    typing_cm = AsyncMock()
    typing_cm.__aenter__ = AsyncMock(return_value=None)
    typing_cm.__aexit__ = AsyncMock(return_value=None)
    ctx.typing = MagicMock(return_value=typing_cm)
    ctx.defer = AsyncMock()

    return ctx


@pytest.fixture
def sample_first_df():
    """Sample firstlist_id rows spanning multiple users and days."""
    return pd.DataFrame({
        "user_id": ["111", "111", "222", "111"],
        "timesent": pd.to_datetime([
            "2024-01-01 14:00:00",
            "2024-01-02 14:00:00",
            "2024-01-03 14:00:00",
            "2024-01-04 14:00:00",
        ]),
    })


@pytest.fixture
def leaderboard_first_df():
    """At least five users for score/juice leaderboard commands."""
    return pd.DataFrame({
        "user_id": ["101", "102", "103", "104", "105", "101"],
        "timesent": pd.to_datetime([
            "2024-01-01 14:00:00",
            "2024-01-02 14:00:00",
            "2024-01-03 14:00:00",
            "2024-01-04 14:00:00",
            "2024-01-05 14:00:00",
            "2024-01-06 14:00:00",
        ]),
    })


@pytest.fixture
def empty_first_df():
    return pd.DataFrame(columns=["user_id", "timesent"])


@pytest.fixture(autouse=True)
def mock_db_ops(request):
    """Patch db_ops methods on the shared singleton for non-live tests."""
    if request.node.get_closest_marker("live"):
        yield None
        return

    from utils.db import db_ops

    patchers = [
        patch.object(db_ops, "get_table_data", MagicMock()),
        patch.object(db_ops, "write_first_entry", MagicMock()),
        patch.object(db_ops, "write_dalle_entry", MagicMock()),
        patch.object(db_ops, "update_members", MagicMock()),
        patch.object(db_ops, "update_emojis", MagicMock()),
        patch.object(db_ops, "update_channels", MagicMock()),
        patch.object(db_ops, "update_messages", MagicMock()),
        patch.object(db_ops, "log_chatgpt_interaction", MagicMock()),
        patch.object(db_ops, "get_dink_balance", MagicMock(return_value=0.0)),
        patch.object(db_ops, "get_dink_ledger", MagicMock()),
        patch.object(db_ops, "get_total_dink_circulation", MagicMock(return_value=0.0)),
        patch.object(db_ops, "record_dink_mint", MagicMock()),
        patch.object(db_ops, "record_dink_transfer", MagicMock()),
    ]
    for patcher in patchers:
        patcher.start()
    try:
        yield db_ops
    finally:
        for patcher in patchers:
            patcher.stop()


@pytest.fixture
def ai_cog(mock_bot):
    from cogs.ai import AI

    mock_grok = MagicMock()
    mock_grok.send_message.return_value = ("resp-id-1", "AI reply text")

    with patch("cogs.ai.GrokClient", return_value=mock_grok):
        cog = AI(mock_bot)
    cog.grok = mock_grok
    return cog
