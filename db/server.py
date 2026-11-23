"""PostgreSQL persistence for server metadata sync and monthly reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from db.pool import DatabasePool, get_pool


@dataclass(frozen=True)
class MonthlyCount:
    user_id: int
    user_name: str
    message_count: int


class ServerRepository:
    def __init__(self, pool: DatabasePool | None = None) -> None:
        self._pool = pool

    @property
    def pool(self) -> DatabasePool:
        return self._pool or get_pool()

    def upsert_members(self, member_rows: Sequence[Sequence[object]]) -> None:
        query = """
            INSERT INTO members (id, user_name, display_name, avatar, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                user_name = EXCLUDED.user_name,
                display_name = EXCLUDED.display_name,
                avatar = EXCLUDED.avatar,
                created_at = EXCLUDED.created_at
        """
        self.pool.executemany(query, member_rows)

    def upsert_emojis(self, emoji_rows: Sequence[Sequence[object]]) -> None:
        query = """
            INSERT INTO emojis (id, emoji_name, guild_id, url, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                emoji_name = EXCLUDED.emoji_name,
                guild_id = EXCLUDED.guild_id,
                url = EXCLUDED.url,
                created_at = EXCLUDED.created_at
        """
        self.pool.executemany(query, emoji_rows)

    def upsert_channels(self, channel_rows: Sequence[Sequence[object]]) -> None:
        query = """
            INSERT INTO channels (id, channel_name, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                channel_name = EXCLUDED.channel_name,
                created_at = EXCLUDED.created_at
        """
        self.pool.executemany(query, channel_rows)

    def get_monthly_message_counts(self, year: int, month: int) -> list[MonthlyCount]:
        rows = self.pool.fetchall(
            """
            SELECT m.id, m.user_name, COUNT(msg.id) AS message_count
            FROM members m
            LEFT JOIN messages msg ON m.id = msg.member_id
            WHERE EXTRACT(YEAR FROM msg.created_at) = %s
              AND EXTRACT(MONTH FROM msg.created_at) = %s
            GROUP BY m.id, m.user_name
            ORDER BY message_count DESC
            """,
            (year, month),
        )
        return [
            MonthlyCount(
                user_id=int(uid),
                user_name=str(name or f"User {uid}"),
                message_count=int(count),
            )
            for uid, name, count in rows
        ]

    def claim_monthly_report(self, report_month: str, channel_id: int) -> str:
        """Atomically claim a YYYY-MM monthly report post.

        Returns ``claimed`` or ``already_posted``.
        """
        with self.pool.transaction() as cursor:
            cursor.execute(
                "SELECT status FROM monthly_report_posts WHERE report_month = %s FOR UPDATE",
                (report_month,),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    INSERT INTO monthly_report_posts (report_month, channel_id, status, claimed_at)
                    VALUES (%s, %s, 'pending', NOW())
                    """,
                    (report_month, channel_id),
                )
                return "claimed"
            if row[0] == "posted":
                return "already_posted"
            cursor.execute(
                """
                UPDATE monthly_report_posts
                SET status = 'pending',
                    channel_id = %s,
                    claimed_at = NOW(),
                    posted_at = NULL,
                    message_id = NULL
                WHERE report_month = %s AND status = 'pending'
                """,
                (channel_id, report_month),
            )
            return "claimed" if cursor.rowcount else "already_posted"

    def mark_monthly_report_posted(
        self, report_month: str, channel_id: int, message_id: int | None
    ) -> None:
        self.pool.execute(
            """
            UPDATE monthly_report_posts
            SET status = 'posted',
                channel_id = %s,
                posted_at = NOW(),
                message_id = %s
            WHERE report_month = %s
            """,
            (channel_id, message_id, report_month),
        )

    def release_monthly_report_claim(self, report_month: str) -> None:
        self.pool.execute(
            """
            DELETE FROM monthly_report_posts
            WHERE report_month = %s AND status = 'pending'
            """,
            (report_month,),
        )
