"""Mocked tests for first cog commands."""

from datetime import datetime
from unittest.mock import patch

import pandas as pd
import pytz

from cogs.first import First
from utils.constants import GENERAL_CHANNEL_ID


def _est_today_df(date_str: str, user_id: str = "999") -> pd.DataFrame:
    """Build a df whose most recent entry is on the given Eastern date."""
    utc_ts = pytz.timezone("US/Eastern").localize(
        datetime.strptime(date_str, "%Y-%m-%d").replace(hour=12)
    ).astimezone(pytz.UTC).replace(tzinfo=None)
    return pd.DataFrame({
        "user_id": [user_id],
        "timesent": pd.to_datetime([utc_ts]),
    })


async def test_first_wrong_channel(mock_bot, mock_ctx):
    mock_ctx.channel.id = 111111111
    cog = First(mock_bot)

    await cog.first.callback(cog, mock_ctx)

    mock_ctx.channel.send.assert_awaited_once_with(
        f"Please send your message to <#{GENERAL_CHANNEL_ID}>."
    )


@patch("cogs.first.time.sleep")
@patch("cogs.first.datetime")
async def test_first_successful_claim(
    mock_datetime, _mock_sleep, mock_db_ops, mock_bot, mock_ctx,
):
    est = pytz.timezone("US/Eastern")
    today = est.localize(datetime(2024, 6, 11, 8, 0, 0))

    mock_datetime.utcnow.return_value = today.astimezone(pytz.UTC).replace(tzinfo=None)
    mock_db_ops.get_table_data.return_value = _est_today_df("2024-06-10")

    cog = First(mock_bot)
    await cog.first.callback(cog, mock_ctx)

    mock_db_ops.write_first_entry.assert_called_once_with(mock_ctx.author.id)
    mock_ctx.channel.send.assert_awaited_once_with(f"{mock_ctx.author.mention} is first today! 🥳")


@patch("cogs.first.datetime")
async def test_first_already_claimed_today(mock_datetime, mock_db_ops, mock_bot, mock_ctx):
    est = pytz.timezone("US/Eastern")
    today = est.localize(datetime(2024, 6, 11, 8, 0, 0))

    mock_datetime.utcnow.return_value = today.astimezone(pytz.UTC).replace(tzinfo=None)
    mock_db_ops.get_table_data.return_value = _est_today_df("2024-06-11")

    cog = First(mock_bot)
    await cog.first.callback(cog, mock_ctx)

    mock_db_ops.write_first_entry.assert_not_called()
    mock_ctx.channel.send.assert_awaited_once_with(
        f"Sorry {mock_ctx.author.mention}, first has already been claimed today. 😭"
    )


async def test_score(mock_db_ops, mock_bot, mock_ctx, leaderboard_first_df):
    mock_db_ops.get_table_data.return_value = leaderboard_first_df
    cog = First(mock_bot)

    await cog.score.callback(cog, mock_ctx)

    mock_ctx.channel.send.assert_awaited_once()
    embed = mock_ctx.channel.send.call_args.kwargs["embed"]
    assert embed.title == "First Leaderboard"
    assert len(embed.fields) == 5


async def test_stats_self(mock_db_ops, mock_bot, mock_ctx, sample_first_df):
    mock_db_ops.get_table_data.return_value = sample_first_df
    mock_ctx.author.id = 111
    mock_ctx.message.author.id = 111
    cog = First(mock_bot)

    await cog.stats.callback(cog, mock_ctx)

    mock_ctx.channel.send.assert_awaited_once()
    embed = mock_ctx.channel.send.call_args.kwargs["embed"]
    assert embed.title is not None
    assert len(embed.fields) == 3


async def test_stats_no_entries(mock_db_ops, mock_bot, mock_ctx):
    mock_db_ops.get_table_data.return_value = pd.DataFrame({
        "user_id": ["999"],
        "timesent": pd.to_datetime(["2024-01-01 12:00:00"]),
    })
    cog = First(mock_bot)

    await cog.stats.callback(cog, mock_ctx)

    mock_ctx.channel.send.assert_awaited_once_with("This user has never gotten a first!")


async def test_juice(mock_db_ops, mock_bot, mock_ctx, leaderboard_first_df):
    mock_db_ops.get_table_data.return_value = leaderboard_first_df
    cog = First(mock_bot)

    await cog.juice.callback(cog, mock_ctx)

    mock_ctx.channel.send.assert_awaited_once()
    embed = mock_ctx.channel.send.call_args.kwargs["embed"]
    assert embed.title == "Juice Board 🧃"


async def test_graph_empty(mock_db_ops, mock_bot, mock_ctx, empty_first_df):
    mock_db_ops.get_table_data.return_value = empty_first_df
    cog = First(mock_bot)

    await cog.graph.callback(cog, mock_ctx)

    mock_ctx.send.assert_awaited_once()
    assert "No firsts recorded yet" in mock_ctx.send.call_args.args[0]


async def test_graph_with_data(mock_db_ops, mock_bot, mock_ctx, sample_first_df):
    mock_db_ops.get_table_data.return_value = sample_first_df
    cog = First(mock_bot)

    await cog.graph.callback(cog, mock_ctx)

    mock_ctx.send.assert_awaited_once()
    assert mock_ctx.send.call_args.kwargs.get("file") is not None
    assert mock_ctx.send.call_args.kwargs.get("embed") is not None


async def test_juicegraph_with_data(mock_db_ops, mock_bot, mock_ctx, sample_first_df):
    mock_db_ops.get_table_data.return_value = sample_first_df
    cog = First(mock_bot)

    await cog.juicegraph.callback(cog, mock_ctx)

    mock_ctx.send.assert_awaited_once()
    assert mock_ctx.send.call_args.kwargs.get("file") is not None
