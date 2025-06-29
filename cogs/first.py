import discord
from discord.ext import commands
import io
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
                            value=counts[i],
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
        - Juice score (minutes saved from midnight)
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
        
        Juice score is calculated as minutes between claim time and midnight.
        """
        # reads SQL database and send embed of total minutes between each "1st" timestamp and midnight
        df = db_ops.get_table_data('firstlist_id')
        juice_df, highscore_user_id, highscore_value = juice_calc.calculate_juice(df)
        
        embed=discord.Embed(title='Juice Board 🧃',description='Total minutes between _1st and midnight',color=EMBED_COLOR)
        for i in range(5):
            embed.add_field(name=self.bot.get_user(int(juice_df.iloc[i]['user_id'])),
                          value=int(juice_df.iloc[i]['juice']),
                          inline=False)
        txt = f'1-Day Highscore: {self.bot.get_user(int(highscore_user_id))}🧃{int(highscore_value)} mins'
        embed.set_footer(text=txt)
        await ctx.channel.send(embed=embed)

    @commands.command()
    async def graph(self, ctx, brief='Get a graph of the firsts to date'):
        """Generate and send a graph showing the progression of first claims over time.
        
        Args:
            ctx: The command context
            
        Creates a line chart where:
        - X-axis represents dates
        - Y-axis shows cumulative number of firsts
        - Each user has their own line
        """
        # Initialize IO
        data_stream = io.BytesIO()

        df_first = db_ops.get_table_data('firstlist_id')
        df_first['_1st to date'] = df_first.groupby('user_id').cumcount()+1

        # Initiate plot
        fig, ax = plt.subplots(figsize=(8, 6))

        # Group the DataFrame by 'user_id'
        grouped_data = df_first.groupby('user_id')

        # Iterate over each unique 'user_id' and plot the corresponding data
        for user_id, data in grouped_data:
            # Extract x-axis and y-axis values for the current 'user_id'
            x_values = data['timesent']
            y_values = data['_1st to date']

            # Plot the line chart for the current 'user_id'
            ax.plot(x_values, y_values, label=f'User ID: {user_id}')

        # Customize the plot as needed
        ax.set_xlabel('Date')
        ax.set_ylabel('# of firsts')
        ax.set_title('Firsts to Date')

        plt.savefig(data_stream, format='png', bbox_inches="tight", dpi = 80)

        ## Create file
        # Reset point back to beginning of stream
        data_stream.seek(0)
        chart = discord.File(data_stream,filename="first_graph.png")
        embed = discord.Embed(title='Firsts to Date',color=EMBED_COLOR)
        embed.set_image(url="attachment://first_graph.png")

        await ctx.send(embed=embed, file=chart)

async def setup(bot):
    await bot.add_cog(First(bot)) 