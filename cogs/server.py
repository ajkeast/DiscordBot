"""Server metadata sync commands and idempotent monthly activity report."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import EASTERN, EMBED_COLOR, GENERAL_CHANNEL_ID
from db.server import ServerRepository
from utils.discord_helpers import hybrid_command
from utils.permissions import require_admin

logger = logging.getLogger(__name__)
MONTHLY_REPORT_AT = time(hour=9, minute=0, tzinfo=EASTERN)


def _previous_year_month(now: datetime) -> tuple[int, int]:
    local = now.astimezone(EASTERN)
    if local.month == 1:
        return local.year - 1, 12
    return local.year, local.month - 1


def _report_month_due(now: datetime) -> tuple[int, int] | None:
    """Return ``(year, month)`` to report on the 1st of the month at/after 9:00 AM ET."""
    local = now.astimezone(EASTERN)
    if local.day != 1 or (local.hour, local.minute) < (9, 0):
        return None
    return _previous_year_month(local)


class Server(commands.Cog):
    """Server info sync and monthly activity report."""

    def __init__(
        self, bot: commands.Bot, *, repo: ServerRepository | None = None
    ) -> None:
        self.bot = bot
        self.repo = repo or ServerRepository()

    async def cog_load(self) -> None:
        self.monthly_stats.start()

    async def cog_unload(self) -> None:
        if self.monthly_stats.is_running():
            self.monthly_stats.cancel()

    async def _post_monthly_report_if_due(self, now: datetime) -> bool:
        due = _report_month_due(now)
        if due is None:
            return False
        year, month = due
        report_key = f"{year:04d}-{month:02d}"

        claim = await asyncio.to_thread(
            self.repo.claim_monthly_report, report_key, GENERAL_CHANNEL_ID
        )
        if claim == "already_posted":
            return False

        sent_message = None
        try:
            counts = await asyncio.to_thread(
                self.repo.get_monthly_message_counts, year, month
            )
            if not counts:
                await asyncio.to_thread(
                    self.repo.release_monthly_report_claim, report_key
                )
                return False

            month_name = datetime(year, month, 1).strftime("%B")
            embed = discord.Embed(
                title=f"📊 Monthly Activity Report - {month_name} {year}",
                color=EMBED_COLOR,
            )
            for i, row in enumerate(counts[:5], start=1):
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                embed.add_field(
                    name=f"{medal} {row.user_name}",
                    value=f"{row.message_count} messages",
                    inline=False,
                )
            total_messages = sum(row.message_count for row in counts)
            embed.set_footer(text=f"Total messages: {total_messages}")

            channel = self.bot.get_channel(GENERAL_CHANNEL_ID)
            if channel is None:
                channel = await self.bot.fetch_channel(GENERAL_CHANNEL_ID)
            sent_message = await channel.send(embed=embed)  # type: ignore[union-attr]
            await asyncio.to_thread(
                self.repo.mark_monthly_report_posted,
                report_key,
                GENERAL_CHANNEL_ID,
                getattr(sent_message, "id", None),
            )
            return True
        except discord.Forbidden:
            logger.warning(
                "Bot doesn't have permission to send messages in channel %s",
                GENERAL_CHANNEL_ID,
            )
            await asyncio.to_thread(self.repo.release_monthly_report_claim, report_key)
            return False
        except Exception:
            logger.exception("Failed posting monthly activity report for %s", report_key)
            if sent_message is None:
                try:
                    await asyncio.to_thread(
                        self.repo.release_monthly_report_claim, report_key
                    )
                except Exception:
                    logger.exception("Failed releasing monthly report claim for %s", report_key)
            return False

    @tasks.loop(time=MONTHLY_REPORT_AT)
    async def monthly_stats(self) -> None:
        """Post monthly stats at 9:00 AM Eastern on the 1st of each month."""
        await self._post_monthly_report_if_due(datetime.now(EASTERN))

    @monthly_stats.before_loop
    async def before_monthly_stats(self) -> None:
        await self.bot.wait_until_ready()
        try:
            await self._post_monthly_report_if_due(datetime.now(EASTERN))
        except Exception:
            logger.exception("Monthly report startup catch-up failed")

    @hybrid_command(brief="Refresh member info for the dashboard (admins)")
    @commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def members(self, ctx: commands.Context) -> None:
        """Refresh member info for the dashboard."""
        if not await require_admin(self.bot, ctx):
            return
        assert ctx.guild is not None
        member_data = [
            (
                int(member.id),
                member.name,
                member.nick,
                str(member.display_avatar.with_size(128)),
                member.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            )
            for member in ctx.guild.members
        ]
        await asyncio.to_thread(self.repo.upsert_members, member_data)
        await ctx.send("Member info successfully updated.")

    @hybrid_command(brief="Refresh emoji info for the dashboard (admins)")
    @commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def emojis(self, ctx: commands.Context) -> None:
        """Refresh emoji info for the dashboard."""
        if not await require_admin(self.bot, ctx):
            return
        assert ctx.guild is not None
        emoji_data = [
            (
                emoji.id,
                emoji.name,
                emoji.guild_id,
                str(emoji.url),
                emoji.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            )
            for emoji in ctx.guild.emojis
        ]
        await asyncio.to_thread(self.repo.upsert_emojis, emoji_data)
        await ctx.send("Emoji info successfully updated.")

    @hybrid_command(brief="Refresh channel info for the dashboard (admins)")
    @commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def channels(self, ctx: commands.Context) -> None:
        """Refresh channel info for the dashboard."""
        if not await require_admin(self.bot, ctx):
            return
        assert ctx.guild is not None
        channel_data = [
            (channel.id, channel.name, channel.created_at)
            for channel in ctx.guild.channels
        ]
        await asyncio.to_thread(self.repo.upsert_channels, channel_data)
        await ctx.send("Channel info successfully updated.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Server(bot))
