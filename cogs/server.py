import discord
from discord.ext import commands
from utils.db import db_ops

class Server(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def members(self, ctx):
        # updates database with all current members of the server and their info
        members = ctx.guild.members
        member_data = []
        for member in members:
            id = int(member.id)
            user = self.bot.get_user(id)
            avatar = f'https://cdn.discordapp.com/avatars/{id}/{user.avatar}.webp?size=128'
            user_name = user.name
            display_name = member.nick
            created_at = user.created_at.strftime("%Y-%m-%d %H:%M:%S")
            member_data.append([id, user_name, display_name, avatar, created_at])
            
        db_ops.update_members(member_data)
        await ctx.channel.send("Member info successfully updated.")

    @commands.command()
    async def emojis(self, ctx):
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