import os
import json
from contextlib import contextmanager
from typing import List, Optional, Tuple

import pandas as pd
import psycopg
from dotenv import load_dotenv

load_dotenv()


class DatabaseError(Exception):
    """Custom exception for database operations"""
    pass


def _connect():
    host = os.getenv("SQL_HOST", "localhost")
    port = 5432
    if host and ":" in host:
        host, port_str = host.rsplit(":", 1)
        port = int(port_str)
    return psycopg.connect(
        host=host,
        port=port,
        user=os.getenv("SQL_USER"),
        password=os.getenv("SQL_PASSWORD"),
        dbname=os.getenv("SQL_DATABASE"),
    )


class Database:
    def __init__(self):
        self.host = os.getenv("SQL_HOST")
        self.user = os.getenv("SQL_USER")
        self.password = os.getenv("SQL_PASSWORD")
        self.database = os.getenv("SQL_DATABASE")

    @contextmanager
    def connection(self):
        conn = None
        try:
            conn = _connect()
            yield conn
        except psycopg.Error as e:
            raise DatabaseError(f"Database connection error: {e}") from e
        finally:
            if conn:
                conn.close()

    @contextmanager
    def cursor(self):
        with self.connection() as conn:
            try:
                cursor = conn.cursor()
                yield cursor
                conn.commit()
            except psycopg.Error as e:
                conn.rollback()
                raise DatabaseError(f"Database operation error: {e}") from e
            finally:
                if cursor:
                    cursor.close()

    def execute(self, query: str, params: Optional[tuple] = None) -> None:
        with self.cursor() as cursor:
            cursor.execute(query, params)

    def executemany(self, query: str, params: List[tuple]) -> None:
        with self.cursor() as cursor:
            cursor.executemany(query, params)

    def fetch_df(self, query: str, params: Optional[tuple] = None) -> pd.DataFrame:
        with self.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                if cursor.description is None:
                    return pd.DataFrame()
                columns = [desc.name for desc in cursor.description]
                rows = cursor.fetchall()
        return pd.DataFrame(rows, columns=columns)


