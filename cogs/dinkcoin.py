import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.constants import EMBED_COLOR
from utils.db import DatabaseError, db_ops

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 24 * 60 * 60


class DinkRequestView(discord.ui.View):
    """Accept/decline buttons for a pending DINK request."""

    def __init__(self, requester: discord.abc.User, payer: discord.abc.User, amount: int):
        super().__init__(timeout=REQUEST_TIMEOUT_SECONDS)
        self.requester_id = requester.id
        self.payer_id = payer.id
        self.amount = amount
        self.requester_mention = requester.mention
        self.payer_mention = payer.mention
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.payer_id:
            await interaction.response.send_message(
                "Only the requested user can respond to this.",
                ephemeral=True,
            )
            return False
        return True

    def _disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    async def on_timeout(self) -> None:
        self._disable_buttons()
        if self.message is not None:
            try:
                await self.message.edit(
                    content=(
                        f"Request expired: {self.requester_mention} asked "
                        f"{self.payer_mention} for **{self.amount:g} DINK**."
                    ),
                    view=self,
                )
            except discord.HTTPException:
                logger.exception("Failed to edit expired DINK request message")

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.primary)
    async def accept(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        balance = db_ops.get_dink_balance(self.payer_id)
        if self.amount > balance:
            await interaction.response.send_message(
                f"Insufficient balance. You have **{balance:g} DINK**.",
                ephemeral=True,
            )
            return

        try:
            db_ops.record_dink_transfer(self.payer_id, self.requester_id, self.amount)
        except DatabaseError:
            await interaction.response.send_message(
                "Transfer failed. Please try again later.",
                ephemeral=True,
            )
            return
        except Exception as exc:
            logger.exception("DinkCoin request transfer failed: %s", exc)
            await interaction.response.send_message(
                "Transfer failed. Please try again later.",
                ephemeral=True,
            )
            return

        self._disable_buttons()
        await interaction.response.edit_message(
            content=(
                f"{self.payer_mention} accepted and sent **{self.amount:g} DINK** "
                f"to {self.requester_mention}!"
            ),
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self._disable_buttons()
        await interaction.response.edit_message(
            content=(
                f"{self.payer_mention} declined {self.requester_mention}'s request "
                f"for **{self.amount:g} DINK**."
            ),
            view=self,
        )
        self.stop()


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

    @commands.hybrid_command(brief="Request DINK from another user")
    @app_commands.describe(
        member="Who to request DINK from",
        amount="Whole number of DINK to request",
    )
    async def request(self, ctx, member: discord.Member, amount: float):
        """Ask another user to send you DINK; they can accept or decline."""
        if member.bot:
            await ctx.send("You cannot request DINK from bots.")
            return
        if member.id == ctx.author.id:
            await ctx.send("You cannot request DINK from yourself.")
            return
        if amount <= 0:
            await ctx.send("Amount must be greater than zero.")
            return
        if amount != int(amount):
            await ctx.send("Only whole DINK coins can be transferred.")
            return

        amount = int(amount)
        view = DinkRequestView(ctx.author, member, amount)
        message = await ctx.send(
            f"{member.mention}, {ctx.author.mention} is requesting "
            f"**{amount:g} DINK**. Accept or decline?",
            view=view,
        )
        view.message = message


async def setup(bot):
    await bot.add_cog(DinkCoin(bot))
