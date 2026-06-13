"""Mocked tests for misc cog commands."""

import discord

from cogs.misc import Misc


async def test_donation(mock_bot, mock_ctx):
    cog = Misc(mock_bot)
    await cog.donation.callback(cog, mock_ctx)

    mock_ctx.channel.send.assert_awaited_once()
    embed = mock_ctx.channel.send.call_args.kwargs.get("embed") or mock_ctx.channel.send.call_args.args[0]
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Donation Board"
    assert embed.footer.text == "Peter Dinklage is a non-profit"
    assert len(embed.fields) == 6
