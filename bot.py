# Import dependencies (make sure requirements.txt includes these)

import discord                                              # Discord API
from discord.ext import commands
# from discord_components import Button, DiscordComponents
import pymysql                                              # Connect to AWS SQL
import os,io,base64,string,time,random,asyncio,re           # Core python libraries
import pandas as pd                                         # Manipulate tabular data
from chatgpt_functions import *                             # function calls for ChatGPT API
from dotenv import load_dotenv                              # Load .env
from datetime import datetime
import matplotlib.pyplot as plt

load_dotenv()
intents = discord.Intents.all()

bot = commands.Bot(intents=intents,command_prefix='_', case_insensitive=True)      # structure for bot commands
discord.Intents.all()
discord.MemberCacheFlags.all()
# DiscordComponents(bot)                                      # structure for buttons
bot.remove_command('help')                                  # remove default help command

# Bot Commands

@bot.event
async def on_message(message):
    await bot.process_commands(message)     # ensures all commands are completed first
    if(message.author.bot): 
        return
    else: 
        id = message.id
        member_id = message.author.id
        channel_id = message.channel.id
        content = message.content
        created_at = message.created_at
        vals = [id,member_id,channel_id,content,created_at]
        vals = [value if value is not None else 'NULL' for value in vals]

        update_sql_messages(vals)


@bot.command(name = 'help')
async def a_help(ctx):
    # provides an embed of all availble commands
    embed=discord.Embed(title="Commands", color=0x4d4170)
    embed.add_field(name="_1st", value="Try for first", inline=True)
    embed.add_field(name="_score", value="First leaderboard", inline=True)
    embed.add_field(name="_ask", value="Ask ChatGPT", inline=True)
    embed.add_field(name="_hello", value="Say hi", inline=True)
    embed.add_field(name="_dash", value="Server dashboard", inline=True)
    embed.add_field(name="_simonsays", value="I'll repeat after you", inline=True)
    embed.add_field(name="_juice", value="Juice board", inline=True)
    embed.add_field(name="_donation", value="Our patrons", inline=True)
    embed.add_field(name="_imagine", value="Generate AI images", inline=True)
    embed.add_field(name="_stats", value="Individual user stats", inline=True)
    await ctx.channel.send(embed=embed)

@bot.command()
async def score(ctx,pass_context=True, brief='Count of daily 1st wins'):
    # reads SQL database and generates an embed with list of names and scores
    df = get_db('firstlist_id')
    streak = get_streak(df)
    counts = df.user_id.value_counts()
    embed=discord.Embed(title='First Leaderboard',description="Count of daily 1st wins",color=0x4d4170)
    for i in range(5):  # display top 5
        embed.add_field(name=bot.get_user(int(counts.index[i])),
                        value=counts[i],
                        inline=False)
    txt = f'Most recent: {bot.get_user(int(df.user_id.iloc[-1]))} 🔥 {streak} days'
    embed.set_footer(text=txt)
    await ctx.channel.send(embed=embed)

@bot.command()
async def stats(ctx,*, args=None, pass_context=True, brief='Get an individual users stats'):
    # reads SQL database and generates an embed with list of names and scores
    df = get_db('firstlist_id')

    if len(ctx.message.mentions) > 0:
        author_id = str(ctx.message.mentions[0].id)
    else:
        author_id = str(ctx.message.author.id)

    try:
        author = bot.get_user(int(author_id))
        streak = get_user_streak(df,author_id)
        score = get_user_score(df,author_id)
        juice = get_user_juice(df,author_id)

        embed=discord.Embed(title=author, description="Your server statistics", color=0x4d4170)
        embed.set_thumbnail(url=f'https://cdn.discordapp.com/avatars/{author_id}/{author.avatar}.webp?size=128')
        embed.add_field(name="Score", value=f'{score} 🏆', inline=True)
        embed.add_field(name="Juice", value=f'{int(juice)} 🧃', inline=True)
        embed.add_field(name="Longest streak", value=f'{streak} days 🔥', inline=True)

        await ctx.channel.send(embed=embed)
    except Exception as error:
        print("An error occurred:", type(error).__name__)
        await ctx.channel.send('This user has never gotten a first!')

@bot.command()
async def members(ctx):
    # updates database with all current members of the server and their info
    members = ctx.guild.members
    vals=[]
    for member in members:
        id = int(member.id)
        user = bot.get_user(id)
        avatar = f'https://cdn.discordapp.com/avatars/{id}/{user.avatar}.webp?size=128'
        user_name = user.name
        display_name = member.nick
        created_at = user.created_at.strftime("%Y-%m-%d %H:%M:%S")
        vals.append([id,user_name,display_name,avatar,created_at])
        
    update_sql_members(vals)    # write to database

    await ctx.channel.send("Member info succesfully updated.")

