"""Mocked tests for server cog commands."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from cogs.server import Server
from tests.conftest import _make_discord_user


def _make_member(user_id: int, nick=None):
    member = MagicMock()
    member.id = user_id
    member.nick = nick
    return member


def _make_guild(members=None, emojis=None, channels=None):
    guild = MagicMock()
    guild.members = members or []
    guild.emojis = emojis or []
    guild.channels = channels or []
    return guild


def _make_server_cog(mock_bot):
    cog = Server.__new__(Server)
    cog.bot = mock_bot
    return cog


async def test_members(mock_db_ops, mock_bot, mock_ctx):
    member = _make_member(111)
    mock_ctx.guild = _make_guild(members=[member])
    user = _make_discord_user(111)
    user.name = "alice"
    user.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    mock_bot.get_user = MagicMock(return_value=user)

    cog = _make_server_cog(mock_bot)
    await cog.members.callback(cog, mock_ctx)

    mock_db_ops.update_members.assert_called_once()
    rows = mock_db_ops.update_members.call_args.args[0]
    assert len(rows) == 1
    assert rows[0][0] == 111
    assert rows[0][1] == "alice"
    mock_ctx.channel.send.assert_awaited_once_with("Member info successfully updated.")


async def test_emojis(mock_db_ops, mock_bot, mock_ctx):
    emoji = MagicMock()
    emoji.id = 222
    emoji.name = "pepe"
    emoji.guild_id = 333
    emoji.url = "https://cdn.discordapp.com/emojis/222.png"
    emoji.created_at = datetime(2021, 1, 1, tzinfo=timezone.utc)

    mock_ctx.guild = _make_guild(emojis=[emoji])

    cog = _make_server_cog(mock_bot)
    await cog.emojis.callback(cog, mock_ctx)

    mock_db_ops.update_emojis.assert_called_once()
    rows = mock_db_ops.update_emojis.call_args.args[0]
    assert rows[0][1] == "pepe"
    mock_ctx.channel.send.assert_awaited_once_with("Emoji info successfully updated.")


async def test_channels(mock_db_ops, mock_bot, mock_ctx):
    channel = MagicMock()
    channel.id = 444
    channel.name = "general"
    channel.created_at = datetime(2019, 6, 1, tzinfo=timezone.utc)

    mock_ctx.guild = _make_guild(channels=[channel])

    cog = _make_server_cog(mock_bot)
    await cog.channels.callback(cog, mock_ctx)

    mock_db_ops.update_channels.assert_called_once()
    rows = mock_db_ops.update_channels.call_args.args[0]
    assert rows[0][0] == 444
    assert rows[0][1] == "general"
    mock_ctx.channel.send.assert_awaited_once_with("Channel info successfully updated.")
