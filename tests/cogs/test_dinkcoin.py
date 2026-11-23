"""Tests for DinkCoin commands and persistent DB-backed request buttons."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.dinkcoin import DinkCoin, DinkRequestButton
from db.dinkcoin import (
    DinkRequestRecord,
    InsufficientBalanceError,
    LedgerEntry,
    RequestStateError,
)
from tests.conftest import make_discord_user


@pytest.fixture
def dink_cog(mock_bot):
    repo = MagicMock()
    return DinkCoin(mock_bot, repo=repo)


async def test_balance(dink_cog, mock_ctx):
    dink_cog.repo.get_balance.return_value = 3.5
    await dink_cog.balance.callback(dink_cog, mock_ctx)
    dink_cog.repo.get_balance.assert_called_once_with(mock_ctx.author.id)
    mock_ctx.send.assert_awaited_once_with(f"{mock_ctx.author.mention} has **3.5 DINK**")


async def test_ledger_empty_and_populated(dink_cog, mock_ctx):
    dink_cog.repo.get_ledger.return_value = []
    dink_cog.repo.get_total_circulation.return_value = 0.0
    await dink_cog.ledger.callback(dink_cog, mock_ctx)
    embed = mock_ctx.send.call_args.kwargs["embed"]
    assert any("No balances yet" in f.name for f in embed.fields)

    mock_ctx.send.reset_mock()
    dink_cog.repo.get_ledger.return_value = [
        LedgerEntry("111", 5.0),
        LedgerEntry("222", 2.0),
    ]
    dink_cog.repo.get_total_circulation.return_value = 7.0
    await dink_cog.ledger.callback(dink_cog, mock_ctx)
    embed = mock_ctx.send.call_args.kwargs["embed"]
    assert len(embed.fields) == 2


async def test_pay_success_and_validation(dink_cog, mock_ctx):
    recipient = make_discord_user(222222222)
    dink_cog.repo.transfer.return_value = 3.0

    await dink_cog.pay.callback(dink_cog, mock_ctx, recipient, 2)
    dink_cog.repo.transfer.assert_called_once_with(mock_ctx.author.id, recipient.id, 2)
    assert "new balance: **3 DINK**" in mock_ctx.send.call_args.args[0]

    # Fractional rejected before DB call
    dink_cog.repo.transfer.reset_mock()
    await dink_cog.pay.callback(dink_cog, mock_ctx, recipient, 1.5)
    dink_cog.repo.transfer.assert_not_called()

    # Insufficient balance
    dink_cog.repo.transfer.side_effect = InsufficientBalanceError("Insufficient")
    dink_cog.repo.get_balance.return_value = 1.0
    await dink_cog.pay.callback(dink_cog, mock_ctx, recipient, 5)
    assert "Insufficient balance" in mock_ctx.send.call_args.args[0]


async def test_request_creates_persistent_button_view(dink_cog, mock_ctx):
    payer = make_discord_user(222222222)
    dink_cog.repo.create_request.return_value = 42

    await dink_cog.request.callback(dink_cog, mock_ctx, payer, 2)

    dink_cog.repo.create_request.assert_called_once()
    view = mock_ctx.send.call_args.kwargs["view"]
    assert view.timeout is None
    custom_ids = [item.custom_id for item in view.children]
    assert custom_ids == ["dink:accept:42", "dink:decline:42"]


async def test_persistent_accept_decline_and_error_states(dink_cog):
    payer = make_discord_user(222222222)
    expires = datetime.now(timezone.utc) + timedelta(hours=24)

    interaction = AsyncMock()
    interaction.user = payer
    interaction.client = MagicMock()
    interaction.client.get_cog.return_value = dink_cog
    interaction.response = AsyncMock()

    dink_cog.repo.accept_request.return_value = DinkRequestRecord(
        id=42, requester_id=111, payer_id=222222222, amount=2, status="accepted", expires_at=expires
    )
    btn = DinkRequestButton("accept", 42)
    await btn.callback(interaction)
    assert "accepted" in interaction.response.edit_message.call_args.kwargs["content"]

    # Wrong user / already handled
    interaction.response.reset_mock()
    dink_cog.repo.decline_request.side_effect = RequestStateError(
        "Only the requested user can respond to this."
    )
    decline_btn = DinkRequestButton("decline", 42)
    await decline_btn.callback(interaction)
    interaction.response.send_message.assert_awaited_once_with(
        "Only the requested user can respond to this.", ephemeral=True
    )

    # Expired request updates message cleanly
    interaction.response.reset_mock()
    dink_cog.repo.accept_request.side_effect = RequestStateError(
        "This DINK request has expired."
    )
    dink_cog.repo.get_request.return_value = DinkRequestRecord(
        id=42, requester_id=111, payer_id=222222222, amount=2, status="expired", expires_at=expires
    )
    await btn.callback(interaction)
    assert "Request expired" in interaction.response.edit_message.call_args.kwargs["content"]
