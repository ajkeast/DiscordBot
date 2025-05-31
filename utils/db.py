import pymysql
import os
import pandas as pd
from dotenv import load_dotenv
from datetime import datetime
from contextlib import contextmanager
from typing import Tuple, List, Optional
import json

load_dotenv()

class DatabaseError(Exception):
    """Custom exception for database operations"""
    pass

class Database:
    def __init__(self):
        self.host = os.getenv('SQL_HOST')
        self.user = os.getenv('SQL_USER')
        self.password = os.getenv('SQL_PASSWORD')
        self.database = os.getenv('SQL_DATABASE')
        self._conn = None
        self._cursor = None

    @contextmanager
    def connection(self):
        """Context manager for database connections"""
        try:
            conn = pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database
            )
            yield conn
        except pymysql.Error as e:
            raise DatabaseError(f"Database connection error: {e}")
        finally:
            if conn:
                conn.close()

    @contextmanager
    def cursor(self):
        """Context manager for database cursors"""
        with self.connection() as conn:
            try:
                cursor = conn.cursor()
                yield cursor
                conn.commit()
            except pymysql.Error as e:
                conn.rollback()
                raise DatabaseError(f"Database operation error: {e}")
            finally:
                if cursor:
                    cursor.close()

    def execute(self, query: str, params: Optional[tuple] = None) -> None:
        """Execute a single query"""
        with self.cursor() as cursor:
            cursor.execute(query, params)

    def executemany(self, query: str, params: List[tuple]) -> None:
        """Execute multiple queries"""
        with self.cursor() as cursor:
            cursor.executemany(query, params)

    def fetch_df(self, query: str, params: Optional[tuple] = None) -> pd.DataFrame:
        """Fetch query results as pandas DataFrame"""
        with self.connection() as conn:
            return pd.read_sql_query(query, conn, params=params)

class DataOperations:
    def __init__(self):
        self.db = Database()

    def write_first_entry(self, user_id: int) -> None:
        """Record a 'first' entry for a user"""
        query = "INSERT INTO firstlist_id (user_id) VALUES (%s);"
        self.db.execute(query, (user_id,))

    def write_dalle_entry(self, user_id: int, prompt: str) -> None:
        """Record a DALL-E prompt entry"""
        query = "INSERT INTO dalle3 (user_id, prompt) VALUES (%s, %s);"
        self.db.execute(query, (user_id, prompt))

    def update_messages(self, message_data: Tuple) -> None:
        """Update messages table"""
        query = """
            INSERT INTO messages (id, member_id, channel_id, content, created_at)
            VALUES (%s, %s, %s, %s, %s)
        """
        self.db.execute(query, message_data)

    def update_members(self, member_data: List[Tuple]) -> None:
        """Update members table"""
        query = """
            INSERT INTO members (id, user_name, display_name, avatar, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                user_name = VALUES(user_name),
                display_name = VALUES(display_name),
                avatar = VALUES(avatar),
                created_at = VALUES(created_at)
        """
        self.db.executemany(query, member_data)

    def update_emojis(self, emoji_data: List[Tuple]) -> None:
        """Update emojis table"""
        query = """
            INSERT INTO emojis (id, emoji_name, guild_id, url, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                emoji_name = VALUES(emoji_name),
                guild_id = VALUES(guild_id),
                url = VALUES(url),
                created_at = VALUES(created_at)
        """
        self.db.executemany(query, emoji_data)

    def update_channels(self, channel_data: List[Tuple]) -> None:
        """Update channels table"""
        query = """
            INSERT INTO channels (id, channel_name, created_at)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                channel_name = VALUES(channel_name),
                created_at = VALUES(created_at)
        """
        self.db.executemany(query, channel_data)

    def log_chatgpt_interaction(self, user_id: int, model: str, request_messages: list, 
                              response_content: str, input_tokens: int, output_tokens: int,
                              function_calls: list = None, image_urls: list = None) -> None:
        """Log a ChatGPT interaction to the database
        
        Args:
            user_id (int): Discord user ID (will be converted to string)
            model (str): GPT model used
            request_messages (list): List of message objects in the conversation
            response_content (str): Assistant's response
            input_tokens (int): Number of input tokens
            output_tokens (int): Number of output tokens
            function_calls (list, optional): List of function calls made
            image_urls (list, optional): List of image URLs attached to the request
        """
        query = """
            INSERT INTO chatgpt_logs 
            (user_id, model, request_messages, response_content, input_tokens, 
             output_tokens, total_tokens, function_calls, image_urls)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        total_tokens = input_tokens + output_tokens
        self.db.execute(query, (
            str(user_id),  # Convert user_id to string to match VARCHAR(20)
            model,
            json.dumps(request_messages),
            response_content,
            input_tokens,
            output_tokens,
            total_tokens,
            json.dumps(function_calls) if function_calls else None,
            json.dumps(image_urls) if image_urls else None
        ))

    def get_table_data(self, table_name: str) -> pd.DataFrame:
        """Get entire table as DataFrame"""
        query = f"SELECT * FROM {table_name}"
        return self.db.fetch_df(query)

class StreakCalculator:
    @staticmethod
    def calculate_streak(df: pd.DataFrame) -> int:
        """Calculate current streak"""
        df['start_of_streak'] = df.user_id.ne(df['user_id'].shift())
        df['streak_id'] = df['start_of_streak'].cumsum()
        df['streak_counter'] = df.groupby('streak_id').cumcount() + 1
        return df.streak_counter.iloc[-1]

    @staticmethod
    def calculate_user_streak(df: pd.DataFrame, user_id: str) -> int:
        """Calculate streak for specific user"""
        df = df[df.user_id == user_id].copy()
        if df.empty:
            return 0
        df['start_of_streak'] = df.user_id.ne(df['user_id'].shift())
        df['streak_id'] = df['start_of_streak'].cumsum()
        df['streak_counter'] = df.groupby('streak_id').cumcount() + 1
        return df.streak_counter.max()

class JuiceCalculator:
    @staticmethod
    def _convert_to_est(df: pd.DataFrame) -> pd.DataFrame:
        """Convert timestamps to EST"""
        df = df.copy()
        df['timesent'] = df['timesent'].dt.tz_localize('utc').dt.tz_convert('US/Eastern')
        return df

    @staticmethod
    def calculate_juice(df: pd.DataFrame) -> Tuple[pd.DataFrame, str, float]:
        """Calculate juice scores for all users"""
        df = JuiceCalculator._convert_to_est(df)
        
        # Calculate minutes
        df['juice'] = (df['timesent'].dt.hour * 60 + 
                      df['timesent'].dt.minute +
                      df['timesent'].dt.second / 60)
        
        # Get highscore
        highscore_idx = df['juice'].idxmax()
        highscore_user = df.iloc[highscore_idx]['user_id']
        highscore_value = df.iloc[highscore_idx]['juice']
        
        # Calculate total juice per user
        juice_df = df.groupby('user_id')['juice'].sum().reset_index()
        juice_df = juice_df.sort_values('juice', ascending=False)
        
        return juice_df, highscore_user, highscore_value

    @staticmethod
    def calculate_user_juice(df: pd.DataFrame, user_id: str) -> float:
        """Calculate juice score for specific user"""
        df = JuiceCalculator._convert_to_est(df)
        df['juice'] = (df['timesent'].dt.hour * 60 + 
                      df['timesent'].dt.minute +
                      df['timesent'].dt.second / 60)
        return df[df.user_id == user_id]['juice'].sum()

# Create global instances
db_ops = DataOperations()
streak_calc = StreakCalculator()
juice_calc = JuiceCalculator() 