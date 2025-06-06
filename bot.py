# Import dependencies
import discord                          # Discord API
from discord.ext import commands
import os                              # For environment variables
from dotenv import load_dotenv         # Load .env
from utils.db import db_ops            # Database operations

# Load environment variables
load_dotenv()

# Setup bot
class DinkBot(commands.Bot):
    """A Discord bot with first-tracking, AI capabilities, and utility functions."""
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix='_', case_insensitive=True, intents=intents)

    async def setup_hook(self):
        # Load all cogs
        await self.load_extension('cogs.first')
        await self.load_extension('cogs.server')
        await self.load_extension('cogs.ai')
        await self.load_extension('cogs.utility')
        await self.load_extension('cogs.misc')

    async def on_ready(self):
        print(f"Live: {self.user.name}")

    async def on_message(self, message):
        if message.author.bot:
            return
        
        # Store message in database
        message_data = (
            message.id,
            message.author.id,
            message.channel.id,
            message.content,
            message.created_at
        )
        db_ops.update_messages(message_data)
        
        await self.process_commands(message)

    async def on_message_edit(self, before, after):
        if after.author.bot:
            return
            
        # Update edited message in database
        message_data = (
            after.id,
            after.author.id,
            after.channel.id,
            after.content,
            after.created_at
        )
        db_ops.update_messages(message_data)

def main():
    bot = DinkBot()
    TOKEN = os.getenv('DISCORD_TOKEN')
    bot.run(TOKEN)

if __name__ == "__main__":
    main()
