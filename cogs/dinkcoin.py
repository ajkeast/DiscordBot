import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.constants import EMBED_COLOR
from utils.db import DatabaseError, db_ops

logger = logging.getLogger(__name__)


class DinkCoin(commands.Cog):
    """DinkCoin balances and transfers."""

    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(brief="Check your DINK balance")
    async def balance(self, ctx):
        """Show your DINK balance."""
        balance = db_ops.get_dink_balance(ctx.author.id)
        await ctx.send(f"{ctx.author.mention} has **{balance:g} DINK**")

    @commands.hybrid_command(brief="Show the DINK leaderboard")
    @app_commands.describe(limit="How many holders to show (1–20, default 10)")
    async def ledger(self, ctx, limit: int = 10):
        """Show the DINK leaderboard."""
        limit = min(max(limit, 1), 20)
        df = db_ops.get_dink_ledger(limit)
        total = db_ops.get_total_dink_circulation()

        embed = discord.Embed(
            title="DinkCoin Ledger",
            description=f"Total in circulation: **{total:g} DINK**",
            color=EMBED_COLOR,
        )

        if df.empty:
            embed.add_field(
                name="No balances yet",
                value="Claim `/1st` to earn your first DINK!",
                inline=False,
            )
        else:
            for _, row in df.iterrows():
                user = self.bot.get_user(int(row["user_id"]))
                name = user.display_name if user else f"User {row['user_id']}"
                rank = len(embed.fields) + 1
                embed.add_field(
                    name=f"{name}",
                    value=f"{float(row['balance']):g} DINK",
                    inline=False,
                )

        await ctx.send(embed=embed)

    @commands.hybrid_command(brief="Send DINK to another user")
    @app_commands.describe(
        member="Who to send DINK to",
        amount="Whole number of DINK to send",
    )
    async def pay(self, ctx, member: discord.Member, amount: float):
        """Send DINK to another user."""
        if member.bot:
            await ctx.send("You cannot pay bots.")
            return
        if member.id == ctx.author.id:
            await ctx.send("You cannot pay yourself.")
            return
        if amount <= 0:
            await ctx.send("Amount must be greater than zero.")
            return
        if amount != int(amount):
            await ctx.send("Only whole DINK coins can be transferred.")
            return

        amount = int(amount)

        sender_balance = db_ops.get_dink_balance(ctx.author.id)
        if amount > sender_balance:
            await ctx.send(
                f"Insufficient balance. You have **{sender_balance:g} DINK**."
            )
            return

        try:
            db_ops.record_dink_transfer(ctx.author.id, member.id, amount)
            new_balance = db_ops.get_dink_balance(ctx.author.id)
            await ctx.send(
                f"{ctx.author.mention} sent **{amount:g} DINK** to "
                f"{member.mention}! Your new balance: **{new_balance:g} DINK**"
            )
        except DatabaseError:
            await ctx.send("Transfer failed. Please try again later.")
        except Exception as exc:
            logger.exception("DinkCoin transfer failed: %s", exc)
            await ctx.send("Transfer failed. Please try again later.")


async def setup(bot):
    await bot.add_cog(DinkCoin(bot))