class DataOperations:
    def __init__(self):
        self.db = Database()

    def write_first_entry(self, user_id: int) -> None:
        query = "INSERT INTO firstlist_id (user_id) VALUES (%s);"
        self.db.execute(query, (str(user_id),))

    def write_dalle_entry(self, user_id: int, prompt: str, message_id: int) -> None:
        query = "INSERT INTO dalle_3_prompts (user_id, prompt, message_id) VALUES (%s, %s, %s);"
        self.db.execute(query, (str(user_id), prompt, message_id))

    def write_recipe_entry(
        self,
        member_id: int,
        name: str,
        ingredients: str,
        instructions: str,
        cuisine: str,
        dietary_preference: str,
        image_url: str,
    ) -> None:
        query = """
            INSERT INTO recipes
            (member_id, name, ingredients, instructions, cuisine, dietary_preference, image_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        self.db.execute(
            query,
            (
                member_id,
                name,
                ingredients,
                instructions,
                cuisine,
                dietary_preference,
                image_url,
            ),
        )

    def update_messages(self, message_data: Tuple) -> None:
        query = """
            INSERT INTO messages (id, member_id, channel_id, content, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                content = EXCLUDED.content
        """
        self.db.execute(query, message_data)

    def update_members(self, member_data: List[Tuple]) -> None:
        query = """
            INSERT INTO members (id, user_name, display_name, avatar, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                user_name = EXCLUDED.user_name,
                display_name = EXCLUDED.display_name,
                avatar = EXCLUDED.avatar,
                created_at = EXCLUDED.created_at
        """
        self.db.executemany(query, member_data)

    def update_emojis(self, emoji_data: List[Tuple]) -> None:
        query = """
            INSERT INTO emojis (id, emoji_name, guild_id, url, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                emoji_name = EXCLUDED.emoji_name,
                guild_id = EXCLUDED.guild_id,
                url = EXCLUDED.url,
                created_at = EXCLUDED.created_at
        """
        self.db.executemany(query, emoji_data)

    def update_channels(self, channel_data: List[Tuple]) -> None:
        query = """
            INSERT INTO channels (id, channel_name, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                channel_name = EXCLUDED.channel_name,
                created_at = EXCLUDED.created_at
        """
        self.db.executemany(query, channel_data)

    def log_chatgpt_interaction(
        self,
        user_id: int,
        model: str,
        request_messages: list,
        response_content: str,
        input_tokens: int,
        output_tokens: int,
        message_id: int,
        function_calls: list = None,
        image_urls: list = None,
    ) -> None:
        query = """
            INSERT INTO chatgpt_logs
            (user_id, model, request_messages, response_content, input_tokens,
             output_tokens, total_tokens, message_id, function_calls, image_urls)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
        """
        total_tokens = input_tokens + output_tokens
        self.db.execute(
            query,
            (
                str(user_id),
                model,
                json.dumps(request_messages),
                response_content,
                input_tokens,
                output_tokens,
                total_tokens,
                message_id,
                json.dumps(function_calls) if function_calls else None,
                json.dumps(image_urls) if image_urls else None,
            ),
        )

    def get_table_data(self, table_name: str) -> pd.DataFrame:
        query = f"SELECT * FROM {table_name}"
        if table_name == "firstlist_id":
            query += " ORDER BY timesent ASC"
        return self.db.fetch_df(query)

    def upsert_dink_balance(self, user_id, delta: float) -> None:
        query = """
            INSERT INTO dinkcoin_balances (user_id, balance)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                balance = dinkcoin_balances.balance + EXCLUDED.balance
        """
        self.db.execute(query, (str(user_id), delta))

    def get_dink_balance(self, user_id) -> float:
        query = "SELECT balance FROM dinkcoin_balances WHERE user_id = %s"
        df = self.db.fetch_df(query, (str(user_id),))
        if df.empty:
            return 0.0
        return float(df.iloc[0]["balance"])

    def get_dink_ledger(self, limit: int = 20) -> pd.DataFrame:
        query = """
            SELECT user_id, balance
            FROM dinkcoin_balances
            WHERE balance > 0
            ORDER BY balance DESC
            LIMIT %s
        """
        return self.db.fetch_df(query, (limit,))

    def get_total_dink_circulation(self) -> float:
        query = "SELECT COALESCE(SUM(balance), 0) AS total FROM dinkcoin_balances"
        df = self.db.fetch_df(query)
        return float(df.iloc[0]["total"])

    def log_dink_transaction(
        self,
        from_user_id,
        to_user_id,
        amount: float,
        tx_type: str,
        tx_hash: str = None,
    ) -> None:
        query = """
            INSERT INTO dinkcoin_transactions
            (from_user_id, to_user_id, amount, tx_type, tx_hash)
            VALUES (%s, %s, %s, %s, %s)
        """
        self.db.execute(
            query,
            (
                str(from_user_id) if from_user_id is not None else None,
                str(to_user_id),
                amount,
                tx_type,
                tx_hash,
            ),
        )

    def apply_dink_transfer(self, from_user_id, to_user_id, amount: float) -> None:
        from_user_id = str(from_user_id)
        to_user_id = str(to_user_id)
        with self.db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE dinkcoin_balances
                SET balance = balance - %s
                WHERE user_id = %s AND balance >= %s
                """,
                (amount, from_user_id, amount),
            )
            if cursor.rowcount == 0:
                raise DatabaseError("Insufficient DINK balance")
            cursor.execute(
                """
                INSERT INTO dinkcoin_balances (user_id, balance)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    balance = dinkcoin_balances.balance + EXCLUDED.balance
                """,
                (to_user_id, amount),
            )

    def record_dink_mint(self, user_id, amount: float, tx_hash: str = None) -> None:
        self.upsert_dink_balance(user_id, amount)
        self.log_dink_transaction(None, user_id, amount, "mint", tx_hash)

    def record_dink_transfer(
        self,
        from_user_id,
        to_user_id,
        amount: float,
        tx_hash: str = None,
    ) -> None:
        self.apply_dink_transfer(from_user_id, to_user_id, amount)
        self.log_dink_transaction(from_user_id, to_user_id, amount, "transfer", tx_hash)

    def get_monthly_message_counts(self, year: int, month: int) -> pd.DataFrame:
        query = """
            SELECT m.id, m.user_name, COUNT(msg.id) as message_count
            FROM members m
            LEFT JOIN messages msg ON m.id = msg.member_id
            WHERE EXTRACT(YEAR FROM msg.created_at) = %s
              AND EXTRACT(MONTH FROM msg.created_at) = %s
            GROUP BY m.id, m.user_name
            ORDER BY message_count DESC
        """
        return self.db.fetch_df(query, (year, month))


class StreakCalculator:
    @staticmethod
    def calculate_streak(df: pd.DataFrame) -> int:
        df = df.sort_values("timesent").reset_index(drop=True)
        df["start_of_streak"] = df.user_id.ne(df["user_id"].shift())
        df["streak_id"] = df["start_of_streak"].cumsum()
        df["streak_counter"] = df.groupby("streak_id").cumcount() + 1
        return df.streak_counter.iloc[-1]

    @staticmethod
    def calculate_user_streak(df: pd.DataFrame, user_id: str) -> int:
        df = df.sort_values("timesent").reset_index(drop=True)
        df["start_of_streak"] = df.user_id.ne(df["user_id"].shift())
        df["streak_id"] = df["start_of_streak"].cumsum()
        df["streak_counter"] = df.groupby("streak_id").cumcount() + 1

        user_df = df[df["user_id"] == user_id]
        if user_df.empty:
            return 0

        return user_df["streak_counter"].max()


class JuiceCalculator:
    @staticmethod
    def _convert_to_est(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["timesent"] = df["timesent"].dt.tz_localize("utc").dt.tz_convert("US/Eastern")
        return df

    @staticmethod
    def _add_juice_column(df: pd.DataFrame) -> pd.DataFrame:
        df = JuiceCalculator._convert_to_est(df)
        df = df.sort_values("timesent").reset_index(drop=True)
        within_day = (
            df["timesent"].dt.hour * 60
            + df["timesent"].dt.minute
            + df["timesent"].dt.second / 60
        )
        day_gap = (
            df["timesent"].dt.normalize() - df["timesent"].shift(1).dt.normalize()
        ).dt.days
        missed_days = day_gap.sub(1).clip(lower=0).fillna(0)
        df["juice"] = within_day + missed_days * 1440
        return df

    @staticmethod
    def daily_juice_series(df: pd.DataFrame) -> pd.DataFrame:
        df = JuiceCalculator._add_juice_column(df)
        return df[["timesent", "juice"]]

    @staticmethod
    def calculate_juice(df: pd.DataFrame) -> Tuple[pd.DataFrame, str, float]:
        df = JuiceCalculator._add_juice_column(df)

        highscore_idx = df["juice"].idxmax()
        highscore_user = df.iloc[highscore_idx]["user_id"]
        highscore_value = df.iloc[highscore_idx]["juice"]

        juice_df = df.groupby("user_id")["juice"].sum().reset_index()
        juice_df = juice_df.sort_values("juice", ascending=False)

        return juice_df, highscore_user, highscore_value

    @staticmethod
    def calculate_user_juice(df: pd.DataFrame, user_id: str) -> float:
        df = JuiceCalculator._add_juice_column(df)
        return df[df.user_id == user_id]["juice"].sum()


db_ops = DataOperations()
streak_calc = StreakCalculator()
juice_calc = JuiceCalculator()
