"""DinkCoin balances, ledger, transfers, and persistent payment requests."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from config import EMBED_COLOR
from db.dinkcoin import (
    DinkCoinRepository,
    InsufficientBalanceError,
    RequestStateError,
)
from db.pool import DatabaseError
from utils.discord_helpers import hybrid_command, resolve_display_name

logger = logging.getLogger(__name__)

REQUEST_TTL = timedelta(hours=24)


def _validate_transfer(
    author: discord.abc.User,
    member: discord.Member,
    amount: float,
    *,
    action: str,
) -> tuple[int | None, str | None]:
    if member.bot:
        if action == "pay":
            return None, "You cannot pay bots."
        return None, "You cannot request DINK from bots."
    if member.id == author.id:
        if action == "pay":
            return None, "You cannot pay yourself."
        return None, "You cannot request DINK from yourself."
    if amount <= 0:
        return None, "Amount must be greater than zero."
    if amount != int(amount):
        return None, "Only whole DINK coins can be transferred."
    return int(amount), None


def _disabled_request_view(request_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Accept",
            style=discord.ButtonStyle.primary,
            custom_id=f"dink:accept:{request_id}",
            disabled=True,
        )
    )
    view.add_item(
        discord.ui.Button(
            label="Decline",
            style=discord.ButtonStyle.danger,
            custom_id=f"dink:decline:{request_id}",
            disabled=True,
        )
    )
    return view


class DinkRequestButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"dink:(?P<action>accept|decline):(?P<request_id>\d+)",
):
    """Persistent DB-backed Accept/Decline button that survives bot restarts."""

    def __init__(self, action: str, request_id: int) -> None:
        self.action = action
        self.request_id = request_id
        style = (
            discord.ButtonStyle.primary
            if action == "accept"
            else discord.ButtonStyle.danger
        )
        super().__init__(
            discord.ui.Button(
                label="Accept" if action == "accept" else "Decline",
                style=style,
                custom_id=f"dink:{action}:{request_id}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item,
        match: re.Match[str],
    ) -> DinkRequestButton:
        return cls(action=match.group("action"), request_id=int(match.group("request_id")))

    def _repo(self, interaction: discord.Interaction) -> DinkCoinRepository:
        get_cog = getattr(interaction.client, "get_cog", None)
        cog = get_cog("DinkCoin") if callable(get_cog) else None
        repo = getattr(cog, "repo", None) if cog is not None else None
        return repo or DinkCoinRepository()

    async def callback(self, interaction: discord.Interaction) -> None:
        repo = self._repo(interaction)
        try:
            if self.action == "accept":
                record = await asyncio.to_thread(
                    repo.accept_request, self.request_id, interaction.user.id
                )
                await interaction.response.edit_message(
                    content=(
                        f"<@{record.payer_id}> accepted and sent "
                        f"**{record.amount:g} DINK** to <@{record.requester_id}>!"
                    ),
                    view=_disabled_request_view(self.request_id),
                )
            else:
                record = await asyncio.to_thread(
                    repo.decline_request, self.request_id, interaction.user.id
                )
                await interaction.response.edit_message(
                    content=(
                        f"<@{record.payer_id}> declined <@{record.requester_id}>'s "
                        f"request for **{record.amount:g} DINK**."
                    ),
                    view=_disabled_request_view(self.request_id),
                )
        except InsufficientBalanceError:
            balance = await asyncio.to_thread(repo.get_balance, interaction.user.id)
            await interaction.response.send_message(
                f"Insufficient balance. You have **{balance:g} DINK**.",
                ephemeral=True,
            )
        except RequestStateError as exc:
            msg = str(exc)
            if "expired" in msg.lower():
                expired = await asyncio.to_thread(repo.get_request, self.request_id)
                if expired is not None:
                    await interaction.response.edit_message(
                        content=(
                            f"Request expired: <@{expired.requester_id}> asked "
                            f"<@{expired.payer_id}> for **{expired.amount:g} DINK**."
                        ),
                        view=_disabled_request_view(self.request_id),
                    )
                    return
            await interaction.response.send_message(msg, ephemeral=True)
        except DatabaseError:
            await interaction.response.send_message(
                "Transfer failed. Please try again later.",
                ephemeral=True,
            )
        except Exception:
            logger.exception("DinkCoin request callback failed")
            await interaction.response.send_message(
                "Transfer failed. Please try again later.",
                ephemeral=True,
            )


def build_request_view(request_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(DinkRequestButton("accept", request_id))
    view.add_item(DinkRequestButton("decline", request_id))
    return view


class DinkCoin(commands.Cog):
    """DinkCoin balances and transfers."""

    def __init__(
        self, bot: commands.Bot, *, repo: DinkCoinRepository | None = None
    ) -> None:
        self.bot = bot
        self.repo = repo or DinkCoinRepository()

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(DinkRequestButton)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(DinkRequestButton)

    @hybrid_command(brief="Check your DINK balance")
    async def balance(self, ctx: commands.Context) -> None:
        """Show your DINK balance."""
        bal = await asyncio.to_thread(self.repo.get_balance, ctx.author.id)
        await ctx.send(f"{ctx.author.mention} has **{bal:g} DINK**")

    @hybrid_command(brief="Show the DINK leaderboard")
    @app_commands.describe(limit="How many holders to show (1–20, default 10)")
    async def ledger(self, ctx: commands.Context, limit: int = 10) -> None:
        """Show the DINK leaderboard."""
        clamped = min(max(limit, 1), 20)
        entries, total = await asyncio.gather(
            asyncio.to_thread(self.repo.get_ledger, clamped),
            asyncio.to_thread(self.repo.get_total_circulation),
        )

        embed = discord.Embed(
            title="DinkCoin Ledger",
            description=f"Total in circulation: **{total:g} DINK**",
            color=EMBED_COLOR,
        )
        if not entries:
            embed.add_field(
                name="No balances yet",
                value="Claim `/1st` to earn your first DINK!",
                inline=False,
            )
        else:
            for row in entries:
                embed.add_field(
                    name=resolve_display_name(self.bot, row.user_id),
                    value=f"{row.balance:g} DINK",
                    inline=False,
                )

        await ctx.send(embed=embed)

    @hybrid_command(brief="Send DINK to another user")
    @app_commands.describe(
        member="Who to send DINK to",
        amount="Whole number of DINK to send",
    )
    async def pay(
        self, ctx: commands.Context, member: discord.Member, amount: float
    ) -> None:
        """Send DINK to another user."""
        whole, error = _validate_transfer(ctx.author, member, amount, action="pay")
        if error or whole is None:
            await ctx.send(error or "Invalid amount.")
            return

        try:
            new_balance = await asyncio.to_thread(
                self.repo.transfer, ctx.author.id, member.id, whole
            )
            await ctx.send(
                f"{ctx.author.mention} sent **{whole:g} DINK** to "
                f"{member.mention}! Your new balance: **{new_balance:g} DINK**"
            )
        except InsufficientBalanceError:
            bal = await asyncio.to_thread(self.repo.get_balance, ctx.author.id)
            await ctx.send(f"Insufficient balance. You have **{bal:g} DINK**.")
        except DatabaseError:
            await ctx.send("Transfer failed. Please try again later.")
        except Exception:
            logger.exception("DinkCoin transfer failed")
            await ctx.send("Transfer failed. Please try again later.")

    @hybrid_command(brief="Request DINK from another user")
    @app_commands.describe(
        member="Who to request DINK from",
        amount="Whole number of DINK to request",
    )
    async def request(
        self, ctx: commands.Context, member: discord.Member, amount: float
    ) -> None:
        """Ask another user to send you DINK; they can accept or decline."""
        whole, error = _validate_transfer(ctx.author, member, amount, action="request")
        if error or whole is None:
            await ctx.send(error or "Invalid amount.")
            return

        expires_at = datetime.now(timezone.utc) + REQUEST_TTL
        try:
            request_id = await asyncio.to_thread(
                self.repo.create_request,
                ctx.author.id,
                member.id,
                whole,
                expires_at,
            )
        except Exception:
            logger.exception("Failed to create DINK request")
            await ctx.send("Could not create request. Please try again later.")
            return

        await ctx.send(
            f"{member.mention}, {ctx.author.mention} is requesting "
            f"**{whole:g} DINK**. Accept or decline?",
            view=build_request_view(request_id),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DinkCoin(bot))
