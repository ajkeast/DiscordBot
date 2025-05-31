import discord
from discord.ext import commands
from utils.db import update_sql_members, update_sql_emojis, update_sql_channels

class Server(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def members(self, ctx):
        # updates database with all current members of the server and their info
        members = ctx.guild.members
        vals=[]
        for member in members:
            id = int(member.id)
            user = self.bot.get_user(id)
            avatar = f'https://cdn.discordapp.com/avatars/{id}/{user.avatar}.webp?size=128'
            user_name = user.name
            display_name = member.nick
            created_at = user.created_at.strftime("%Y-%m-%d %H:%M:%S")
            vals.append([id,user_name,display_name,avatar,created_at])
            
        update_sql_members(vals)    # write to database
        await ctx.channel.send("Member info succesfully updated.")

    @commands.command()
    async def emojis(self, ctx):
        # updates database with all current emojis in the server
        vals=[]
        for emoji in ctx.guild.emojis:
            id = emoji.id
            emoji_name = emoji.name
            guild_id = emoji.guild_id
            url = emoji.url
            created_at = emoji.created_at.strftime("%Y-%m-%d %H:%M:%S")
            vals.append([id,emoji_name,guild_id,url,created_at])
        
        update_sql_emojis(vals)
        await ctx.channel.send("Emoji info succesfully updated.")

    @commands.command()
    async def channels(self, ctx):
        # updates database with all current channels on the server
        vals=[]
        for channel in ctx.guild.channels:
            id = channel.id
            name = channel.name
            created_at = channel.created_at
            vals.append([id,name,created_at])
        
        update_sql_channels(vals)
        await ctx.channel.send("Channel info succesfully updated.")

async def setup(bot):
    await bot.add_cog(Server(bot)) 