@bot.command()
async def emojis(ctx):
    # updates database with all current emojis in the server
    vals=[]
    for emoji in ctx.guild.emojis:
        id = emoji.id
        emoji_name = emoji.name
        guild_id = emoji.guild_id
        url = emoji.url
        created_at = emoji.created_at.strftime("%Y-%m-%d %H:%M:%S")
        vals.append([id,emoji_name,guild_id,url,created_at])
    
    update_sql_emojis(vals)

    await ctx.channel.send("Emoji info succesfully updated.")

@bot.command()
async def channels(ctx):
    # updates database with all current channels on the server
    vals=[]
    for channel in ctx.guild.channels:
        id = channel.id
        name = channel.name
        created_at = channel.created_at
        vals.append([id,name,created_at])
    
    update_sql_channels(vals)
        
    await ctx.channel.send("Channel info succesfully updated.")


@bot.command()
async def donation(ctx, brief='Get a list of all donations'):
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

@bot.command()
async def juice(ctx, pass_context=True, brief='Get the server juice scores'):
    # reads SQL database and send embed of total minutes between each "1st" timestamp and midnight
    df = get_db('firstlist_id')
    df_juice,highscore_user_id,val = get_juice(df)
    value = int(val)
    embed=discord.Embed(title='Juice Board 🧃',description='Total minutes between _1st and midnight',color=0x4d4170)
    for i in range(5):
        embed.add_field(name=bot.get_user(int(df_juice.iloc[i][0])),value=int(df_juice.iloc[i][1]),inline=False)
    txt = f'1-Day Highscore: {bot.get_user(int(highscore_user_id))}🧃{value} mins'
    embed.set_footer(text=txt)
    await ctx.channel.send(embed=embed)

# @bot.command()
# async def dash(ctx):
#     # sends button with hyperlink to server dashboard
#     await ctx.send('Here you go!', components=[Button(label="Go to your dashboard",style=5,url='https://dinkscord.onrender.com')])

@bot.command(pass_context=True)
async def hello(ctx):
    # response with name of the message author
    Author = ctx.author.mention
    msg = f'Hello {Author}!'
    await ctx.channel.send(msg)

@bot.command()
async def ping(ctx, brief='Ping the bot'):
    # response with pong
    await ctx.channel.send('pong')

@bot.command()
async def simonsays(ctx, *, arg, pass_context=True, brief='I will repeat after you'):
    # repeats string back
    await ctx.channel.send(arg)

chat_history = [{"role": "system", "content": "Talk like a surfer, stoner bro who is always chill and relaxed"}]
@bot.command()
async def ask(ctx,*, arg, pass_context=True, brief='Ask ChatGPT'):
    # Passes prompt to ChatGPT API and returns response
    global chat_history
    if str(ctx.message.author.id) in IDCARD:
        async with ctx.typing():
            chat_history, response = call_chatGPT(chat_history, arg)
        await ctx.send(response)
    else:
        await ctx.channel.send('To conserve compute resources, only specific users can use _ask')

@bot.command()
async def imagine(ctx,*, arg, pass_context=True, brief='Generate AI Art'):
    if str(ctx.message.author.id) in DALLE3_WHITELIST:
        write_to_db(table_name='dalle_3_prompts',user_id=ctx.author.id, prompt=arg)
        async with ctx.typing():
            response = call_dalle3(arg)
        #     embed=discord.Embed(title='Dalle-3 Image',color=0x4d4170)
        #     embed.set_image(url=str(response))
        # await ctx.channel.send(embed=embed)
        await ctx.channel.send(str(response))
    else:
        await ctx.channel.send('OpenAI charges ¢4 per image. Contact bot administrator for access.')

@bot.command()
async def graph(ctx, brief='Get a graph of the firsts to date'):
    # Initialize IO
    data_stream = io.BytesIO()

    df_first = get_db('firstlist_id')
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
    embed = discord.Embed(title='Firsts to Date',color=0x4d4170)
    embed.set_image(url="attachment://first_graph.png")

    await ctx.send(embed=embed, file=chart)
    


