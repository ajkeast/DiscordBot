"""Mocked tests for DinkCoin cog commands."""

from unittest.mock import MagicMock

import pandas as pd

from cogs.dinkcoin import DinkCoinCog


async def test_balance(mock_db_ops, mock_bot, mock_ctx):
    mock_db_ops.get_dink_balance.return_value = 3.5
    expected = f"{mock_ctx.author.mention} has **3.5 DINK**"
    cog = DinkCoinCog(mock_bot)

    await cog.balance.callback(cog, mock_ctx)

    mock_db_ops.get_dink_balance.assert_called_once_with(mock_ctx.author.id)
    mock_ctx.send.assert_awaited_once_with(expected)


async def test_ledger_empty(mock_db_ops, mock_bot, mock_ctx):
    mock_db_ops.get_dink_ledger.return_value = pd.DataFrame(columns=["user_id", "balance"])
    mock_db_ops.get_total_dink_circulation.return_value = 0.0
    cog = DinkCoinCog(mock_bot)

    await cog.ledger.callback(cog, mock_ctx)

    embed = mock_ctx.send.call_args.kwargs["embed"]
    assert embed.title == "DinkCoin Ledger"
    assert any("No balances yet" in field.name for field in embed.fields)


async def test_ledger_with_holders(mock_db_ops, mock_bot, mock_ctx):
    mock_db_ops.get_dink_ledger.return_value = pd.DataFrame({
        "user_id": ["111", "222"],
        "balance": [5.0, 2.0],
    })
    mock_db_ops.get_total_dink_circulation.return_value = 7.0
    cog = DinkCoinCog(mock_bot)

    await cog.ledger.callback(cog, mock_ctx)

    embed = mock_ctx.send.call_args.kwargs["embed"]
    assert len(embed.fields) == 2


async def test_pay_success(mock_db_ops, mock_bot, mock_ctx):
    recipient = MagicMock()
    recipient.id = 222222222
    recipient.mention = "<@222222222>"
    recipient.bot = False

    mock_db_ops.get_dink_balance.side_effect = [5.0, 3.0]

    cog = DinkCoinCog(mock_bot)
    await cog.pay.callback(cog, mock_ctx, recipient, 2.0)

    mock_db_ops.record_dink_transfer.assert_called_once_with(
        mock_ctx.author.id, recipient.id, 2.0
    )
    message = mock_ctx.send.call_args.args[0]
    assert "2 DINK" in message


async def test_pay_insufficient_balance(mock_db_ops, mock_bot, mock_ctx):
    recipient = MagicMock()
    recipient.id = 222222222
    recipient.bot = False
    mock_db_ops.get_dink_balance.return_value = 1.0

    cog = DinkCoinCog(mock_bot)
    await cog.pay.callback(cog, mock_ctx, recipient, 2.0)

    message = mock_ctx.send.call_args.args[0]
    assert "Insufficient balance" in message
    mock_db_ops.record_dink_transfer.assert_not_called()


async def test_pay_self(mock_db_ops, mock_bot, mock_ctx):
    mock_ctx.author.bot = False
    cog = DinkCoinCog(mock_bot)
    await cog.pay.callback(cog, mock_ctx, mock_ctx.author, 1.0)

    message = mock_ctx.send.call_args.args[0]
    assert "cannot pay yourself" in message.lower()
    mock_db_ops.record_dink_transfer.assert_not_called()
