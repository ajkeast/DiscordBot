"""Mocked tests for misc cog commands."""

import discord

from cogs.misc import Misc
from tests.reporting import SECTION_COMMANDS


async def test_donation(report, mock_bot, mock_ctx):
    cog = Misc(mock_bot)
    await cog.donation.callback(cog, mock_ctx)

    embed = mock_ctx.send.call_args.kwargs.get("embed") or mock_ctx.send.call_args.args[0]
    report.record("embed title", "Donation Board", embed.title, section=SECTION_COMMANDS)
    report.record("embed field count", 6, len(embed.fields), section=SECTION_COMMANDS)
    report.record("footer", "Peter Dinklage is a non-profit", embed.footer.text, section=SECTION_COMMANDS)

    mock_ctx.send.assert_awaited_once()
    assert isinstance(embed, discord.Embed)
    assert embed.title == "Donation Board"
    assert embed.footer.text == "Peter Dinklage is a non-profit"
    assert len(embed.fields) == 6
