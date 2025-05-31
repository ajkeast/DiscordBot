import discord
from discord.ext import commands

class Utility(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='help')
    async def help(self, ctx):
        # provides an embed of all availble commands
        embed=discord.Embed(title="Commands", color=0x4d4170)
        embed.add_field(name="_1st", value="Try for first", inline=True)
        embed.add_field(name="_score", value="First leaderboard", inline=True)
        embed.add_field(name="_ask", value="Ask ChatGPT", inline=True)
        embed.add_field(name="_hello", value="Say hi", inline=True)
        embed.add_field(name="_dash", value="Server dashboard", inline=True)
        embed.add_field(name="_simonsays", value="I'll repeat after you", inline=True)
        embed.add_field(name="_juice", value="Juice board", inline=True)
        embed.add_field(name="_donation", value="Our patrons", inline=True)
        embed.add_field(name="_imagine", value="Generate AI images", inline=True)
        embed.add_field(name="_stats", value="Individual user stats", inline=True)
        await ctx.channel.send(embed=embed)

    @commands.command(pass_context=True)
    async def hello(self, ctx):
        # response with name of the message author
        Author = ctx.author.mention
        msg = f'Hello {Author}!'
        await ctx.channel.send(msg)

    @commands.command()
    async def ping(self, ctx, brief='Ping the bot'):
        # response with pong
        await ctx.channel.send('pong')

    @commands.command()
    async def simonsays(self, ctx, *, arg, pass_context=True, brief='I will repeat after you'):
        # repeats string back
        await ctx.channel.send(arg)

async def setup(bot):
    await bot.add_cog(Utility(bot)) 