"""Mocked tests for DinkCoin cog commands."""

from unittest.mock import AsyncMock, MagicMock

import pandas as pd

from cogs.dinkcoin import DinkCoin, DinkRequestView


async def test_balance(mock_db_ops, mock_bot, mock_ctx):
    mock_db_ops.get_dink_balance.return_value = 3.5
    expected = f"{mock_ctx.author.mention} has **3.5 DINK**"
    cog = DinkCoin(mock_bot)

    await cog.balance.callback(cog, mock_ctx)

    mock_db_ops.get_dink_balance.assert_called_once_with(mock_ctx.author.id)
    mock_ctx.send.assert_awaited_once_with(expected)


async def test_ledger_empty(mock_db_ops, mock_bot, mock_ctx):
    mock_db_ops.get_dink_ledger.return_value = pd.DataFrame(columns=["user_id", "balance"])
    mock_db_ops.get_total_dink_circulation.return_value = 0.0
    cog = DinkCoin(mock_bot)

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
    cog = DinkCoin(mock_bot)

    await cog.ledger.callback(cog, mock_ctx)

    embed = mock_ctx.send.call_args.kwargs["embed"]
    assert len(embed.fields) == 2


async def test_pay_success(mock_db_ops, mock_bot, mock_ctx):
    recipient = MagicMock()
    recipient.id = 222222222
    recipient.mention = "<@222222222>"
    recipient.bot = False

    mock_db_ops.get_dink_balance.side_effect = [5.0, 3.0]

    cog = DinkCoin(mock_bot)
    await cog.pay.callback(cog, mock_ctx, recipient, 2)

    mock_db_ops.record_dink_transfer.assert_called_once_with(
        mock_ctx.author.id, recipient.id, 2
    )
    message = mock_ctx.send.call_args.args[0]
    assert "2 DINK" in message


async def test_pay_insufficient_balance(mock_db_ops, mock_bot, mock_ctx):
    recipient = MagicMock()
    recipient.id = 222222222
    recipient.bot = False
    mock_db_ops.get_dink_balance.return_value = 1.0

    cog = DinkCoin(mock_bot)
    await cog.pay.callback(cog, mock_ctx, recipient, 2)

    message = mock_ctx.send.call_args.args[0]
    assert "Insufficient balance" in message
    mock_db_ops.record_dink_transfer.assert_not_called()


async def test_pay_fractional_amount(mock_db_ops, mock_bot, mock_ctx):
    recipient = MagicMock()
    recipient.id = 222222222
    recipient.bot = False
    cog = DinkCoin(mock_bot)

    await cog.pay.callback(cog, mock_ctx, recipient, 1.5)

    message = mock_ctx.send.call_args.args[0]
    assert "Only whole DINK coins" in message
    mock_db_ops.record_dink_transfer.assert_not_called()


async def test_pay_self(mock_db_ops, mock_bot, mock_ctx):
    mock_ctx.author.bot = False
    cog = DinkCoin(mock_bot)
    await cog.pay.callback(cog, mock_ctx, mock_ctx.author, 1)

    message = mock_ctx.send.call_args.args[0]
    assert "cannot pay yourself" in message.lower()
    mock_db_ops.record_dink_transfer.assert_not_called()


def _make_member(user_id: int, mention: str | None = None, bot: bool = False):
    member = MagicMock()
    member.id = user_id
    member.mention = mention or f"<@{user_id}>"
    member.bot = bot
    return member


async def test_request_sends_buttons(mock_db_ops, mock_bot, mock_ctx):
    payer = _make_member(222222222)
    sent_message = MagicMock()
    mock_ctx.send = AsyncMock(return_value=sent_message)
    cog = DinkCoin(mock_bot)

    await cog.request.callback(cog, mock_ctx, payer, 2)

    args, kwargs = mock_ctx.send.call_args
    message = args[0]
    view = kwargs["view"]
    assert "requesting" in message
    assert "2 DINK" in message
    assert isinstance(view, DinkRequestView)
    assert view.payer_id == payer.id
    assert view.requester_id == mock_ctx.author.id
    assert view.amount == 2
    assert view.message is sent_message
    labels = [item.label for item in view.children]
    assert labels == ["Accept", "Decline"]
    mock_db_ops.record_dink_transfer.assert_not_called()


async def test_request_self(mock_db_ops, mock_bot, mock_ctx):
    cog = DinkCoin(mock_bot)
    await cog.request.callback(cog, mock_ctx, mock_ctx.author, 1)

    message = mock_ctx.send.call_args.args[0]
    assert "yourself" in message.lower()
    mock_db_ops.record_dink_transfer.assert_not_called()


async def test_request_fractional_amount(mock_db_ops, mock_bot, mock_ctx):
    payer = _make_member(222222222)
    cog = DinkCoin(mock_bot)

    await cog.request.callback(cog, mock_ctx, payer, 1.5)

    message = mock_ctx.send.call_args.args[0]
    assert "Only whole DINK coins" in message
    mock_db_ops.record_dink_transfer.assert_not_called()


async def test_request_accept_transfers(mock_db_ops, mock_bot, mock_ctx):
    requester = _make_member(111111111)
    payer = _make_member(222222222)
    view = DinkRequestView(requester, payer, 2)
    mock_db_ops.get_dink_balance.return_value = 5.0

    interaction = AsyncMock()
    interaction.user = payer
    interaction.response = AsyncMock()

    await view.accept.callback(interaction)

    mock_db_ops.record_dink_transfer.assert_called_once_with(payer.id, requester.id, 2)
    interaction.response.edit_message.assert_awaited_once()
    content = interaction.response.edit_message.call_args.kwargs["content"]
    assert "accepted" in content
    assert all(item.disabled for item in view.children)


async def test_request_accept_insufficient_balance(mock_db_ops, mock_bot, mock_ctx):
    requester = _make_member(111111111)
    payer = _make_member(222222222)
    view = DinkRequestView(requester, payer, 2)
    mock_db_ops.get_dink_balance.return_value = 1.0

    interaction = AsyncMock()
    interaction.user = payer
    interaction.response = AsyncMock()

    await view.accept.callback(interaction)

    mock_db_ops.record_dink_transfer.assert_not_called()
    interaction.response.send_message.assert_awaited_once()
    message = interaction.response.send_message.call_args.args[0]
    assert "Insufficient balance" in message
    assert all(not item.disabled for item in view.children)


async def test_request_decline(mock_db_ops, mock_bot, mock_ctx):
    requester = _make_member(111111111)
    payer = _make_member(222222222)
    view = DinkRequestView(requester, payer, 2)

    interaction = AsyncMock()
    interaction.user = payer
    interaction.response = AsyncMock()

    await view.decline.callback(interaction)

    mock_db_ops.record_dink_transfer.assert_not_called()
    content = interaction.response.edit_message.call_args.kwargs["content"]
    assert "declined" in content
    assert all(item.disabled for item in view.children)


async def test_request_wrong_user_blocked(mock_db_ops, mock_bot, mock_ctx):
    requester = _make_member(111111111)
    payer = _make_member(222222222)
    outsider = _make_member(333333333)
    view = DinkRequestView(requester, payer, 2)

    interaction = AsyncMock()
    interaction.user = outsider
    interaction.response = AsyncMock()

    allowed = await view.interaction_check(interaction)

    assert allowed is False
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True
