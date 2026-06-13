import discord
from discord.ext import commands
import io
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from datetime import datetime
import pytz
import time
from utils.db import db_ops, streak_calc, juice_calc
from utils.constants import GENERAL_CHANNEL_ID, EMBED_COLOR

class First(commands.Cog):
    """A cog that manages the daily 'first' claiming game and related statistics."""
    
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='1st', brief='Claim your first today')
    async def first(self, ctx):
        """Attempt to claim first place for the day.
        
        Args:
            ctx: The command context
            
        The command only works in the designated general channel.
        First can only be claimed once per day (Eastern Time).
        Successful claims are recorded in the database.
        """
        # Checks if first has been claimed, if not, writes user_id and timestamp to SQL database
        channel_id = GENERAL_CHANNEL_ID    # dinkscord general text-channel id
        if ctx.channel.id != channel_id:
            msg = f'Please send your message to <#{channel_id}>.'
            await ctx.channel.send(msg)
        else:
            utc_now = datetime.utcnow()
            utc_now = pytz.timezone('UTC').localize(utc_now).astimezone(pytz.timezone('US/Eastern'))

            df = db_ops.get_table_data('firstlist_id')
            timestamp_most_recent = df['timesent'].iloc[-1].to_pydatetime()
            timestamp_most_recent = pytz.timezone('UTC').localize(timestamp_most_recent).astimezone(pytz.timezone('US/Eastern'))
            
            if utc_now.strftime("%Y-%m-%d") == timestamp_most_recent.strftime("%Y-%m-%d"):
                Author = ctx.author.mention
                msg = f'Sorry {Author}, first has already been claimed today. 😭'
                await ctx.channel.send(msg)
            else:
                db_ops.write_first_entry(ctx.author.id)
                time.sleep(0.5)
                Author = ctx.author.mention
                msg = f'{Author} is first today! 🥳'
                await ctx.channel.send(msg)

    @commands.command()
    async def score(self, ctx, pass_context=True, brief='Count of daily 1st wins'):
        """Display the leaderboard of first place claims.
        
        Args:
            ctx: The command context
            
        Shows:
        - Top 5 users by number of first place claims
        - Most recent winner and their current streak
        """
        # reads SQL database and generates an embed with list of names and scores
        df = db_ops.get_table_data('firstlist_id')
        streak = streak_calc.calculate_streak(df)
        counts = df.user_id.value_counts()
        embed=discord.Embed(title='First Leaderboard',description="Count of daily 1st wins",color=EMBED_COLOR)
        for i in range(5):  # display top 5
            embed.add_field(name=self.bot.get_user(int(counts.index[i])),
                            value=counts.iloc[i],
                            inline=False)
        txt = f'Most recent: {self.bot.get_user(int(df.user_id.iloc[-1]))} 🔥 {streak} days'
        embed.set_footer(text=txt)
        await ctx.channel.send(embed=embed)

    @commands.command()
    async def stats(self, ctx, *, args=None, pass_context=True, brief='Get an individual users stats'):
        """Display detailed statistics for a specific user.
        
        Args:
            ctx: The command context
            args: Optional mention of another user to view their stats
            
        Shows:
        - Total first place claims (Score)
        - Juice score (minutes since midnight, rolling over missed days)
        - Longest streak of consecutive first places
        """
        # reads SQL database and generates an embed with list of names and scores
        df = db_ops.get_table_data('firstlist_id')

        if len(ctx.message.mentions) > 0:
            author_id = str(ctx.message.mentions[0].id)
        else:
            author_id = str(ctx.message.author.id)

        try:
            author = self.bot.get_user(int(author_id))
            # Check if user has any entries
            user_entries = df[df.user_id == author_id]
            if user_entries.empty:
                await ctx.channel.send('This user has never gotten a first!')
                return

            streak = streak_calc.calculate_user_streak(df, author_id)
            score = len(user_entries)
            juice = juice_calc.calculate_user_juice(df, author_id)

            embed=discord.Embed(title=author, description="Your server statistics", color=EMBED_COLOR)
            embed.set_thumbnail(url=str(author.display_avatar.with_size(128)))
            embed.add_field(name="Score", value=f'{score} 🏆', inline=True)
            embed.add_field(name="Juice", value=f'{int(juice)} 🧃', inline=True)
            embed.add_field(name="Longest streak", value=f'{streak} days 🔥', inline=True)

            print(str(author.display_avatar.with_size(128)))
            await ctx.channel.send(embed=embed)
        except ValueError as ve:
            print(f"ValueError in stats command: {str(ve)}")
            await ctx.channel.send('Error: Invalid user ID format.')
        except Exception as e:
            print(f"Unexpected error in stats command: {type(e).__name__}: {str(e)}")
            await ctx.channel.send('An unexpected error occurred while fetching stats.')

    @commands.command()
    async def juice(self, ctx, pass_context=True, brief='Get the server juice scores'):
        """Display the juice leaderboard showing time efficiency of first claims.
        
        Args:
            ctx: The command context
            
        Shows:
        - Top 5 users by total juice score
        - The highest single-day juice score and its holder
        
        Juice score is minutes since midnight Eastern, with missed days rolling over.
        """
        df = db_ops.get_table_data('firstlist_id')
        juice_df, highscore_user_id, highscore_value = juice_calc.calculate_juice(df)
        
        embed=discord.Embed(title='Juice Board 🧃',description='Total minutes between _1st and midnight 🧃',color=EMBED_COLOR)
        for i in range(5):
            embed.add_field(name=self.bot.get_user(int(juice_df.iloc[i]['user_id'])),
                          value=int(juice_df.iloc[i]['juice']),
                          inline=False)
        txt = f'1-Day Highscore: {self.bot.get_user(int(highscore_user_id))}🧃{int(highscore_value)} mins'
        embed.set_footer(text=txt)
        await ctx.channel.send(embed=embed)

    @commands.command()
    async def graph(self, ctx, brief='Get a graph of the firsts to date'):
        """Generate and send a graph showing the progression of first claims over time."""
        df_first = db_ops.get_table_data('firstlist_id')
        if df_first.empty:
            await ctx.send("No firsts recorded yet — claim one with `!1st`!")
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

    @commands.command()
    async def juicegraph(self, ctx, brief='Get a graph of daily juice over time'):
        """Generate and send a graph showing daily juice scores over time."""
        df_first = db_ops.get_table_data('firstlist_id')
        if df_first.empty:
            await ctx.send("No firsts recorded yet — claim one with `_1st`!")
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