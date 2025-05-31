from discord.ext import commands

class Utility(commands.Cog):
    """A cog containing basic utility commands for user interaction."""
    
    def __init__(self, bot):
        self.bot = bot

    @commands.command(pass_context=True)
    async def hello(self, ctx):
        """Send a friendly greeting mentioning the user.
        
        Args:
            ctx: The command context
        """
        # response with name of the message author
        Author = ctx.author.mention
        msg = f'Hello {Author}!'
        await ctx.channel.send(msg)

    @commands.command()
    async def ping(self, ctx, brief='Ping the bot'):
        """Check if the bot is responsive.
        
        Args:
            ctx: The command context
            
        Responds with 'pong' to indicate the bot is active.
        """
        # response with pong
        await ctx.channel.send('pong')

    @commands.command()
    async def simonsays(self, ctx, *, arg, pass_context=True, brief='I will repeat after you'):
        """Repeat the message sent by the user.
        
        Args:
            ctx: The command context
            arg: The message to repeat
        """
        # repeats string back
        await ctx.channel.send(arg)

async def setup(bot):
    await bot.add_cog(Utility(bot)) 