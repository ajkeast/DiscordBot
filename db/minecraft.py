"""PostgreSQL persistence for Minecraft daily activity and post claims."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from db.pool import DatabasePool, get_pool

if TYPE_CHECKING:
    from minecraft.models import DayRecap


def _dedupe(rows: list[tuple], key_indexes: tuple[int, ...]) -> list[tuple]:
    seen: set[tuple] = set()
    unique: list[tuple] = []
    for row in rows:
        key = tuple(row[idx] for idx in key_indexes)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


class MinecraftRepository:
    def __init__(self, pool: DatabasePool | None = None) -> None:
        self._pool = pool

    @property
    def pool(self) -> DatabasePool:
        return self._pool or get_pool()

    def persist_day(self, recap: DayRecap) -> None:
        """Replace one play day's sessions, deaths, and advancements atomically."""
        day = recap.day
        session_rows = _dedupe(
            [
                (
                    day,
                    sl.player_name,
                    sl.player_uuid,
                    sl.started_at,
                    sl.ended_at,
                    sl.slice_started_at,
                    sl.slice_ended_at,
                    sl.duration_seconds,
                    sl.end_reason,
                    sl.likely_afk,
                )
                for sl in recap.slices
            ],
            (0, 1, 3),
        )
        death_rows = _dedupe(
            [
                (
                    day,
                    d.at,
                    d.player,
                    recap.player_uuids.get(d.player),
                    d.message,
                )
                for d in recap.deaths
            ],
            (0, 2, 1, 4),
        )
        advancement_rows = _dedupe(
            [
                (
                    day,
                    adv.at,
                    adv.player,
                    recap.player_uuids.get(adv.player),
                    adv.kind,
                    adv.title,
                )
                for adv in recap.advancements
            ],
            (0, 2, 1, 4, 5),
        )

        with self.pool.transaction() as cursor:
            cursor.execute(
                "DELETE FROM minecraft_sessions WHERE activity_date = %s", (day,)
            )
            cursor.execute(
                "DELETE FROM minecraft_deaths WHERE activity_date = %s", (day,)
            )
            cursor.execute(
                "DELETE FROM minecraft_advancements WHERE activity_date = %s", (day,)
            )
            if session_rows:
                cursor.executemany(
                    """
                    INSERT INTO minecraft_sessions (
                        activity_date, player_name, player_uuid, started_at, ended_at,
                        slice_started_at, slice_ended_at, duration_seconds, end_reason, likely_afk
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::boolean)
                    """,
                    session_rows,
                )
            if death_rows:
                cursor.executemany(
                    """
                    INSERT INTO minecraft_deaths (
                        activity_date, occurred_at, player_name, player_uuid, message
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    death_rows,
                )
            if advancement_rows:
                cursor.executemany(
                    """
                    INSERT INTO minecraft_advancements (
                        activity_date, occurred_at, player_name, player_uuid, kind, title
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    advancement_rows,
                )

    def claim_daily_post(self, activity_date: date, channel_id: int) -> str:
        """Atomically claim one day's recap post.

        Returns ``claimed`` or ``already_posted``. A leftover ``pending`` row
        from a prior crash is reclaimed so restarts can still deliver the recap.
        """
        with self.pool.transaction() as cursor:
            cursor.execute(
                "SELECT status FROM minecraft_daily_posts WHERE activity_date = %s FOR UPDATE",
                (activity_date,),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    INSERT INTO minecraft_daily_posts (
                        activity_date, channel_id, status, claimed_at, nobody_played
                    ) VALUES (%s, %s, 'pending', NOW(), FALSE)
                    """,
                    (activity_date, channel_id),
                )
                return "claimed"
            if row[0] == "posted":
                return "already_posted"
            cursor.execute(
                """
                UPDATE minecraft_daily_posts
                SET status = 'pending',
                    channel_id = %s,
                    claimed_at = NOW(),
                    posted_at = NULL,
                    message_id = NULL
                WHERE activity_date = %s AND status = 'pending'
                """,
                (channel_id, activity_date),
            )
            return "claimed" if cursor.rowcount else "already_posted"

    def release_claim(self, activity_date: date) -> None:
        self.pool.execute(
            """
            DELETE FROM minecraft_daily_posts
            WHERE activity_date = %s AND status = 'pending'
            """,
            (activity_date,),
        )

    def mark_posted(
        self,
        activity_date: date,
        channel_id: int,
        message_id: int | None,
        nobody_played: bool,
    ) -> None:
        self.pool.execute(
            """
            UPDATE minecraft_daily_posts
            SET status = 'posted',
                channel_id = %s,
                posted_at = NOW(),
                message_id = %s,
                nobody_played = %s::boolean
            WHERE activity_date = %s
            """,
            (channel_id, message_id, nobody_played, activity_date),
        )

    def players_seen_before(
        self, window_start: datetime
    ) -> tuple[set[str], set[str]]:
        """Return ``(names, uuids)`` recorded in any Minecraft table before ``window_start``."""
        rows = self.pool.fetchall(
            """
            SELECT player_name, player_uuid FROM minecraft_sessions
            WHERE started_at < %s
            UNION
            SELECT player_name, player_uuid FROM minecraft_deaths
            WHERE occurred_at < %s
            UNION
            SELECT player_name, player_uuid FROM minecraft_advancements
            WHERE occurred_at < %s
            """,
            (window_start, window_start, window_start),
        )
        names = {str(name) for name, _uuid in rows if name}
        uuids = {str(uuid).lower() for _name, uuid in rows if uuid}
        return names, uuids
