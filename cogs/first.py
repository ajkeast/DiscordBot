"""Daily /1st game commands, leaderboards, and charts."""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    EASTERN,
    EMBED_COLOR,
    GENERAL_CHANNEL_ID,
    PROMOTE_DINKSCORD_ON_FIRST,
    dink_mint_amount,
)
from db.first_game import FirstGameRepository
from services.charts import render_firsts_chart, render_juice_chart
from services.first_game import (
    calculate_current_streak,
    calculate_juice_board,
    calculate_user_juice,
    calculate_user_longest_streak,
    compute_daily_juice,
    leaderboard_counts,
)
from utils.discord_helpers import (
    acknowledge,
    dinkscord_link_view,
    hybrid_command,
    resolve_display_name,
)

logger = logging.getLogger(__name__)


class First(commands.Cog):
    """Daily firsts game and related stats."""

    def __init__(
        self, bot: commands.Bot, *, repo: FirstGameRepository | None = None
    ) -> None:
        self.bot = bot
        self.repo = repo or FirstGameRepository()

    @hybrid_command(name="1st", brief="Claim first for today")
    async def first(self, ctx: commands.Context) -> None:
        """Claim first for today. Only works in #general, once per day."""
        if ctx.channel.id != GENERAL_CHANNEL_ID:
            await ctx.send(f"Please send your message to <#{GENERAL_CHANNEL_ID}>.")
            return

        today_eastern = datetime.now(EASTERN).date()
        mint_amount = dink_mint_amount()
        claimed = await asyncio.to_thread(
            self.repo.claim_first_for_day, ctx.author.id, today_eastern, mint_amount
        )
        if not claimed:
            await ctx.send(
                f"Sorry {ctx.author.mention}, first has already been claimed today. 😭"
            )
            return

        msg = f"{ctx.author.mention} is first today! 🥳 +{mint_amount:g} **DINK**"
        if PROMOTE_DINKSCORD_ON_FIRST:
            await ctx.send(msg, view=dinkscord_link_view())
        else:
            await ctx.send(msg)

    @hybrid_command(brief="Show the firsts leaderboard")
    async def score(self, ctx: commands.Context) -> None:
        """Show the firsts leaderboard and current streak."""
        entries = await asyncio.to_thread(self.repo.get_all_entries)
        if not entries:
            await ctx.send("No firsts recorded yet — claim one with `/1st`!")
            return

        streak = calculate_current_streak(entries)
        counts = leaderboard_counts(entries)
        embed = discord.Embed(
            title="First Leaderboard",
            description="Count of daily 1st wins",
            color=EMBED_COLOR,
        )
        for uid, wins in counts[:5]:
            embed.add_field(
                name=resolve_display_name(self.bot, uid),
                value=str(wins),
                inline=False,
            )
        latest_name = resolve_display_name(self.bot, entries[-1].user_id)
        embed.set_footer(text=f"Most recent: {latest_name} 🔥 {streak} days")
        await ctx.send(embed=embed)

    @hybrid_command(brief="Show firsts stats for a user")
    @app_commands.describe(member="User to look up (defaults to you)")
    async def stats(
        self, ctx: commands.Context, member: discord.Member | None = None
    ) -> None:
        """Show score, juice, and streak for you or another user."""
        entries = await asyncio.to_thread(self.repo.get_all_entries)

        target_user: discord.abc.User
        if member is not None:
            target_id = str(member.id)
            target_user = member
        elif ctx.message.mentions:
            target_user = ctx.message.mentions[0]
            target_id = str(target_user.id)
        else:
            target_user = ctx.author
            target_id = str(ctx.author.id)

        user_entries = [e for e in entries if e.user_id == target_id]
        if not user_entries:
            await ctx.send("This user has never gotten a first!")
            return

        streak = calculate_user_longest_streak(entries, target_id)
        score = len(user_entries)
        juice = calculate_user_juice(entries, target_id)

        cached = self.bot.get_user(int(target_id)) or target_user
        title = getattr(cached, "display_name", None) or str(cached)
        embed = discord.Embed(
            title=title,
            description="Your server statistics",
            color=EMBED_COLOR,
        )
        avatar = getattr(cached, "display_avatar", None)
        if avatar is not None:
            embed.set_thumbnail(url=str(avatar.with_size(128)))
        embed.add_field(name="Score", value=f"{score} 🏆", inline=True)
        embed.add_field(name="Juice", value=f"{int(juice)} 🧃", inline=True)
        embed.add_field(name="Longest streak", value=f"{streak} days 🔥", inline=True)
        await ctx.send(embed=embed)

    @hybrid_command(brief="Show the juice leaderboard")
    async def juice(self, ctx: commands.Context) -> None:
        """Show the juice leaderboard and one-day high score."""
        entries = await asyncio.to_thread(self.repo.get_all_entries)
        if not entries:
            await ctx.send("No firsts recorded yet — claim one with `/1st`!")
            return

        rankings, highscore_uid, highscore_value = calculate_juice_board(entries)
        embed = discord.Embed(
            title="Juice Board 🧃",
            description="Total minutes between /1st and midnight 🧃",
            color=EMBED_COLOR,
        )
        for row in rankings[:5]:
            embed.add_field(
                name=resolve_display_name(self.bot, row.user_id),
                value=str(int(row.total_juice)),
                inline=False,
            )
        high_name = resolve_display_name(self.bot, highscore_uid)
        embed.set_footer(
            text=f"1-Day Highscore: {high_name}🧃{int(highscore_value)} mins"
        )
        await ctx.send(embed=embed)

    @hybrid_command(brief="Graph firsts over time")
    async def graph(self, ctx: commands.Context) -> None:
        """Graph firsts over time."""
        async with acknowledge(ctx):
            entries = await asyncio.to_thread(self.repo.get_all_entries)
            if not entries:
                await ctx.send("No firsts recorded yet — claim one with `/1st`!")
                return

            name_map = {
                e.user_id: resolve_display_name(self.bot, e.user_id) for e in entries
            }
            png_bytes = await asyncio.to_thread(
                render_firsts_chart, entries, lambda uid: name_map.get(uid, f"User {uid}")
            )
            embed = discord.Embed(title="Firsts to Date", color=EMBED_COLOR)
            embed.set_image(url="attachment://first_graph.png")
            await ctx.send(
                embed=embed,
                file=discord.File(io.BytesIO(png_bytes), filename="first_graph.png"),
            )

    @hybrid_command(brief="Graph daily juice over time")
    async def juicegraph(self, ctx: commands.Context) -> None:
        """Graph daily juice over time."""
        async with acknowledge(ctx):
            entries = await asyncio.to_thread(self.repo.get_all_entries)
            if not entries:
                await ctx.send("No firsts recorded yet — claim one with `/1st`!")
                return

            points = compute_daily_juice(entries)
            _, highscore_uid, highscore_value = calculate_juice_board(entries)
            png_bytes = await asyncio.to_thread(render_juice_chart, points)

            high_name = resolve_display_name(self.bot, highscore_uid)
            embed = discord.Embed(title="Daily Juice", color=EMBED_COLOR)
            embed.set_footer(
                text=f"1-Day Highscore: {high_name} 🧃{int(highscore_value)} mins"
            )
            embed.set_image(url="attachment://juice_graph.png")
            await ctx.send(
                embed=embed,
                file=discord.File(io.BytesIO(png_bytes), filename="juice_graph.png"),
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(First(bot))
