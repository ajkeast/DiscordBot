"""Tests for the daily /1st game cog commands."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cogs.first import First
from config import GENERAL_CHANNEL_ID
from tests.reporting import SECTION_COMMANDS


@pytest.fixture
def first_cog(mock_bot):
    repo = MagicMock()
    return First(mock_bot, repo=repo)


async def test_first_wrong_channel(first_cog, mock_ctx):
    mock_ctx.channel.id = 111111111
    await first_cog.first.callback(first_cog, mock_ctx)
    mock_ctx.send.assert_awaited_once_with(
        f"Please send your message to <#{GENERAL_CHANNEL_ID}>."
    )
    first_cog.repo.claim_first_for_day.assert_not_called()


async def test_first_atomic_claim_and_already_claimed(report, first_cog, mock_ctx):
    first_cog.repo.claim_first_for_day.return_value = True
    await first_cog.first.callback(first_cog, mock_ctx)
    sent = mock_ctx.send.call_args.args[0]
    report.record("win message", "is first today!", sent, section=SECTION_COMMANDS)
    assert "is first today!" in sent
    assert "+1 **DINK**" in sent
    assert mock_ctx.send.call_args.kwargs.get("view") is not None

    mock_ctx.send.reset_mock()
    first_cog.repo.claim_first_for_day.return_value = False
    await first_cog.first.callback(first_cog, mock_ctx)
    assert "already been claimed today" in mock_ctx.send.call_args.args[0]


async def test_score_and_juice_leaderboards(
    first_cog, mock_ctx, leaderboard_first_entries
):
    first_cog.repo.get_all_entries.return_value = []
    await first_cog.score.callback(first_cog, mock_ctx)
    assert "No firsts recorded yet" in mock_ctx.send.call_args.args[0]

    mock_ctx.send.reset_mock()
    first_cog.repo.get_all_entries.return_value = leaderboard_first_entries
    await first_cog.score.callback(first_cog, mock_ctx)
    embed = mock_ctx.send.call_args.kwargs["embed"]
    assert embed.title == "First Leaderboard"
    assert len(embed.fields) == 5

    mock_ctx.send.reset_mock()
    await first_cog.juice.callback(first_cog, mock_ctx)
    embed = mock_ctx.send.call_args.kwargs["embed"]
    assert embed.title == "Juice Board 🧃"
    assert len(embed.fields) == 5


async def test_stats_handles_uncached_user_without_crashing(
    first_cog, mock_ctx, sample_first_entries
):
    first_cog.repo.get_all_entries.return_value = sample_first_entries
    first_cog.bot.get_user.return_value = None
    mock_ctx.author.id = 111

    await first_cog.stats.callback(first_cog, mock_ctx)
    embed = mock_ctx.send.call_args.kwargs["embed"]
    assert len(embed.fields) == 3


async def test_graph_and_juicegraph_render_png_attachments(
    first_cog, mock_ctx, sample_first_entries
):
    first_cog.repo.get_all_entries.return_value = sample_first_entries

    await first_cog.graph.callback(first_cog, mock_ctx)
    assert mock_ctx.send.call_args.kwargs["file"].filename == "first_graph.png"

    mock_ctx.send.reset_mock()
    await first_cog.juicegraph.callback(first_cog, mock_ctx)
    assert mock_ctx.send.call_args.kwargs["file"].filename == "juice_graph.png"
