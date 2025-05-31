import pymysql
import os
import pandas as pd
from dotenv import load_dotenv
import pytz
from datetime import datetime

load_dotenv()

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