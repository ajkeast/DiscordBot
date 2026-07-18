from discord import app_commands
from discord.ext import commands
from utils.views import dinkscord_link_view


class Utility(commands.Cog):
    """Simple utility commands."""

    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(brief='Say hello')
    async def hello(self, ctx):
        """Say hello."""
        Author = ctx.author.mention
        msg = f'Hello {Author}!'
        await ctx.send(msg)

    @commands.hybrid_command(brief='Check if the bot is online')
    async def ping(self, ctx):
        """Check if the bot is online."""
        await ctx.send('pong')

    @commands.hybrid_command(brief='Make the bot repeat your message')
    @app_commands.describe(message='The message for the bot to repeat')
    async def simonsays(self, ctx, *, message: str):
        """Make the bot repeat your message."""
        await ctx.send(message)

    @commands.hybrid_command(brief='Link to the Dinkscord dashboard')
    async def dashboard(self, ctx):
        """Share a link button to the public Dinkscord dashboard."""
        # Discord requires message content; zero-width space keeps it button-only.
        await ctx.send("\u200b", view=dinkscord_link_view())


async def setup(bot):
    await bot.add_cog(Utility(bot))
