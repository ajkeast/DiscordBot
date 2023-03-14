# Import dependencies (make sure requirements.txt includes these)

import discord                                              # Discord API
from discord.ext import commands
from discord_components import Button, DiscordComponents
import pymysql                                              # Connect to AWS SQL
import aiocron                                              # Schedule events
import os, string, time, random, asyncio                    # Core python libraries
import pandas as pd                                         # Manipulate tabular data
import openai                                               # ChatGPT API
from dotenv import load_dotenv                              # Load .env

flag_first=True                         # initialize first flag
load_dotenv()                           # load .env
bot = commands.Bot(command_prefix='_')  # structure for bot commands
DiscordComponents(bot)                  # structure for buttons
bot.remove_command('help')              # remove default help command

# Bot Commands

@bot.command(name = 'help')
async def a_help(ctx):
    embed=discord.Embed(title="Commands", color=0x395060)
    embed.add_field(name="_1st", value="Try for first", inline=True)
    embed.add_field(name="_score", value="First leaderboard", inline=True)
    embed.add_field(name="_hello", value="Say hi", inline=True)
    embed.add_field(name="_dash", value="Server stats", inline=True)
    embed.add_field(name="_simonsays", value="I'll repeat after you", inline=True)
    embed.add_field(name="_juice", value="Juice board", inline=True)
    embed.add_field(name="_donation", value="Our patrons", inline=True)
    await ctx.channel.send(embed=embed)        

@bot.command()
async def score(ctx):
    df = get_db('firstlist')
    streak = get_streak(df)
    counts = df.username.value_counts()
    embed=discord.Embed(title='First Leaderboard',description="Count of daily 1st wins",color=0x395060)
    for i in range(5):  # display top 5
        embed.add_field(name=counts.index[i],value=counts[i],inline=False)
    txt = f'Most recent: {df.username.iloc[-1]} 🔥 {streak} days'
    embed.set_footer(text=txt)
    await ctx.channel.send(embed=embed) 

@bot.command()
async def donation(ctx):
    embed=discord.Embed(title='Donation Board',description='Thank you to our generous patrons!',color=0x395060)   
    embed.add_field(name='Goat 🤠#4059',value='$8.00',inline=False)
    embed.add_field(name='SamtyClaws#7243',value='$6.90',inline=False)
    embed.add_field(name='jack phelps#4293',value='$6.69',inline=False)
    embed.add_field(name='Mo#8516',value='$6.00',inline=False)
    embed.add_field(name='tornadotom50#8420',value='$6.00',inline=False)
    embed.set_footer(text='Peter Dinklage is a non-profit')
    await ctx.channel.send(embed=embed)    

@bot.command()
async def juice(ctx):
    df = get_db('firstlist')
    df_juice,user,val = get_juice(df)
    value = int(val)
    embed=discord.Embed(title='Juice Board 🧃',description='Total minutes between _1st and midnight',color=0x395060)
    for i in range(5):
        embed.add_field(name=df_juice.iloc[i][0],value=int(df_juice.iloc[i][1]),inline=False)
    txt = f'1-Day Highscore: {user}🧃{value} mins'
    embed.set_footer(text=txt)
    await ctx.channel.send(embed=embed)

@bot.command()
async def dash(ctx):
    await ctx.send('Here you go!', components=[Button(label="Go to your dashboard",style=5,url='https://peterdinklage.streamlit.app/')])

@bot.command(pass_context=True)
async def hello(ctx):
    Author = ctx.author.mention
    msg = f'Hello {Author}!'
    await ctx.channel.send(msg)

@bot.command()
async def ping(ctx):
	await ctx.channel.send('pong')

@bot.command()
async def simonsays(ctx, *args):
	response = ""
	for arg in args:
		response = response + " " + arg
	await ctx.channel.send(response)

IDCARD = ['ConKeastador#0784','Mo#8516','SamtyClaws#7243','Frozen Tofu#8827','jack phelps#4293','tornadotom50#8420'] 
openai.api_key = os.getenv('CHAT_API_KEY')
model_engine = 'gpt-3.5-turbo'
max_tokens = 256
@bot.command()
async def ask(ctx, *args, pass_context=True):
    if str(ctx.message.author) in IDCARD:
        async with ctx.typing():
            prompt = ""
            for arg in args:
                prompt = prompt + " " + arg
            response = openai.ChatCompletion.create(model="gpt-3.5-turbo",
                                                    messages=[{"role": "system", "content": "You are a helpful assistant."},
                                                              {"role": "user", "content": prompt}])
        await ctx.send(response['choices'][0]['message']['content'])    
    else:
        await ctx.channel.send('To conserve compute resources, only specific users can use _ask')


@bot.command(name='1st', pass_context=True)
async def first(ctx):
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
        write_to_db(ctx.author)

# Display in console bot is working correctly
@bot.event
async def on_ready():
    print("Live: " + bot.user.name)
    DiscordComponents(bot)

# Cron job to reset first flag each night
# EST time zone offset is UTC-05. EDT time zone offset is UTC-04.
@aiocron.crontab('00 04 * * *') # (minute, hour, day, month, dayOfWeek) UTC Time
async def cronjob1():
    global flag_first
    flag_first = False
    print('flag_first reset')


# Function definitions

def connect_db():
    # connect to database
    host = os.getenv('SQL_HOST')
    user = os.getenv('SQL_USER')
    password = os.getenv('SQL_PASSWORD')
    conn = pymysql.connect(host=host, user=user,password=password)
    cursor = conn.cursor()
    cursor.execute('use discordbot')
    return conn,cursor

def write_to_db(name):
    # write to server and close connection
    conn,cursor = connect_db()
    query = f'INSERT INTO firstlist (username) VALUES (\'{name}\');'
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
    df['start_of_streak'] = df.username.ne(df['username'].shift())
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
    df_grouped = df[['username','Juice']].groupby('username',as_index=False).sum()
    df_juice = df_grouped.sort_values('Juice',ascending=False).iloc[0:len(df_grouped)]

    return df_juice,highscore_user,highscore_value

TOKEN = os.getenv('DISCORD_TOKEN')
bot.run(TOKEN)
