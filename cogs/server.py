import discord
from discord.ext import commands, tasks
from utils.db import db_ops
from utils.constants import GENERAL_CHANNEL_ID, EMBED_COLOR
from datetime import datetime, timedelta
import pytz
import asyncio

class Server(commands.Cog):
    """A cog for managing and tracking server-related information and updates."""
    
    def __init__(self, bot):
        self.bot = bot
        self.monthly_stats.start()

    def cog_unload(self):
        """Clean up tasks when cog is unloaded"""
        self.monthly_stats.cancel()

    @tasks.loop(hours=1)
    async def monthly_stats(self):
        """Post monthly stats at 9am EST on the first of each month"""
        # Get current time in EST
        est = pytz.timezone('US/Eastern')
        now = datetime.now(est)    
        # Check if it's 9am on the first of the month
        if now.hour == 9 and now.minute == 0 and now.day == 1:
            # Get the previous month
            if now.month == 1:
                year = now.year - 1
                month = 12
            else:
                year = now.year
                month = now.month - 1
            # Get message counts
            df = db_ops.get_monthly_message_counts(year, month)
            if df.empty:
                return
            # Create embed
            month_name = datetime(year, month, 1).strftime("%B")
            embed = discord.Embed(
                title=f"📊 Monthly Activity Report - {month_name} {year}",
                color=EMBED_COLOR
            )
            # Add top 5 members to embed
            for i, (_, row) in enumerate(df.head(5).iterrows(), 1):
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                embed.add_field(
                    name=f"{medal} {row['user_name']}",
                    value=f"{row['message_count']} messages",
                    inline=False
                )

            # Add total messages
            total_messages = df['message_count'].sum()
            embed.set_footer(text=f"Total messages: {total_messages}")

            # Get the general channel
            channel = self.bot.get_channel(GENERAL_CHANNEL_ID)
            if channel:
                try:
                    await channel.send(embed=embed)
                except discord.Forbidden:
                    print(f"Bot doesn't have permission to send messages in channel {GENERAL_CHANNEL_ID}")

    @monthly_stats.before_loop
    async def before_monthly_stats(self):
        """Wait until bot is ready before starting the task"""
        await self.bot.wait_until_ready()
        # Calculate time until next hour
        est = pytz.timezone('US/Eastern')
        now = datetime.now(est)
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        seconds_until_next_hour = (next_hour - now).total_seconds()
        await asyncio.sleep(seconds_until_next_hour)

    @commands.command()
    async def members(self, ctx):
        """Update the database with current server member information.
        
        Args:
            ctx: The command context
            
        Stores for each member:
        - User ID
        - Username
        - Display name (nickname)
        - Avatar URL
        - Account creation date
        """
        # updates database with all current members of the server and their info
        members = ctx.guild.members
        member_data = []
        for member in members:
            id = int(member.id)
            user = self.bot.get_user(id)
            avatar = str(user.display_avatar.with_size(128))
            user_name = user.name
            display_name = member.nick
            created_at = user.created_at.strftime("%Y-%m-%d %H:%M:%S")
            member_data.append([id, user_name, display_name, avatar, created_at])
            
        db_ops.update_members(member_data)
        await ctx.channel.send("Member info successfully updated.")

    @commands.command()
    async def emojis(self, ctx):
        """Update the database with current server emoji information.
        
        Args:
            ctx: The command context
            
        Stores for each emoji:
        - Emoji ID
        - Name
        - Guild ID
        - Image URL
        - Creation date
        """
        # updates database with all current emojis in the server
        emoji_data = []
        for emoji in ctx.guild.emojis:
            id = emoji.id
            emoji_name = emoji.name
            guild_id = emoji.guild_id
            url = emoji.url
            created_at = emoji.created_at.strftime("%Y-%m-%d %H:%M:%S")
            emoji_data.append([id, emoji_name, guild_id, url, created_at])
        
        db_ops.update_emojis(emoji_data)
        await ctx.channel.send("Emoji info successfully updated.")

    @commands.command()
    async def channels(self, ctx):
        """Update the database with current server channel information.
        
        Args:
            ctx: The command context
            
        Stores for each channel:
        - Channel ID
        - Channel name
        - Creation date
        """
        # updates database with all current channels on the server
        channel_data = []
        for channel in ctx.guild.channels:
            id = channel.id
            name = channel.name
            created_at = channel.created_at
            channel_data.append([id, name, created_at])
        
        db_ops.update_channels(channel_data)
        await ctx.channel.send("Channel info successfully updated.")

async def setup(bot):
    await bot.add_cog(Server(bot)) 