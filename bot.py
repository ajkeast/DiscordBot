# Import dependencies
import asyncio
import discord                          # Discord API
from discord.ext import commands
import math
import os                              # For environment variables
import logging
import sys
from dotenv import load_dotenv         # Load .env
from utils.constants import GENERAL_CHANNEL_ID
from utils.db import db_ops            # Database operations

# Load environment variables
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True,
)

logger = logging.getLogger(__name__)


# Setup bot
class DinkBot(commands.Bot):
    """A Discord bot with first-tracking, AI capabilities, and utility functions."""
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix='_', case_insensitive=True, intents=intents)

    async def setup_hook(self):
        # Load all cogs
        await self.load_extension('cogs.first')
        await self.load_extension('cogs.dinkcoin')
        await self.load_extension('cogs.server')
        await self.load_extension('cogs.ai')
        await self.load_extension('cogs.utility')
        await self.load_extension('cogs.misc')
        await self.load_extension('cogs.sentiment')

        await self._sync_app_commands()
        self.loop.create_task(self._console_post_loop())

    async def _sync_app_commands(self):
        """Register slash commands with Discord (guild sync when DISCORD_GUILD_ID is set)."""
        guild_id = os.getenv("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s app commands to guild %s", len(synced), guild_id)
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s app commands globally", len(synced))

    async def _console_post_loop(self):
        await self.wait_until_ready()
        channel = await self.fetch_channel(GENERAL_CHANNEL_ID)
        loop = asyncio.get_running_loop()
        while not self.is_closed():
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            text = line.strip()
            if text:
                await channel.send(text)

    async def on_ready(self):
        print(f"Live: {self.user.name}", flush=True)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CommandOnCooldown):
            minutes = max(1, math.ceil(error.retry_after / 60))
            await ctx.send(
                f"You've hit the `/imagine` limit (30 per hour). "
                f"Try again in about {minutes} minute{'s' if minutes != 1 else ''}."
            )
            return
        logger.exception("Command error in %s: %s", getattr(ctx.command, "name", "?"), error)
        await ctx.send("Something went wrong running that command.")

    async def _store_message(self, message_data):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, db_ops.update_messages, message_data)

    async def on_message(self, message):
        if message.author.bot:
            return
        
        message_data = (
            message.id,
            message.author.id,
            message.channel.id,
            message.content,
            message.created_at
        )
        await self._store_message(message_data)
        
        await self.process_commands(message)

    async def on_message_edit(self, before, after):
        if after.author.bot:
            return
            
        message_data = (
            after.id,
            after.author.id,
            after.channel.id,
            after.content,
            after.created_at
        )
        await self._store_message(message_data)

def main():
    bot = DinkBot()
    TOKEN = os.getenv('DISCORD_TOKEN')
    bot.run(TOKEN)

if __name__ == "__main__":
    main()
