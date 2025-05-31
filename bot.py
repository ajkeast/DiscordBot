# Import dependencies (make sure requirements.txt includes these)

import discord                                              # Discord API
from discord.ext import commands
# from discord_components import Button, DiscordComponents
import pymysql                                              # Connect to AWS SQL
import os,io,base64,string,time,random,asyncio,re           # Core python libraries
import pandas as pd                                         # Manipulate tabular data
from chatgpt_functions import *                             # function calls for ChatGPT API
from dotenv import load_dotenv                              # Load .env
from datetime import datetime
import matplotlib.pyplot as plt

# Load environment variables
load_dotenv()

# Setup bot
class DinkBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix='_', case_insensitive=True, intents=intents)
        self.remove_command('help')  # Remove default help command

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
        id = message.id
        member_id = message.author.id
        channel_id = message.channel.id
        content = message.content
        created_at = message.created_at
        vals = [id, member_id, channel_id, content, created_at]
        vals = [value if value is not None else 'NULL' for value in vals]

        from utils.db import update_sql_messages
        update_sql_messages(vals)
        
        await self.process_commands(message)

def main():
    bot = DinkBot()
    TOKEN = os.getenv('DISCORD_TOKEN')
    bot.run(TOKEN)

if __name__ == "__main__":
    main()
