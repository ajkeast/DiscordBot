"""Tests for Server cog admin sync commands and idempotent monthly report."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.server import Server, _report_month_due
from config import EASTERN
from db.server import MonthlyCount
from tests.conftest import make_discord_user


@pytest.fixture
def server_cog(mock_bot):
    repo = MagicMock()
    return Server(mock_bot, repo=repo)


def test_report_month_due_window():
    assert _report_month_due(datetime(2026, 3, 1, 8, 59, tzinfo=EASTERN)) is None
    assert _report_month_due(datetime(2026, 3, 1, 9, 0, tzinfo=EASTERN)) == (2026, 2)
    assert _report_month_due(datetime(2026, 1, 1, 14, 30, tzinfo=EASTERN)) == (2025, 12)
    assert _report_month_due(datetime(2026, 3, 2, 9, 0, tzinfo=EASTERN)) is None


async def test_monthly_report_claims_posts_and_marks_posted(server_cog):
    server_cog.repo.claim_monthly_report.return_value = "claimed"
    server_cog.repo.get_monthly_message_counts.return_value = [
        MonthlyCount(1, "Alice", 120),
        MonthlyCount(2, "Bob", 85),
    ]
    channel = AsyncMock()
    channel.send.return_value = MagicMock(id=777)
    server_cog.bot.get_channel.return_value = channel

    posted = await server_cog._post_monthly_report_if_due(
        datetime(2026, 3, 1, 9, 5, tzinfo=EASTERN)
    )
    assert posted is True
    channel.send.assert_awaited_once()
    server_cog.repo.mark_monthly_report_posted.assert_called_once()


async def test_members_emojis_channels_reject_non_admin(server_cog, mock_ctx):
    await server_cog.members.callback(server_cog, mock_ctx)
    assert "Only server admins" in mock_ctx.send.call_args.args[0]
    server_cog.repo.upsert_members.assert_not_called()

    await server_cog.emojis.callback(server_cog, mock_ctx)
    server_cog.repo.upsert_emojis.assert_not_called()

    await server_cog.channels.callback(server_cog, mock_ctx)
    server_cog.repo.upsert_channels.assert_not_called()


async def test_members_reads_member_attributes_directly_without_get_user(
    server_cog, mock_ctx, mock_admin_author
):
    mock_ctx.author = mock_admin_author
    server_cog.bot.get_user.return_value = None  # uncached!

    member = make_discord_user(111)
    member.name = "alice"
    member.nick = "Ally"
    member.created_at = datetime(2020, 1, 1)
    mock_ctx.guild.members = [member]

    await server_cog.members.callback(server_cog, mock_ctx)

    rows = server_cog.repo.upsert_members.call_args.args[0]
    assert rows[0][0] == 111
    assert rows[0][1] == "alice"
    assert rows[0][2] == "Ally"
    mock_ctx.send.assert_awaited_once_with("Member info successfully updated.")


async def test_emojis_and_channels_for_admin(server_cog, mock_ctx, mock_admin_author):
    mock_ctx.author = mock_admin_author
    emoji = MagicMock(
        id=222, name="pepe", guild_id=333, url="https://cdn/e.png", created_at=datetime(2021, 1, 1)
    )
    channel = MagicMock(id=444, name="general", created_at=datetime(2019, 6, 1))
    mock_ctx.guild.emojis = [emoji]
    mock_ctx.guild.channels = [channel]

    await server_cog.emojis.callback(server_cog, mock_ctx)
    server_cog.repo.upsert_emojis.assert_called_once()

    await server_cog.channels.callback(server_cog, mock_ctx)
    server_cog.repo.upsert_channels.assert_called_once()
