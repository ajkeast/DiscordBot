"""Tests for Utility cog commands (including merged /donation and safe /simonsays)."""

from __future__ import annotations

import discord

from cogs.utility import Utility
from config import EMBED_COLOR


async def test_hello_and_ping(mock_bot, mock_ctx):
    cog = Utility(mock_bot)

    await cog.hello.callback(cog, mock_ctx)
    mock_ctx.send.assert_awaited_once_with(f"Hello {mock_ctx.author.mention}!")

    mock_ctx.send.reset_mock()
    await cog.ping.callback(cog, mock_ctx)
    mock_ctx.send.assert_awaited_once_with("pong")


async def test_simonsays_disables_all_mentions(mock_bot, mock_ctx):
    cog = Utility(mock_bot)
    await cog.simonsays.callback(cog, mock_ctx, message="@everyone hello")

    args = mock_ctx.send.call_args.args
    kwargs = mock_ctx.send.call_args.kwargs
    assert args == ("@everyone hello",)
    mentions = kwargs["allowed_mentions"]
    assert isinstance(mentions, discord.AllowedMentions)
    assert mentions.everyone is False
    assert mentions.users is False
    assert mentions.roles is False


async def test_dashboard_and_donation(mock_bot, mock_ctx):
    cog = Utility(mock_bot)

    await cog.dashboard.callback(cog, mock_ctx)
    assert mock_ctx.send.call_args.args[0] == "\u200b"
    assert mock_ctx.send.call_args.kwargs.get("view") is not None

    mock_ctx.send.reset_mock()
    await cog.donation.callback(cog, mock_ctx)
    embed = mock_ctx.send.call_args.kwargs["embed"]
    assert embed.title == "Donation Board"
    assert embed.color.value == EMBED_COLOR
    assert len(embed.fields) == 6
    assert embed.footer.text == "Peter Dinklage is a non-profit"
