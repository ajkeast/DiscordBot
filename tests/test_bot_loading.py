"""Verify bot wiring: all cogs load and expected commands register."""

from unittest.mock import MagicMock, patch

from cogs.ai import AI
from cogs.dinkcoin import DinkCoin
from cogs.first import First
from cogs.misc import Misc
from cogs.server import Server
from cogs.utility import Utility
from tests.conftest import EXPECTED_COMMANDS
from tests.reporting import SECTION_WIRING

COG_CLASSES = (First, DinkCoin, Server, AI, Utility, Misc)


def _server_init_without_tasks(self, bot):
    self.bot = bot


def test_all_cogs_have_expected_names(report):
    expected = {"First", "DinkCoin", "Server", "AI", "Utility", "Misc"}
    actual = {cls.__name__ for cls in COG_CLASSES}
    report.record("cog class names", sorted(expected), sorted(actual), section=SECTION_WIRING)
    assert actual == expected


def test_all_commands_registered(report):
    mock_bot = MagicMock()
    registered = set()

    with patch("cogs.ai.GrokClient"):
        with patch.object(Server, "__init__", _server_init_without_tasks):
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
    assert len(EXPECTED_COMMANDS) == 20
