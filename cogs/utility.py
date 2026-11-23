"""Utility and fun commands."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import EMBED_COLOR
from utils.discord_helpers import dinkscord_link_view, hybrid_command

_DONORS: tuple[tuple[str, float], ...] = (
    ("Matt", 6.00 + 8.91 + 6.96 + 6.00),
    ("Sammy T", 6.90 + 14.20 + 6.00),
    ("Danny E", 8.00 + 6.90 + 6.00),
    ("Jacky P", 6.69 + 12.00),
    ("Mike S", 8.01 + 6.68),
    ("Whike", 6.00),
)


class Utility(commands.Cog):
    """Simple utility and fun commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @hybrid_command(brief="Say hello")
    async def hello(self, ctx: commands.Context) -> None:
        """Say hello."""
        await ctx.send(f"Hello {ctx.author.mention}!")

    @hybrid_command(brief="Check if the bot is online")
    async def ping(self, ctx: commands.Context) -> None:
        """Check if the bot is online."""
        await ctx.send("pong")

    @hybrid_command(brief="Make the bot repeat your message")
    @app_commands.describe(message="The message for the bot to repeat")
    async def simonsays(self, ctx: commands.Context, *, message: str) -> None:
        """Make the bot repeat your message."""
        await ctx.send(message, allowed_mentions=discord.AllowedMentions.none())

    @hybrid_command(brief="Link to the Dinkscord dashboard")
    async def dashboard(self, ctx: commands.Context) -> None:
        """Share a link button to the public Dinkscord dashboard."""
        # Discord requires message content; zero-width space keeps it button-only.
        await ctx.send("\u200b", view=dinkscord_link_view())

    @hybrid_command(brief="Show the donation board")
    async def donation(self, ctx: commands.Context) -> None:
        """Show the donation board."""
        embed = discord.Embed(
            title="Donation Board",
            description="Thank you to our generous patrons!",
            color=EMBED_COLOR,
        )
        for name, amount in _DONORS:
            embed.add_field(name=name, value=f"${amount:.2f}", inline=False)
        embed.set_footer(text="Peter Dinklage is a non-profit")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))
