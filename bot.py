# Import dependencies (make sure requirements.txt includes these)

import discord                                              # Discord API
from discord.ext import commands
from discord_components import Button, DiscordComponents
import pymysql                                              # Connect to AWS SQL
import aiocron                                              # Schedule events
import os, string, time, random, asyncio                    # Core python libraries
import pandas as pd                                         # Manipulate tabular data
from chatgpt_functions import *                             # function calls for ChatGPT API
from dotenv import load_dotenv                              # Load .env

flag_first=True                                             # initialize first flag
load_dotenv()
intents = discord.Intents.all()                                             # load .env
bot = commands.Bot(intents=intents,command_prefix='_')                      # structure for bot commands
discord.Intents.all()
discord.MemberCacheFlags.all()
DiscordComponents(bot)                                      # structure for buttons
bot.remove_command('help')                                  # remove default help command

# Bot Commands

@bot.command(name = 'help')
async def a_help(ctx):
    # provides an embed of all availble commands
    embed=discord.Embed(title="Commands", color=0x395060)
    embed.add_field(name="_1st", value="Try for first", inline=True)
    embed.add_field(name="_score", value="First leaderboard", inline=True)
    embed.add_field(name="_ask", value="Ask ChatGPT", inline=True)
    embed.add_field(name="_hello", value="Say hi", inline=True)
    embed.add_field(name="_dash", value="Server stats", inline=True)
    embed.add_field(name="_simonsays", value="I'll repeat after you", inline=True)
    embed.add_field(name="_juice", value="Juice board", inline=True)
    embed.add_field(name="_donation", value="Our patrons", inline=True)
    await ctx.channel.send(embed=embed)        

@bot.command()
async def score(ctx,pass_context=True):
    # reads SQL database and generates an embed with list of names and scores
    df = get_db('firstlist_id')
    streak = get_streak(df)
    counts = df.user_id.value_counts()
    embed=discord.Embed(title='First Leaderboard',description="Count of daily 1st wins",color=0x395060)
    for i in range(5):  # display top 5
        embed.add_field(name=bot.get_user(int(counts.index[i])),
                        value=counts[i],
                        inline=False)
    txt = f'Most recent: {bot.get_user(int(df.user_id.iloc[-1]))} 🔥 {streak} days'
    embed.set_footer(text=txt)
    await ctx.channel.send(embed=embed) 

@bot.command()
async def donation(ctx):
    # provides embed of all donations
    embed=discord.Embed(title='Donation Board',description='Thank you to our generous patrons!',color=0x395060)
    embed.add_field(name='Mike S',value='$8.01',inline=False)
    embed.add_field(name='Danny E',value='$8.00',inline=False)
    embed.add_field(name='Sammy T',value='$6.90',inline=False)
    embed.add_field(name='Jacky P',value='$6.69',inline=False)
    embed.add_field(name='Matt',value='$6.00',inline=False)
    embed.add_field(name='Whike',value='$6.00',inline=False)
    embed.set_footer(text='Peter Dinklage is a non-profit')
    await ctx.channel.send(embed=embed)    

@bot.command()
async def juice(ctx, pass_context=True):
    # reads SQL database and send embed of total minutes between each "1st" timestamp and midnight
    df = get_db('firstlist_id')
    df_juice,highscore_user_id,val = get_juice(df)
    value = int(val)
    embed=discord.Embed(title='Juice Board 🧃',description='Total minutes between _1st and midnight',color=0x395060)
    for i in range(5):
        embed.add_field(name=bot.get_user(int(df_juice.iloc[i][0])),value=int(df_juice.iloc[i][1]),inline=False)
    txt = f'1-Day Highscore: {bot.get_user(int(highscore_user_id))}🧃{value} mins'
    embed.set_footer(text=txt)
    await ctx.channel.send(embed=embed)

@bot.command()
async def dash(ctx):
    # sends button with hyperlink to server dashboard
    await ctx.send('Here you go!', components=[Button(label="Go to your dashboard",style=5,url='https://peterdinklage.streamlit.app/')])

@bot.command(pass_context=True)
async def hello(ctx):
    # response with name of the message author
    Author = ctx.author.mention
    msg = f'Hello {Author}!'
    await ctx.channel.send(msg)

