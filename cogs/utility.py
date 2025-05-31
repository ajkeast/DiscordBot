import discord
from discord.ext import commands

class Utility(commands.Cog):
    """A cog containing basic utility commands for user interaction."""
    
    def __init__(self, bot):
        self.bot = bot

    # @commands.command(name='help')
    # async def help(self, ctx):
    #     """Display a list of all available bot commands.
        
    #     Args:
    #         ctx: The command context
            
    #     Shows an embed containing:
    #     - Command names
    #     - Brief descriptions of each command's function
    #     """
    #     embed = discord.Embed(title="Commands", color=0x4d4170)
        
    #     # Iterate through all cogs
    #     for cog_name, cog in self.bot.cogs.items():
    #         # Get all commands from the cog that the user can use
    #         commands_list = [cmd for cmd in cog.get_commands() if cmd.hidden is False]
    #         if commands_list:
    #             # Add a field for each cog with its commands
    #             for cmd in commands_list:
    #                 name = f"_{cmd.name}" if not cmd.name.startswith('_') else cmd.name
    #                 value = cmd.brief or cmd.help.split('\n')[0]  # Use brief if available, otherwise first line of help
    #                 embed.add_field(name=name, value=value, inline=True)
        
    #     await ctx.send(embed=embed)

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