@bot.command(name='1st', pass_context=True, brief='Claim your first today')
async def first(ctx):
    # Checks if first has been claimed, if not, writes user_id and timestamp to SQL database

    channel_id = 94235299445493760    # dinkscord general text-channel id
    if ctx.channel.id != channel_id:
        msg = f'Please send your message to <#{channel_id}>.'
        await ctx.channel.send(msg)
    else:
        utc_now = datetime.utcnow()
        utc_now = pytz.timezone('UTC').localize(utc_now).astimezone(pytz.timezone('US/Eastern'))

        df = get_db('firstlist_id')
        timestamp_most_recent = df['timesent'].iloc[-1].to_pydatetime()
        timestamp_most_recent = pytz.timezone('UTC').localize(timestamp_most_recent).astimezone(pytz.timezone('US/Eastern'))
        
        if utc_now.strftime("%Y-%m-%d") == timestamp_most_recent.strftime("%Y-%m-%d"):
            Author = ctx.author.mention
            msg = f'Sorry {Author}, first has already been claimed today. 😭'
            await ctx.channel.send(msg)
        else:
            write_to_db(table_name='firstlist_id', user_id=ctx.author.id)
            time.sleep(0.5)
            Author = ctx.author.mention
            msg = f'{Author} is first today! 🥳'
            await ctx.channel.send(msg)

# Display in console bot is working correctly
@bot.event
async def on_ready():
    print("Live: " + bot.user.name)

# ====================================================================================
# Function definitions for reading, writing, and manipulating the data in SQL database
# ====================================================================================
    
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

def write_to_db(table_name, user_id, prompt=None):
    # write to server and close connection
    conn,cursor = connect_db()
    # mainly used for first table
    if prompt == None:
        vals = [user_id]
        query = "INSERT INTO {} (user_id) VALUES (%s);".format(table_name)
    # mainly used for dalle3 table
    else:
        vals = [user_id,prompt]
        query = "INSERT INTO {} (user_id, prompt) VALUES (%s,%s);".format(table_name)
    cursor.execute(query,vals)
    conn.commit()         
    cursor.close()
    conn.close()

def update_sql_messages(vals):
    conn,cursor = connect_db()
    with cursor:
        query="""INSERT INTO messages (id,member_id,channel_id,content,created_at)
                VALUES
                    (%s, %s, %s, %s, %s)"""
        
        cursor.execute(query, vals)
        conn.commit()
        cursor.close()
        conn.close()

def update_sql_members(vals):
    conn,cursor = connect_db()
    with cursor:
        query="""INSERT INTO members (id, user_name, display_name, avatar, created_at)
                VALUES
                    (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    user_name = VALUES(user_name),
                    display_name = VALUES(display_name),
                    avatar = VALUES(avatar),
                    created_at = VALUES(created_at);"""
        
        cursor.executemany(query, vals)
        conn.commit()
        cursor.close()
        conn.close()

def update_sql_emojis(vals):
    conn,cursor = connect_db()
    with cursor:
        query="""INSERT INTO emojis (id, emoji_name, guild_id, url, created_at)
                VALUES
                    (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    emoji_name = VALUES(emoji_name),
                    guild_id = VALUES(guild_id),
                    url = VALUES(url),
                    created_at = VALUES(created_at);"""
        
        cursor.executemany(query, vals)
        conn.commit()
        cursor.close()
        conn.close()

def update_sql_channels(vals):
    conn,cursor = connect_db()
    with cursor:
        query="""INSERT INTO channels (id, channel_name, created_at)
                VALUES
                    (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    channel_name = VALUES(channel_name),
                    created_at = VALUES(created_at);"""
        
        cursor.executemany(query, vals)
        conn.commit()
        cursor.close()
        conn.close()

def get_db(table_name):
    # get table as pandas df and close connection
    conn,cursor = connect_db()
    query = f'SELECT * FROM {table_name}'
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

def get_user_streak(df,user_id):
    # find streak of repeated user_ids
    df['start_of_streak'] = df.user_id.ne(df['user_id'].shift())
    df['streak_id'] = df['start_of_streak'].cumsum()
    df['streak_counter'] = df.groupby('streak_id').cumcount() + 1

    df = df[(df==user_id).any(axis=1)]
    id = df['streak_counter'].idxmax()
    user_streak = df.loc[id][4]

    return user_streak

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

def get_user_juice(df,user_id):    
    # localize to UTC time and convert to EST
    df['timesent'] = df['timesent'].dt.tz_localize('utc').dt.tz_convert('US/Eastern')
    hours = df['timesent'].dt.hour
    minutes = df['timesent'].dt.minute
    seconds = df['timesent'].dt.second
    total_mins = (seconds/60)+minutes+(hours*60)
    df['Juice'] = total_mins

    df = df[['user_id','Juice']].groupby('user_id',as_index=False).sum()
    df = df.sort_values('Juice',ascending=False).iloc[0:len(df)]
    df = df[(df==user_id).any(axis=1)]

    user_juice = df.iloc[0][1]

    return user_juice

def get_user_score(df,user_id):
    df = df['user_id'].value_counts().to_dict()
    user_score = df[user_id]
    return user_score

TOKEN = os.getenv('DISCORD_TOKEN')
bot.run(TOKEN)
