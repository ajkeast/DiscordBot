import discord
from discord.ext import commands

class Misc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def donation(self, ctx, brief='Get a list of all donations'):
        # provides embed of all donations
        embed=discord.Embed(title='Donation Board',description='Thank you to our generous patrons!',color=0x4d4170)
        embed.add_field(name='Sammy T',value=f'${(6.90+14.20):.2f}',inline=False)
        embed.add_field(name='Matt',value=f'${(6.00+8.91):.2f}',inline=False)
        embed.add_field(name='Danny E',value=f'${(8.00+6.90):.2f}',inline=False)
        embed.add_field(name='Mike S',value=f'${(8.01+6.68):.2f}',inline=False)
        embed.add_field(name='Jacky P',value=f'${(6.69):.2f}',inline=False)
        embed.add_field(name='Whike',value=f'${(6.00):.2f}',inline=False)
        embed.set_footer(text='Peter Dinklage is a non-profit')
        await ctx.channel.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Misc(bot)) 