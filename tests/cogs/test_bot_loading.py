"""Verify bot wiring: cogs initialize cleanly and register the exact command set."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord.voice_client as voice_client
from discord.ext import commands

from bot import EXTENSIONS, DinkBot
from cogs.ai import AI
from cogs.dinkcoin import DinkCoin
from cogs.first import First
from cogs.minecraft import Minecraft
from cogs.music import Music
from cogs.sentiment import Sentiment
from cogs.server import Server
from cogs.utility import Utility
from tests.conftest import EXPECTED_COMMANDS
from tests.reporting import SECTION_WIRING

COG_CLASSES = (First, DinkCoin, Server, AI, Utility, Sentiment, Music, Minecraft)


def test_discord_voice_deps_installed(report):
    report.record("has_nacl", True, voice_client.has_nacl, section=SECTION_WIRING)
    report.record("has_dave", True, voice_client.has_dave, section=SECTION_WIRING)
    assert voice_client.has_nacl
    assert voice_client.has_dave


def test_bot_extensions_list_matches_cogs():
    assert set(EXTENSIONS) == {
        "cogs.first",
        "cogs.dinkcoin",
        "cogs.server",
        "cogs.ai",
        "cogs.utility",
        "cogs.sentiment",
        "cogs.music",
        "cogs.minecraft",
    }


def test_all_cogs_construct_without_starting_tasks_and_register_exact_commands(report):
    mock_bot = MagicMock()
    registered = set()

    with patch("cogs.ai.GrokChatService"):
        for cls in COG_CLASSES:
            cog = cls(mock_bot)
            for cmd in cog.get_commands():
                registered.add(cmd.name)

    report.record(
        "registered commands",
        sorted(EXPECTED_COMMANDS),
        sorted(registered),
        section=SECTION_WIRING,
    )
    assert registered == EXPECTED_COMMANDS
    assert len(registered) == 32


async def test_on_command_error_uses_generic_cooldown_and_guild_only_messages():
    bot = DinkBot.__new__(DinkBot)
    ctx = AsyncMock()
    ctx.command = MagicMock(name="ping")

    await bot.on_command_error(ctx, commands.NoPrivateMessage())
    assert "only works in a server" in ctx.send.call_args.args[0].lower()

    ctx.send.reset_mock()
    cooldown = commands.Cooldown(1, 120.0)
    await bot.on_command_error(
        ctx, commands.CommandOnCooldown(cooldown, 125.0, commands.BucketType.user)
    )
    msg = ctx.send.call_args.args[0]
    assert "/imagine" not in msg
    assert "3 minutes" in msg
