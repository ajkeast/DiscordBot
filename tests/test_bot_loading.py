"""Verify bot wiring: all cogs load and expected commands register."""

from unittest.mock import MagicMock, patch

import discord.voice_client as voice_client

from cogs.ai import AI
from cogs.dinkcoin import DinkCoin
from cogs.first import First
from cogs.misc import Misc
from cogs.music import Music
from cogs.sentiment import Sentiment
from cogs.server import Server
from cogs.utility import Utility
from tests.conftest import EXPECTED_COMMANDS
from tests.reporting import SECTION_WIRING

COG_CLASSES = (First, DinkCoin, Server, AI, Utility, Misc, Sentiment, Music)


def test_discord_voice_deps_installed(report):
    """discord.py 2.7+ needs PyNaCl and davey before VoiceClient can connect."""
    report.record("has_nacl", True, voice_client.has_nacl, section=SECTION_WIRING)
    report.record("has_dave", True, voice_client.has_dave, section=SECTION_WIRING)
    assert voice_client.has_nacl, "PyNaCl missing; install requirements.txt voice deps"
    assert voice_client.has_dave, "davey missing; install requirements.txt voice deps"


def _server_init_without_tasks(self, bot):
    self.bot = bot


def _sentiment_init_without_tasks(self, bot):
    self.bot = bot
    self._enabled = False


def test_all_cogs_have_expected_names(report):
    expected = {"First", "DinkCoin", "Server", "AI", "Utility", "Misc", "Sentiment", "Music"}
    actual = {cls.__name__ for cls in COG_CLASSES}
    report.record("cog class names", sorted(expected), sorted(actual), section=SECTION_WIRING)
    assert actual == expected


def test_all_commands_registered(report):
    mock_bot = MagicMock()
    registered = set()

    with patch("cogs.ai.GrokClient"):
        with patch.object(Server, "__init__", _server_init_without_tasks):
            with patch.object(Sentiment, "__init__", _sentiment_init_without_tasks):
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
    report.record("command count", len(EXPECTED_COMMANDS), len(registered), section=SECTION_WIRING)
    assert EXPECTED_COMMANDS <= registered
    assert len(EXPECTED_COMMANDS) == 30
