import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import io
import logging
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from datetime import datetime
import pytz
from utils.db import db_ops, streak_calc, juice_calc
from utils.constants import (
    GENERAL_CHANNEL_ID,
    EMBED_COLOR,
    DINK_MINT_AMOUNT,
    DINKSCORD_URL,
    PROMOTE_DINKSCORD_ON_FIRST,
)
from utils.interactions import acknowledge
from utils.views import dinkscord_link_view

logger = logging.getLogger(__name__)

class First(commands.Cog):
    """Daily firsts game and related stats."""
    
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='1st', brief='Claim first for today')
    async def first(self, ctx):
        """Claim first for today. Only works in #general, once per day."""
        # Checks if first has been claimed, if not, writes user_id and timestamp to SQL database
        channel_id = GENERAL_CHANNEL_ID    # dinkscord general text-channel id
        if ctx.channel.id != channel_id:
            msg = f'Please send your message to <#{channel_id}>.'
            await ctx.send(msg)
        else:
            utc_now = datetime.utcnow()
            utc_now = pytz.timezone('UTC').localize(utc_now).astimezone(pytz.timezone('US/Eastern'))

            df = db_ops.get_table_data('firstlist_id')
            already_claimed = False
            if not df.empty:
                timestamp_most_recent = df['timesent'].iloc[-1].to_pydatetime()
                timestamp_most_recent = pytz.timezone('UTC').localize(timestamp_most_recent).astimezone(pytz.timezone('US/Eastern'))
                already_claimed = utc_now.strftime("%Y-%m-%d") == timestamp_most_recent.strftime("%Y-%m-%d")

            if already_claimed:
                Author = ctx.author.mention
                msg = f'Sorry {Author}, first has already been claimed today. 😭'
                await ctx.send(msg)
            else:
                db_ops.write_first_entry(ctx.author.id)
                db_ops.record_dink_mint(ctx.author.id, DINK_MINT_AMOUNT)
                await asyncio.sleep(0.5)
                Author = ctx.author.mention
                msg = f"{Author} is first today! 🥳 +{DINK_MINT_AMOUNT:g} **DINK**"
                if PROMOTE_DINKSCORD_ON_FIRST:
                    msg += (
                        f"\nCheck out the recently launched website for Peter Dinklage: "
                        f"{DINKSCORD_URL}"
                    )
                    await ctx.send(msg, view=dinkscord_link_view())
                else:
                    await ctx.send(msg)

    @commands.hybrid_command(brief='Show the firsts leaderboard')
    async def score(self, ctx):
        """Show the firsts leaderboard and current streak."""
        # reads SQL database and generates an embed with list of names and scores
        df = db_ops.get_table_data('firstlist_id')
        if df.empty:
            await ctx.send("No firsts recorded yet — claim one with `/1st`!")
            return

        streak = streak_calc.calculate_streak(df)
        counts = df.user_id.value_counts()
        embed=discord.Embed(title='First Leaderboard',description="Count of daily 1st wins",color=EMBED_COLOR)
        for i in range(min(5, len(counts))):
            embed.add_field(name=self.bot.get_user(int(counts.index[i])),
                            value=counts.iloc[i],
                            inline=False)
        txt = f'Most recent: {self.bot.get_user(int(df.user_id.iloc[-1]))} 🔥 {streak} days'
        embed.set_footer(text=txt)
        await ctx.send(embed=embed)

    @commands.hybrid_command(brief='Show firsts stats for a user')
    @app_commands.describe(member='User to look up (defaults to you)')
    async def stats(self, ctx, member: discord.Member = None):
        """Show score, juice, and streak for you or another user."""
        # reads SQL database and generates an embed with list of names and scores
        df = db_ops.get_table_data('firstlist_id')

        if member is not None:
            author_id = str(member.id)
        elif ctx.message.mentions:
            author_id = str(ctx.message.mentions[0].id)
        else:
            author_id = str(ctx.author.id)

        try:
            author = self.bot.get_user(int(author_id))
            # Check if user has any entries
            user_entries = df[df.user_id == author_id]
            if user_entries.empty:
                await ctx.send('This user has never gotten a first!')
                return

            streak = streak_calc.calculate_user_streak(df, author_id)
            score = len(user_entries)
            juice = juice_calc.calculate_user_juice(df, author_id)

            embed=discord.Embed(title=author, description="Your server statistics", color=EMBED_COLOR)
            embed.set_thumbnail(url=str(author.display_avatar.with_size(128)))
            embed.add_field(name="Score", value=f'{score} 🏆', inline=True)
            embed.add_field(name="Juice", value=f'{int(juice)} 🧃', inline=True)
            embed.add_field(name="Longest streak", value=f'{streak} days 🔥', inline=True)

            await ctx.send(embed=embed)
        except ValueError:
            logger.exception("Invalid user ID in stats command: %s", author_id)
            await ctx.send('Error: Invalid user ID format.')
        except Exception:
            logger.exception("Unexpected error in stats command for user %s", author_id)
            await ctx.send('An unexpected error occurred while fetching stats.')

    @commands.hybrid_command(brief='Show the juice leaderboard')
    async def juice(self, ctx):
        """Show the juice leaderboard and one-day high score."""
        df = db_ops.get_table_data('firstlist_id')
        if df.empty:
            await ctx.send("No firsts recorded yet — claim one with `/1st`!")
            return

        juice_df, highscore_user_id, highscore_value = juice_calc.calculate_juice(df)
        
        embed=discord.Embed(title='Juice Board 🧃',description='Total minutes between /1st and midnight 🧃',color=EMBED_COLOR)
        for i in range(min(5, len(juice_df))):
            embed.add_field(name=self.bot.get_user(int(juice_df.iloc[i]['user_id'])),
                          value=int(juice_df.iloc[i]['juice']),
                          inline=False)
        txt = f'1-Day Highscore: {self.bot.get_user(int(highscore_user_id))}🧃{int(highscore_value)} mins'
        embed.set_footer(text=txt)
        await ctx.send(embed=embed)

    @commands.hybrid_command(brief='Graph firsts over time')
    async def graph(self, ctx):
        """Graph firsts over time."""
        async with acknowledge(ctx):
            df_first = db_ops.get_table_data('firstlist_id')
            if df_first.empty:
                await ctx.send("No firsts recorded yet — claim one with `/1st`!")
                return

            df_first['_1st to date'] = df_first.groupby('user_id').cumcount() + 1
            bg, grid, text = '#2b2d31', '#404249', '#dcddde'
            plt.rcParams.update({
                'figure.facecolor': bg, 'axes.facecolor': bg, 'axes.edgecolor': grid,
                'axes.labelcolor': text, 'text.color': text, 'xtick.color': text,
                'ytick.color': text, 'grid.color': grid, 'grid.alpha': 0.45,
            })

            fig, ax = plt.subplots(figsize=(12, 7))
            colors = plt.cm.tab10.colors

            for i, user_id in enumerate(df_first.groupby('user_id').size().sort_values(ascending=False).index):
                data = df_first[df_first['user_id'] == user_id]
                user = self.bot.get_user(int(user_id))
                ax.plot(
                    data['timesent'], data['_1st to date'],
                    label=user.display_name if user else f"User {user_id}",
                    color=colors[i % len(colors)], linewidth=2.5,
                )

            ax.set(ylabel='Cumulative Firsts', title='Firsts to Date')
            ax.grid(True, linestyle='--')
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
            fig.autofmt_xdate(rotation=30, ha='right')
            ax.legend(loc='upper left', frameon=False)

            data_stream = io.BytesIO()
            fig.savefig(data_stream, format='png', bbox_inches='tight', dpi=150, facecolor=bg)
            plt.close(fig)

            data_stream.seek(0)
            embed = discord.Embed(title='Firsts to Date', color=EMBED_COLOR)
            embed.set_image(url="attachment://first_graph.png")
            await ctx.send(embed=embed, file=discord.File(data_stream, filename="first_graph.png"))

    @commands.hybrid_command(brief='Graph daily juice over time')
    async def juicegraph(self, ctx):
        """Graph daily juice over time."""
        async with acknowledge(ctx):
            df_first = db_ops.get_table_data('firstlist_id')
            if df_first.empty:
                await ctx.send("No firsts recorded yet — claim one with `/1st`!")
                return

            df_juice = juice_calc.daily_juice_series(df_first)
            _, highscore_user_id, highscore_value = juice_calc.calculate_juice(df_first)

            bg, grid, text = '#2b2d31', '#404249', '#dcddde'
            plt.rcParams.update({
                'figure.facecolor': bg, 'axes.facecolor': bg, 'axes.edgecolor': grid,
                'axes.labelcolor': text, 'text.color': text, 'xtick.color': text,
                'ytick.color': text, 'grid.color': grid, 'grid.alpha': 0.45,
            })

            fig, ax = plt.subplots(figsize=(12, 7))
            ax.plot(
                df_juice['timesent'], df_juice['juice'],
                color=f'#{EMBED_COLOR:06x}', linewidth=2.5,
            )

            ax.set(ylabel='Juice', title='Daily Juice')
            ax.grid(True, linestyle='--')
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
            fig.autofmt_xdate(rotation=30, ha='right')

            data_stream = io.BytesIO()
            fig.savefig(data_stream, format='png', bbox_inches='tight', dpi=150, facecolor=bg)
            plt.close(fig)

            data_stream.seek(0)
            highscore_user = self.bot.get_user(int(highscore_user_id))
            highscore_name = highscore_user.display_name if highscore_user else f"User {highscore_user_id}"
            embed = discord.Embed(title='Daily Juice', color=EMBED_COLOR)
            embed.set_footer(text=f'1-Day Highscore: {highscore_name} 🧃{int(highscore_value)} mins')
            embed.set_image(url="attachment://juice_graph.png")
            await ctx.send(embed=embed, file=discord.File(data_stream, filename="juice_graph.png"))

async def setup(bot):
    await bot.add_cog(First(bot))
