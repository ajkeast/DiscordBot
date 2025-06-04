import discord
from discord.ext import commands
from utils.db import db_ops
from datetime import datetime, timedelta

class Server(commands.Cog):
    """A cog for managing and tracking server-related information and updates."""
    
    def __init__(self, bot):
        self.bot = bot

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

    @commands.command()
    async def monthly_stats(self, ctx):
        """Display the most active members for the previous month.
        
        Args:
            ctx: The command context
        """
        # Get the previous month
        today = datetime.now()
        if today.month == 1:
            year = today.year - 1
            month = 12
        else:
            year = today.year
            month = today.month - 1

        # Get message counts
        df = db_ops.get_monthly_message_counts(year, month)
        
        if df.empty:
            await ctx.send("No messages found for the previous month.")
            return

        # Create embed
        month_name = datetime(year, month, 1).strftime("%B")
        embed = discord.Embed(
            title=f"📊 Monthly Activity Report - {month_name} {year}",
            color=discord.Color.blue()
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

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Server(bot)) 