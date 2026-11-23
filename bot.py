"""DiscordBot entrypoint: lifecycle, shared resources, message logging, and cogs."""

from __future__ import annotations

import asyncio
import logging
import math
import sys

import aiohttp
import discord
from discord.ext import commands

from config import (
    GENERAL_CHANNEL_ID,
    discord_guild_id,
    discord_token,
    enable_console_post_loop,
)
from db.messages import MessagesRepository
from db.pool import DatabasePool, get_pool
from db.schema import ensure_runtime_tables

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True,
)

logger = logging.getLogger(__name__)

EXTENSIONS: tuple[str, ...] = (
    "cogs.first",
    "cogs.dinkcoin",
    "cogs.server",
    "cogs.ai",
    "cogs.utility",
    "cogs.sentiment",
    "cogs.music",
    "cogs.minecraft",
)


class DinkBot(commands.Bot):
    """Discord bot with /1st, DinkCoin, AI, music, sentiment, and Minecraft recaps."""

    def __init__(
        self,
        *,
        db_pool: DatabasePool | None = None,
        messages_repo: MessagesRepository | None = None,
    ) -> None:
        intents = discord.Intents.all()
        super().__init__(command_prefix="_", case_insensitive=True, intents=intents)
        self.db_pool: DatabasePool = db_pool or get_pool()
        self.messages_repo: MessagesRepository = messages_repo or MessagesRepository(
            self.db_pool
        )
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession()
        try:
            await asyncio.to_thread(ensure_runtime_tables, self.db_pool)
        except Exception:
            logger.exception("Could not initialize bot-managed Postgres tables on startup")

        for ext in EXTENSIONS:
            await self.load_extension(ext)

        await self._sync_app_commands()
        if enable_console_post_loop():
            self.loop.create_task(self._console_post_loop())

    async def close(self) -> None:
        if self.http_session is not None and not self.http_session.closed:
            await self.http_session.close()
        self.db_pool.close()
        await super().close()

    async def _sync_app_commands(self) -> None:
        guild_id = discord_guild_id()
        if guild_id is not None:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s app commands to guild %s", len(synced), guild_id)
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s app commands globally", len(synced))

    async def _console_post_loop(self) -> None:
        await self.wait_until_ready()
        channel = await self.fetch_channel(GENERAL_CHANNEL_ID)
        while not self.is_closed():
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            text = line.strip()
            if text and isinstance(channel, discord.abc.Messageable):
                await channel.send(text)

    async def on_ready(self) -> None:
        logger.info("Live: %s", getattr(self.user, "name", "?"))

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("This command only works in a server.")
            return
        if isinstance(error, commands.CommandOnCooldown):
            minutes = max(1, math.ceil(error.retry_after / 60))
            await ctx.send(
                f"You're on cooldown for this command. "
                f"Try again in about {minutes} minute{'s' if minutes != 1 else ''}."
            )
            return
        logger.exception(
            "Command error in %s: %s", getattr(ctx.command, "name", "?"), error
        )
        await ctx.send("Something went wrong running that command.")

    async def _store_message(
        self,
        message_id: int,
        member_id: int,
        channel_id: int,
        content: str,
        created_at,
    ) -> None:
        await asyncio.to_thread(
            self.messages_repo.insert_message,
            message_id,
            member_id,
            channel_id,
            content,
            created_at,
        )

    async def _store_message_edit(
        self,
        message_id: int,
        new_content: str | None,
        *,
        member_id: int | None,
        channel_id: int | None,
        created_at,
        original_content: str | None,
    ) -> None:
        await asyncio.to_thread(
            self.messages_repo.record_edit,
            message_id,
            new_content,
            member_id=member_id,
            channel_id=channel_id,
            created_at=created_at,
            original_content=original_content,
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        await self._store_message(
            message.id,
            message.author.id,
            message.channel.id,
            message.content,
            message.created_at,
        )
        await self.process_commands(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        """Cached edit. Identical content (link-preview embeds) is ignored."""
        author = getattr(after, "author", None)
        if author is not None and getattr(author, "bot", False):
            return
        before_content = getattr(before, "content", None)
        after_content = getattr(after, "content", None)
        if after_content is None:
            return
        if before_content is not None and before_content == after_content:
            return
        channel = getattr(after, "channel", None)
        await self._store_message_edit(
            after.id,
            after_content,
            member_id=getattr(author, "id", None),
            channel_id=getattr(channel, "id", None),
            created_at=getattr(after, "created_at", None),
            original_content=before_content,
        )

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """Uncached edit. on_message_edit does not run when the message is not cached."""
        if getattr(payload, "cached_message", None) is not None:
            return
        data = getattr(payload, "data", None) or {}
        if "content" not in data:
            return
        author_data = data.get("author") or {}
        if author_data.get("bot"):
            return
        message = getattr(payload, "message", None)
        author = getattr(message, "author", None) if message is not None else None
        if author is not None and getattr(author, "bot", False):
            return

        member_id = author_data.get("id")
        if member_id is None and author is not None:
            member_id = getattr(author, "id", None)
        if member_id is not None:
            member_id = int(member_id)

        channel_id = getattr(payload, "channel_id", None)
        created_at = getattr(message, "created_at", None) if message is not None else None
        if created_at is None:
            created_at = discord.utils.snowflake_time(payload.message_id)
        content = data.get("content")
        await self._store_message_edit(
            payload.message_id,
            "" if content is None else content,
            member_id=member_id,
            channel_id=channel_id,
            created_at=created_at,
            original_content=None,
        )


def main() -> None:
    bot = DinkBot()
    bot.run(discord_token())


if __name__ == "__main__":
    main()
