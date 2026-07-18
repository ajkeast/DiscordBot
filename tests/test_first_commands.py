"""Mocked tests for first cog commands."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytz

from cogs.first import First
from tests.reporting import SECTION_COMMANDS
from utils.constants import DINKSCORD_URL, GENERAL_CHANNEL_ID


def _expected_first_win(mention: str) -> str:
    return (
        f"{mention} is first today! 🥳 +1 **DINK**\n"
        f"Check out the recently launched website for Peter Dinklage: {DINKSCORD_URL}"
    )


def _est_today_df(date_str: str, user_id: str = "999") -> pd.DataFrame:
    """Build a df whose most recent entry is on the given Eastern date."""
    utc_ts = pytz.timezone("US/Eastern").localize(
        datetime.strptime(date_str, "%Y-%m-%d").replace(hour=12)
    ).astimezone(pytz.UTC).replace(tzinfo=None)
    return pd.DataFrame({
        "user_id": [user_id],
        "timesent": pd.to_datetime([utc_ts]),
    })


async def test_first_wrong_channel(report, mock_bot, mock_ctx):
    expected = f"Please send your message to <#{GENERAL_CHANNEL_ID}>."
    mock_ctx.channel.id = 111111111
    cog = First(mock_bot)

    await cog.first.callback(cog, mock_ctx)

    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)


@patch("cogs.first.asyncio.sleep", new_callable=AsyncMock)
@patch("cogs.first.datetime")
async def test_first_successful_claim(
    mock_datetime, _mock_sleep, report, mock_db_ops, mock_bot, mock_ctx,
):
    expected = _expected_first_win(mock_ctx.author.mention)
    est = pytz.timezone("US/Eastern")
    today = est.localize(datetime(2024, 6, 11, 8, 0, 0))

    mock_datetime.utcnow.return_value = today.astimezone(pytz.UTC).replace(tzinfo=None)
    mock_db_ops.get_table_data.return_value = _est_today_df("2024-06-10")

    cog = First(mock_bot)
    await cog.first.callback(cog, mock_ctx)

    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    report.record("db write", mock_ctx.author.id, mock_db_ops.write_first_entry.call_args.args[0], section=SECTION_COMMANDS)

    mock_db_ops.write_first_entry.assert_called_once_with(mock_ctx.author.id)
    mock_db_ops.record_dink_mint.assert_called_once_with(mock_ctx.author.id, 1.0)
    mock_ctx.send.assert_awaited_once()
    assert actual == expected
    assert mock_ctx.send.call_args.kwargs.get("view") is not None


@patch("cogs.first.datetime")
async def test_first_already_claimed_today(mock_datetime, report, mock_db_ops, mock_bot, mock_ctx):
    expected = f"Sorry {mock_ctx.author.mention}, first has already been claimed today. 😭"
    est = pytz.timezone("US/Eastern")
    today = est.localize(datetime(2024, 6, 11, 8, 0, 0))

    mock_datetime.utcnow.return_value = today.astimezone(pytz.UTC).replace(tzinfo=None)
    mock_db_ops.get_table_data.return_value = _est_today_df("2024-06-11")

    cog = First(mock_bot)
    await cog.first.callback(cog, mock_ctx)

    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    report.record("db write", "not called", mock_db_ops.write_first_entry.called, section=SECTION_COMMANDS)

    mock_db_ops.write_first_entry.assert_not_called()
    mock_ctx.send.assert_awaited_once_with(expected)


async def test_score(report, mock_db_ops, mock_bot, mock_ctx, leaderboard_first_df):
    mock_db_ops.get_table_data.return_value = leaderboard_first_df
    cog = First(mock_bot)

    await cog.score.callback(cog, mock_ctx)

    embed = mock_ctx.send.call_args.kwargs["embed"]
    report.record("embed title", "First Leaderboard", embed.title, section=SECTION_COMMANDS)
    report.record("embed field count", 5, len(embed.fields), section=SECTION_COMMANDS)

    mock_ctx.send.assert_awaited_once()
    assert embed.title == "First Leaderboard"
    assert len(embed.fields) == 5


async def test_stats_self(report, mock_db_ops, mock_bot, mock_ctx, sample_first_df):
    mock_db_ops.get_table_data.return_value = sample_first_df
    mock_ctx.author.id = 111
    mock_ctx.message.author.id = 111
    cog = First(mock_bot)

    await cog.stats.callback(cog, mock_ctx)

    embed = mock_ctx.send.call_args.kwargs["embed"]
    report.record("embed field count", 3, len(embed.fields), section=SECTION_COMMANDS)
    report.record("embed has title", True, embed.title is not None, section=SECTION_COMMANDS)

    mock_ctx.send.assert_awaited_once()
    assert embed.title is not None
    assert len(embed.fields) == 3


async def test_stats_no_entries(report, mock_db_ops, mock_bot, mock_ctx):
    expected = "This user has never gotten a first!"
    mock_db_ops.get_table_data.return_value = pd.DataFrame({
        "user_id": ["999"],
        "timesent": pd.to_datetime(["2024-01-01 12:00:00"]),
    })
    cog = First(mock_bot)

    await cog.stats.callback(cog, mock_ctx)

    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)


async def test_juice(report, mock_db_ops, mock_bot, mock_ctx, leaderboard_first_df):
    mock_db_ops.get_table_data.return_value = leaderboard_first_df
    cog = First(mock_bot)

    await cog.juice.callback(cog, mock_ctx)

    embed = mock_ctx.send.call_args.kwargs["embed"]
    report.record("embed title", "Juice Board 🧃", embed.title, section=SECTION_COMMANDS)

    mock_ctx.send.assert_awaited_once()
    assert embed.title == "Juice Board 🧃"


@patch("cogs.first.asyncio.sleep", new_callable=AsyncMock)
@patch("cogs.first.datetime")
async def test_first_empty_table_allows_claim(
    mock_datetime, _mock_sleep, report, mock_db_ops, mock_bot, mock_ctx, empty_first_df,
):
    expected = _expected_first_win(mock_ctx.author.mention)
    est = pytz.timezone("US/Eastern")
    today = est.localize(datetime(2024, 6, 11, 8, 0, 0))

    mock_datetime.utcnow.return_value = today.astimezone(pytz.UTC).replace(tzinfo=None)
    mock_db_ops.get_table_data.return_value = empty_first_df

    cog = First(mock_bot)
    await cog.first.callback(cog, mock_ctx)

    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    report.record("db write", mock_ctx.author.id, mock_db_ops.write_first_entry.call_args.args[0], section=SECTION_COMMANDS)

    mock_db_ops.write_first_entry.assert_called_once_with(mock_ctx.author.id)
    mock_db_ops.record_dink_mint.assert_called_once_with(mock_ctx.author.id, 1.0)
    mock_ctx.send.assert_awaited_once()
    assert actual == expected
    assert mock_ctx.send.call_args.kwargs.get("view") is not None


async def test_score_empty(report, mock_db_ops, mock_bot, mock_ctx, empty_first_df):
    expected = "No firsts recorded yet — claim one with `/1st`!"
    mock_db_ops.get_table_data.return_value = empty_first_df
    cog = First(mock_bot)

    await cog.score.callback(cog, mock_ctx)

    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)


async def test_score_fewer_than_five_winners(report, mock_db_ops, mock_bot, mock_ctx, sample_first_df):
    mock_db_ops.get_table_data.return_value = sample_first_df
    cog = First(mock_bot)

    await cog.score.callback(cog, mock_ctx)

    embed = mock_ctx.send.call_args.kwargs["embed"]
    # sample_first_df has 2 distinct users
    report.record("embed field count", 2, len(embed.fields), section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once()
    assert embed.title == "First Leaderboard"
    assert len(embed.fields) == 2


async def test_juice_empty(report, mock_db_ops, mock_bot, mock_ctx, empty_first_df):
    expected = "No firsts recorded yet — claim one with `/1st`!"
    mock_db_ops.get_table_data.return_value = empty_first_df
    cog = First(mock_bot)

    await cog.juice.callback(cog, mock_ctx)

    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)


async def test_juice_fewer_than_five_winners(report, mock_db_ops, mock_bot, mock_ctx, sample_first_df):
    mock_db_ops.get_table_data.return_value = sample_first_df
    cog = First(mock_bot)

    await cog.juice.callback(cog, mock_ctx)

    embed = mock_ctx.send.call_args.kwargs["embed"]
    report.record("embed field count", 2, len(embed.fields), section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once()
    assert embed.title == "Juice Board 🧃"
    assert len(embed.fields) == 2


async def test_graph_empty(report, mock_db_ops, mock_bot, mock_ctx, empty_first_df):
    expected = "No firsts recorded yet — claim one with `/1st`!"
    mock_db_ops.get_table_data.return_value = empty_first_df
    cog = First(mock_bot)

    await cog.graph.callback(cog, mock_ctx)

    message = mock_ctx.send.call_args.args[0]
    report.record("message", expected, message, section=SECTION_COMMANDS)

    mock_ctx.send.assert_awaited_once_with(expected)
    assert message == expected


async def test_graph_with_data(report, mock_db_ops, mock_bot, mock_ctx, sample_first_df):
    mock_db_ops.get_table_data.return_value = sample_first_df
    cog = First(mock_bot)

    await cog.graph.callback(cog, mock_ctx)

    sent_file = mock_ctx.send.call_args.kwargs.get("file")
    sent_embed = mock_ctx.send.call_args.kwargs.get("embed")
    report.record("attachment sent", True, sent_file is not None, section=SECTION_COMMANDS)
    report.record("embed sent", True, sent_embed is not None, section=SECTION_COMMANDS)

    mock_ctx.send.assert_awaited_once()
    assert sent_file is not None
    assert sent_embed is not None


async def test_juicegraph_with_data(report, mock_db_ops, mock_bot, mock_ctx, sample_first_df):
    mock_db_ops.get_table_data.return_value = sample_first_df
    cog = First(mock_bot)

    await cog.juicegraph.callback(cog, mock_ctx)

    sent_file = mock_ctx.send.call_args.kwargs.get("file")
    report.record("attachment sent", True, sent_file is not None, section=SECTION_COMMANDS)

    mock_ctx.send.assert_awaited_once()
    assert sent_file is not None
