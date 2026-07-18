"""Mocked tests for utility cog commands."""

from cogs.utility import Utility
from tests.reporting import SECTION_COMMANDS
from utils.constants import DINKSCORD_URL


async def test_hello(report, mock_bot, mock_ctx):
    expected = "Hello <@123456789>!"
    cog = Utility(mock_bot)
    await cog.hello.callback(cog, mock_ctx)
    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)


async def test_ping(report, mock_bot, mock_ctx):
    expected = "pong"
    cog = Utility(mock_bot)
    await cog.ping.callback(cog, mock_ctx)
    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)


async def test_simonsays(report, mock_bot, mock_ctx):
    expected = "repeat this"
    cog = Utility(mock_bot)
    await cog.simonsays.callback(cog, mock_ctx, message=expected)
    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)


async def test_dashboard(report, mock_bot, mock_ctx):
    expected = f"Check out the Dinkscord dashboard: {DINKSCORD_URL}"
    cog = Utility(mock_bot)
    await cog.dashboard.callback(cog, mock_ctx)
    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once()
    assert actual == expected
    assert mock_ctx.send.call_args.kwargs.get("view") is not None
