"""Shared pytest fixtures for DiscordBot tests."""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("MPLBACKEND", "Agg")

from config import GENERAL_CHANNEL_ID  # noqa: E402
from db.first_game import FirstEntry  # noqa: E402
from tests.reporting import get_collector, reset_collector, write_report  # noqa: E402

EXPECTED_COMMANDS = frozenset(
    {
        "1st", "score", "stats", "juice", "graph", "juicegraph",
        "balance", "ledger", "pay", "request",
        "ask", "imagine", "clear", "voice",
        "members", "emojis", "channels",
        "hello", "ping", "simonsays", "dashboard", "donation",
        "score_sentiment",
        "play", "skip", "stop", "queue", "np", "pause", "resume", "leave",
        "minecraft_summary",
    }
)


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


def make_discord_user(user_id: int | str, *, admin: bool = False):
    user = MagicMock()
    user.id = int(user_id)
    user.name = f"User{user_id}"
    user.display_name = f"User{user_id}"
    user.mention = f"<@{user_id}>"
    user.bot = False
    user.guild_permissions = MagicMock(administrator=admin)
    avatar = MagicMock()
    avatar.__str__ = lambda _self: f"https://cdn.discordapp.com/avatars/{user_id}.png"
    user.display_avatar.with_size.return_value = avatar
    return user


@pytest.fixture
def mock_author():
    author = make_discord_user(123456789, admin=False)
    author.display_name = "TestUser"
    return author


@pytest.fixture
def mock_admin_author():
    author = make_discord_user(999999999, admin=True)
    author.display_name = "AdminUser"
    return author


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.get_user = MagicMock(side_effect=lambda uid: make_discord_user(uid))
    bot.get_channel = MagicMock(return_value=AsyncMock())
    bot.fetch_channel = AsyncMock(return_value=AsyncMock())
    bot.is_owner = AsyncMock(return_value=False)
    bot.http_session = None
    bot.messages_repo = MagicMock()
    return bot


@pytest.fixture
def mock_ctx(mock_author):
    ctx = AsyncMock()
    ctx.author = mock_author
    ctx.guild = MagicMock()
    ctx.channel = AsyncMock()
    ctx.channel.id = GENERAL_CHANNEL_ID
    ctx.channel.send = AsyncMock()
    ctx.send = AsyncMock()
    ctx.message = MagicMock()
    ctx.message.id = 987654321
    ctx.message.author = mock_author
    ctx.message.mentions = []
    ctx.message.attachments = []
    ctx.message.reference = None
    ctx.interaction = None

    typing_cm = AsyncMock()
    typing_cm.__aenter__ = AsyncMock(return_value=None)
    typing_cm.__aexit__ = AsyncMock(return_value=None)
    ctx.typing = MagicMock(return_value=typing_cm)
    ctx.defer = AsyncMock()
    return ctx


@pytest.fixture
def sample_first_entries() -> list[FirstEntry]:
    return [
        FirstEntry("111", datetime(2024, 1, 1, 14, 0, 0)),
        FirstEntry("111", datetime(2024, 1, 2, 14, 0, 0)),
        FirstEntry("222", datetime(2024, 1, 3, 14, 0, 0)),
        FirstEntry("111", datetime(2024, 1, 4, 14, 0, 0)),
    ]


@pytest.fixture
def leaderboard_first_entries() -> list[FirstEntry]:
    return [
        FirstEntry("101", datetime(2024, 1, 1, 14, 0, 0)),
        FirstEntry("102", datetime(2024, 1, 2, 14, 0, 0)),
        FirstEntry("103", datetime(2024, 1, 3, 14, 0, 0)),
        FirstEntry("104", datetime(2024, 1, 4, 14, 0, 0)),
        FirstEntry("105", datetime(2024, 1, 5, 14, 0, 0)),
        FirstEntry("101", datetime(2024, 1, 6, 14, 0, 0)),
    ]
