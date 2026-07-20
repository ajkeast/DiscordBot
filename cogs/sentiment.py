"""Nightly incremental Grok sentiment scoring for new messages."""

from __future__ import annotations

import asyncio
import logging
from datetime import time

import pytz
from discord import app_commands
from discord.ext import commands, tasks

from utils.interactions import acknowledge
from utils.sentiment_job import run_sentiment_nightly, sentiment_enabled

logger = logging.getLogger(__name__)
EASTERN = pytz.timezone("US/Eastern")
# 4:15 AM Eastern — incremental catch-up for messages missing sentiment rows.
NIGHTLY_AT = time(hour=4, minute=15, tzinfo=EASTERN)


class Sentiment(commands.Cog):
    """Schedule incremental message sentiment scoring with cheap Grok."""

    def __init__(self, bot):
        self.bot = bot
        self._enabled = sentiment_enabled()
        self._run_lock = asyncio.Lock()
        if self._enabled:
            self.nightly_sentiment.start()
        else:
            logger.warning(
                "Sentiment cog loaded but XAI_API_KEY is missing; nightly job disabled."
            )

    def cog_unload(self):
        if self.nightly_sentiment.is_running():
            self.nightly_sentiment.cancel()

    async def _run_sentiment(self, *, limit: int | None = None) -> int:
        """Run sentiment scoring off the event loop; raises if already running."""
        if self._run_lock.locked():
            raise RuntimeError("Sentiment scoring is already running")
        async with self._run_lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, lambda: run_sentiment_nightly(limit=limit)
            )

    @tasks.loop(time=NIGHTLY_AT)
    async def nightly_sentiment(self):
        """Score messages missing from message_sentiment (idempotent)."""
        try:
            written = await self._run_sentiment()
            logger.info("Nightly sentiment wrote %s rows", written)
        except RuntimeError as exc:
            logger.warning("Nightly sentiment skipped: %s", exc)
        except Exception:
            logger.exception("Nightly sentiment job crashed")

    @nightly_sentiment.before_loop
    async def before_nightly_sentiment(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(brief="Score unscored messages with Grok sentiment")
    @app_commands.describe(
        limit="Max messages to score, newest first (omit for all / SENTIMENT_NIGHTLY_LIMIT)",
    )
    async def score_sentiment(self, ctx, limit: int | None = None):
        """Manually kick off incremental sentiment scoring for unscored messages."""
        if not self._enabled:
            await ctx.send("Sentiment scoring is disabled (missing `XAI_API_KEY`).")
            return
        if limit is not None and limit < 1:
            await ctx.send("Limit must be at least 1.")
            return

        async with acknowledge(ctx):
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


async def setup(bot):
    await bot.add_cog(Sentiment(bot))
