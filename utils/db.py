import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
import psycopg
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_MESSAGE_EDIT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "messages_edit_schema.sql"
)


class DatabaseError(Exception):
    """Custom exception for database operations"""
    pass


def message_edit_schema_statements() -> List[str]:
    """Load scripts/messages_edit_schema.sql, skipping comments and blanks.

    Full-line comments are removed before splitting, so a semicolon inside a
    comment cannot glue the next statement onto leftover prose.
    """
    text = _MESSAGE_EDIT_SCHEMA_PATH.read_text(encoding="utf-8")
    sql_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        sql_lines.append(line)
    statements: List[str] = []
    for chunk in "\n".join(sql_lines).split(";"):
        statement = chunk.strip()
        if statement:
            statements.append(statement)
    if not statements:
        raise DatabaseError(f"No statements in {_MESSAGE_EDIT_SCHEMA_PATH}")
    return statements


# on_message / slash inserts. content is the original text.
# If an edit was stored first (edited_content still NULL and the text differs),
# keep that earlier text as the edited version and store this write as content.
INSERT_MESSAGE_SQL = """
INSERT INTO messages (id, member_id, channel_id, content, created_at)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    edited_content = messages.content,
    edited_at = CURRENT_TIMESTAMP,
    content = EXCLUDED.content
WHERE messages.edited_content IS NULL
  AND messages.content IS DISTINCT FROM EXCLUDED.content
"""

# Cached edit: the pre-edit text is known. Never overwrite messages.content.
INSERT_KNOWN_EDIT_SQL = """
INSERT INTO messages (
    id, member_id, channel_id, content, created_at, edited_content, edited_at
) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
ON CONFLICT (id) DO UPDATE SET
    edited_content = EXCLUDED.edited_content,
    edited_at = CURRENT_TIMESTAMP
WHERE EXCLUDED.edited_content IS DISTINCT FROM COALESCE(messages.edited_content, messages.content)
"""

# Uncached edit: the pre-edit text is unknown. A missing row is stored as the
# only text we have, without marking it edited. An existing row updates only
# the edited columns, and only when the text changed.
UPSERT_UNSEEN_EDIT_SQL = """
INSERT INTO messages (id, member_id, channel_id, content, created_at)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    edited_content = %s,
    edited_at = CURRENT_TIMESTAMP
WHERE %s IS DISTINCT FROM COALESCE(messages.edited_content, messages.content)
"""

UPDATE_EDITED_CONTENT_SQL = """
UPDATE messages
SET edited_content = %s,
    edited_at = CURRENT_TIMESTAMP
WHERE id = %s
  AND %s IS DISTINCT FROM COALESCE(edited_content, content)
"""


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
        self._message_edit_schema_ready = False

    def ensure_message_edit_columns(self) -> None:
        """Add edited_content / edited_at if they are not already present."""
        if self._message_edit_schema_ready:
            return
        for statement in message_edit_schema_statements():
            self.db.execute(statement)
        self._message_edit_schema_ready = True
        logger.info("Ensured messages.edited_content and messages.edited_at")

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
        """Insert a message. content stays the original text."""
        self.ensure_message_edit_columns()
        self.db.execute(INSERT_MESSAGE_SQL, message_data)

    def record_message_edit(
        self,
        message_id: int,
        new_content: Optional[str],
        *,
        member_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        created_at=None,
        original_content: Optional[str] = None,
    ) -> str:
        """Store a content edit without overwriting messages.content.

        original_content is the pre-edit text when the message was cached.
        None means the edit was not cached, so the previous text is unknown.

        Returns "unchanged", "stored", "updated", or "missing".
        Embed-only updates (identical text) leave the row untouched.
        """
        new_text = "" if new_content is None else str(new_content)
        known_original = None if original_content is None else str(original_content)
        if known_original is not None and known_original == new_text:
            return "unchanged"

        self.ensure_message_edit_columns()
        can_insert = (
            member_id is not None and channel_id is not None and created_at is not None
        )
        with self.db.cursor() as cursor:
            if known_original is not None and can_insert:
                cursor.execute(
                    INSERT_KNOWN_EDIT_SQL,
                    (
                        message_id,
                        member_id,
                        channel_id,
                        known_original,
                        created_at,
                        new_text,
                    ),
                )
                return "stored" if cursor.rowcount else "unchanged"
            if known_original is None and can_insert:
                cursor.execute(
                    UPSERT_UNSEEN_EDIT_SQL,
                    (
                        message_id,
                        member_id,
                        channel_id,
                        new_text,
                        created_at,
                        new_text,
                        new_text,
                    ),
                )
                return "stored" if cursor.rowcount else "unchanged"
            cursor.execute(
                UPDATE_EDITED_CONTENT_SQL,
                (new_text, message_id, new_text),
            )
            if cursor.rowcount:
                return "updated"
        logger.debug(
            "Edit for message %s was not stored; row missing and author unknown",
            message_id,
        )
        return "missing"

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
