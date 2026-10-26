"""Daily Minecraft activity summary posted to #minecraft at 8:00 AM Eastern."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.constants import EMBED_COLOR
from utils.minecraft_job import (
    PreparedPost,
    activity_date_due,
    claim_daily_post,
    embed_footer,
    mark_posted,
    minecraft_channel_id,
    prepare_post,
    release_claim,
    summary_title,
)
from utils.minecraft_log import EASTERN, MinecraftLogError

logger = logging.getLogger(__name__)
DAILY_AT = time(hour=8, minute=0, tzinfo=EASTERN)


def actor_is_admin_result(is_owner: bool, user, guild) -> bool:
    """True for the bot owner or a guild administrator."""
    if is_owner:
        return True
    if guild is None:
        return False
    perms = getattr(user, "guild_permissions", None)
    return bool(perms and perms.administrator is True)


class Minecraft(commands.Cog):
    """Post the previous day's Minecraft activity and preview it for admins."""

    def __init__(self, bot):
        self.bot = bot
        self._run_lock = asyncio.Lock()

    async def cog_load(self):
        self.daily_minecraft.start()

    def cog_unload(self):
        if self.daily_minecraft.is_running():
            self.daily_minecraft.cancel()

    async def _actor_is_admin(self, ctx) -> bool:
        is_owner = False
        checker = getattr(self.bot, "is_owner", None)
        if checker is not None:
            result = checker(ctx.author)
            if asyncio.iscoroutine(result):
                result = await result
            is_owner = result is True
        return actor_is_admin_result(is_owner, ctx.author, ctx.guild)

    def _embed(self, prepared: PreparedPost) -> discord.Embed:
        description = prepared.narrative
        if len(description) > 4096:
            description = description[:4090].rstrip() + "…"
        embed = discord.Embed(
            title=summary_title(prepared.stats),
            description=description,
            color=EMBED_COLOR,
        )
        embed.set_footer(text=embed_footer(prepared.stats))
        return embed

    async def _send_summary(self, channel, prepared: PreparedPost):
        if prepared.nobody_played:
            return await channel.send(prepared.narrative[:2000])
        return await channel.send(embed=self._embed(prepared))

    async def _post_due_date(self, day: date, *, reason: str) -> None:
        """Post one calendar day if it has not already been posted."""
        if self._run_lock.locked():
            logger.info("Minecraft summary already running; skipping %s (%s)", day, reason)
            return
        async with self._run_lock:
            channel_id = minecraft_channel_id()
            loop = asyncio.get_running_loop()
            try:
                claim = await loop.run_in_executor(
                    None, lambda: claim_daily_post(day, channel_id)
                )
            except Exception:
                logger.exception("Minecraft summary could not claim %s", day.isoformat())
                return
            if claim == "already_posted":
                logger.info("Minecraft summary for %s already posted", day.isoformat())
                return

            sent_message = None
            try:
                now = datetime.now(EASTERN)
                prepared = await loop.run_in_executor(
                    None, lambda: prepare_post(day, as_of=now, persist="required")
                )
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    channel = await self.bot.fetch_channel(channel_id)
                sent_message = await self._send_summary(channel, prepared)
                await loop.run_in_executor(
                    None,
                    lambda: mark_posted(
                        day,
                        channel_id,
                        getattr(sent_message, "id", None),
                        prepared.nobody_played,
                    ),
                )
                logger.info(
                    "Posted Minecraft summary for %s via %s (%s)",
                    day.isoformat(),
                    prepared.source,
                    reason,
                )
            except Exception:
                logger.exception("Minecraft summary for %s failed (%s)", day.isoformat(), reason)
                if sent_message is not None:
                    logger.error(
                        "Minecraft summary for %s was sent but not recorded; "
                        "a restart may post it again",
                        day.isoformat(),
                    )
                    return
                try:
                    await loop.run_in_executor(None, lambda: release_claim(day))
                except Exception:
                    logger.exception(
                        "Could not release Minecraft post claim for %s",
                        day.isoformat(),
                    )

    async def _catch_up(self) -> None:
        due = activity_date_due(datetime.now(EASTERN))
        if due is None:
            return
        await self._post_due_date(due, reason="startup-catchup")

    @tasks.loop(time=DAILY_AT)
    async def daily_minecraft(self):
        """Post yesterday's Minecraft recap. Idempotent across restarts."""
        due = activity_date_due(datetime.now(EASTERN))
        if due is None:
            return
        await self._post_due_date(due, reason="scheduled")

    @daily_minecraft.before_loop
    async def before_daily_minecraft(self):
        await self.bot.wait_until_ready()
        try:
            await self._catch_up()
        except Exception:
            logger.exception("Minecraft summary startup catch-up failed")

    @commands.hybrid_command(
        name="minecraft_summary",
        brief="Preview a Minecraft daily summary (admins)",
    )
    @commands.guild_only()
    @app_commands.describe(day="Calendar date in America/New_York (YYYY-MM-DD)")
    @app_commands.default_permissions(administrator=True)
    async def minecraft_summary(self, ctx, day: str):
        """Preview the Minecraft daily summary for a date. Does not post it."""
        if not await self._actor_is_admin(ctx):
            message = "Only server admins can preview the Minecraft summary."
            if ctx.interaction is not None and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(message, ephemeral=True)
            else:
                await ctx.send(message)
            return

        try:
            activity_day = date.fromisoformat(day.strip())
        except ValueError:
            message = "Use a date like `2026-10-25`."
            if ctx.interaction is not None and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(message, ephemeral=True)
            else:
                await ctx.send(message)
            return

        if ctx.interaction is not None and not ctx.interaction.response.is_done():
            await ctx.defer(ephemeral=True)

        loop = asyncio.get_running_loop()
        try:
            now = datetime.now(EASTERN)
            prepared = await loop.run_in_executor(
                None,
                lambda: prepare_post(activity_day, as_of=now, persist="best_effort"),
            )
        except MinecraftLogError as exc:
            await ctx.send(str(exc))
            return
        except Exception:
            logger.exception("Minecraft preview failed for %s", day)
            await ctx.send("Couldn't build that Minecraft summary. Check the bot logs.")
            return

        if prepared.nobody_played:
            await ctx.send(prepared.narrative[:2000])
            return
        await ctx.send(embed=self._embed(prepared))


async def setup(bot):
    await bot.add_cog(Minecraft(bot))
