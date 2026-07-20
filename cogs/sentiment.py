"""Nightly incremental Grok sentiment scoring for new messages."""

from __future__ import annotations

import asyncio
import logging
from datetime import time

import pytz
from discord.ext import commands, tasks

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
        if self._enabled:
            self.nightly_sentiment.start()
        else:
            logger.warning(
                "Sentiment cog loaded but XAI_API_KEY is missing; nightly job disabled."
            )

    def cog_unload(self):
        if self.nightly_sentiment.is_running():
            self.nightly_sentiment.cancel()

    @tasks.loop(time=NIGHTLY_AT)
    async def nightly_sentiment(self):
        """Score messages missing from message_sentiment (idempotent)."""
        loop = asyncio.get_running_loop()
        try:
            written = await loop.run_in_executor(None, run_sentiment_nightly)
            logger.info("Nightly sentiment wrote %s rows", written)
        except Exception:
            logger.exception("Nightly sentiment job crashed")

    @nightly_sentiment.before_loop
    async def before_nightly_sentiment(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Sentiment(bot))
