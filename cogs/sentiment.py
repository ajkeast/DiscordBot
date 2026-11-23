"""Nightly incremental Grok sentiment scoring for new messages."""

from __future__ import annotations

import asyncio
import logging
from datetime import time

from discord import app_commands
from discord.ext import commands, tasks

from config import EASTERN
from services.sentiment import run_sentiment_nightly, sentiment_enabled
from utils.discord_helpers import acknowledge, hybrid_command, reply_ephemeral
from utils.permissions import require_admin

logger = logging.getLogger(__name__)
NIGHTLY_AT = time(hour=4, minute=15, tzinfo=EASTERN)


class Sentiment(commands.Cog):
    """Schedule incremental message sentiment scoring with Grok."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._enabled = sentiment_enabled()
        self._run_lock = asyncio.Lock()

    async def cog_load(self) -> None:
        if self._enabled:
            self.nightly_sentiment.start()
        else:
            logger.warning(
                "Sentiment cog loaded but XAI_API_KEY is missing; nightly job disabled."
            )

    async def cog_unload(self) -> None:
        if self.nightly_sentiment.is_running():
            self.nightly_sentiment.cancel()

    async def _run_sentiment(self, *, limit: int | None = None) -> int:
        if self._run_lock.locked():
            raise RuntimeError("Sentiment scoring is already running")
        async with self._run_lock:
            return await asyncio.to_thread(run_sentiment_nightly, limit=limit)

    @tasks.loop(time=NIGHTLY_AT)
    async def nightly_sentiment(self) -> None:
        try:
            written = await self._run_sentiment()
            logger.info("Nightly sentiment wrote %s rows", written)
        except RuntimeError as exc:
            logger.warning("Nightly sentiment skipped: %s", exc)
        except Exception:
            logger.exception("Nightly sentiment job crashed")

    @nightly_sentiment.before_loop
    async def before_nightly_sentiment(self) -> None:
        await self.bot.wait_until_ready()

    @hybrid_command(brief="Score unscored messages with Grok sentiment (admins)")
    @commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        limit="Max messages to score, newest first (omit for all / SENTIMENT_NIGHTLY_LIMIT)",
    )
    async def score_sentiment(
        self, ctx: commands.Context, limit: int | None = None
    ) -> None:
        """Manually kick off incremental sentiment scoring for unscored messages."""
        if not await require_admin(
            self.bot, ctx, "Only server admins can run sentiment scoring."
        ):
            return
        if not self._enabled:
            await reply_ephemeral(
                ctx, "Sentiment scoring is disabled (missing `XAI_API_KEY`)."
            )
            return
        if limit is not None and limit < 1:
            await reply_ephemeral(ctx, "Limit must be at least 1.")
            return

        async with acknowledge(ctx, ephemeral=True):
            try:
                written = await self._run_sentiment(limit=limit)
            except RuntimeError as exc:
                await ctx.send(str(exc))
                return
            except Exception:
                logger.exception("Manual sentiment job crashed")
                await ctx.send("Sentiment scoring failed. Check the bot logs.")
                return

            if written == 0:
                await ctx.send("No unscored messages to process.")
            else:
                noun = "message" if written == 1 else "messages"
                await ctx.send(f"Scored {written} {noun}.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Sentiment(bot))
