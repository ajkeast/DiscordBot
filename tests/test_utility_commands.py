"""Mocked tests for utility cog commands."""

from cogs.utility import Utility


async def test_hello(mock_bot, mock_ctx):
    cog = Utility(mock_bot)
    await cog.hello.callback(cog, mock_ctx)
    mock_ctx.channel.send.assert_awaited_once_with("Hello <@123456789>!")


async def test_ping(mock_bot, mock_ctx):
    cog = Utility(mock_bot)
    await cog.ping.callback(cog, mock_ctx)
    mock_ctx.channel.send.assert_awaited_once_with("pong")


async def test_simonsays(mock_bot, mock_ctx):
    cog = Utility(mock_bot)
    await cog.simonsays.callback(cog, mock_ctx, arg="repeat this")
    mock_ctx.channel.send.assert_awaited_once_with("repeat this")
