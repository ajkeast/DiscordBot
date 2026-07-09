from discord.ext import commands

class Utility(commands.Cog):
    """Simple utility commands."""
    
    def __init__(self, bot):
        self.bot = bot

    @commands.command(brief='Say hello')
    async def hello(self, ctx):
        """Say hello."""
        Author = ctx.author.mention
        msg = f'Hello {Author}!'
        await ctx.channel.send(msg)

    @commands.command(brief='Check if the bot is online')
    async def ping(self, ctx):
        """Check if the bot is online."""
        await ctx.channel.send('pong')

    @commands.command(brief='Make the bot repeat your message')
    async def simonsays(self, ctx, *, arg):
        """Make the bot repeat your message."""
        await ctx.channel.send(arg)

async def setup(bot):
    await bot.add_cog(Utility(bot)) 