@bot.command()
async def ping(ctx):
    await ctx.channel.send('pong')

@bot.command()
async def simonsays(ctx, *, arg):
    # repeats string back
    await ctx.channel.send(arg)
 
chat_history = [{"role": "system", "content": "Respond as if you are a Scandanavian viking. You have a flamboyant personality that is characteristic of members of the LGBT community. Speak with a lot of exclamatory sentences and use words like yas queen slay. Try to maintain both your flamboyant personality and viking role."}]
@bot.command()
async def ask(ctx,*, arg, pass_context=True):
    # Passes prompt to ChatGPT API and returns response
    global chat_history
    if str(ctx.message.author.id) in IDCARD:
        async with ctx.typing():
            chat_history, response = call_chatGPT(chat_history, arg)
        await ctx.send(response)
    else:
        await ctx.channel.send('To conserve compute resources, only specific users can use _ask')


@bot.command(name='1st', pass_context=True)
async def first(ctx):
    # Checks if first has been claimed, if not, writes user_id and timestamp to SQL database
    global flag_first
    if flag_first==True:
        Author = ctx.author.mention
        msg = f'Sorry {Author}, first has already been claimed today. 😭'
        await ctx.channel.send(msg)
    else:
        flag_first=True
        Author = ctx.author.mention
        msg = f'{Author} is first today! 🥳'
        await ctx.channel.send(msg)
        write_to_db('firstlist_id', ctx.author.id)

# Display in console bot is working correctly
@bot.event
async def on_ready():
    print("Live: " + bot.user.name)
    DiscordComponents(bot)

# Cron job to reset first flag each night
# EST time zone offset is UTC-05. EDT time zone offset is UTC-04.
@aiocron.crontab('00 05 * * *') # (minute, hour, day, month, dayOfWeek) UTC Time
async def cronjob1():
    global flag_first
    flag_first = False
    print('flag_first reset')

# Function definitions for reading, writing, and manipulating the data in SQL database

def connect_db():
    # connect to database
    host = os.getenv('SQL_HOST')
    user = os.getenv('SQL_USER')
    password = os.getenv('SQL_PASSWORD')
    database = os.getenv('SQL_DATABASE')
    conn = pymysql.connect(host=host, user=user,password=password)
    cursor = conn.cursor()
    cursor.execute(f'use {database}')
    return conn,cursor

def write_to_db(table_name, value):
    # write to server and close connection
    conn,cursor = connect_db()
    query = f'INSERT INTO {table_name} (user_id) VALUES (\'{value}\');'
    cursor.execute(query)
    conn.commit()         
    cursor.close()
    conn.close()

def get_db(tablename):
    # get table as pandas df and close connection
    conn,cursor = connect_db()
    query = f'SELECT * FROM {tablename}'
    df = pd.read_sql_query(query, conn)
    cursor.close()
    conn.close()
    return df

def get_streak(df):
    # find streak of repeated user_ids
    df['start_of_streak'] = df.user_id.ne(df['user_id'].shift())
    df['streak_id'] = df['start_of_streak'].cumsum()
    df['streak_counter'] = df.groupby('streak_id').cumcount() + 1

    return df.streak_counter.iloc[-1]

def get_juice(df):
    # localize to UTC time and convert to EST
    df['timesent'] = df['timesent'].dt.tz_localize('utc').dt.tz_convert('US/Eastern')
    hours = df['timesent'].dt.hour
    minutes = df['timesent'].dt.minute
    seconds = df['timesent'].dt.second
    total_mins = (seconds/60)+minutes+(hours*60)
    df['Juice'] = total_mins
    # Get highscore value
    id = df['Juice'].idxmax()
    highscore_user = df.iloc[id][0]
    highscore_value = df.iloc[id][2]
    df_grouped = df[['user_id','Juice']].groupby('user_id',as_index=False).sum()
    df_juice = df_grouped.sort_values('Juice',ascending=False).iloc[0:len(df_grouped)]

    return df_juice,highscore_user,highscore_value

TOKEN = os.getenv('DISCORD_TOKEN')
bot.run(TOKEN)
