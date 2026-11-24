"""PostgreSQL access for Minecraft collector reads and recap post claims.

Reads playday facts from the Dinkboard collector schema (``minecraft.*``).
Writes playday session slices to ``public.minecraft_sessions`` for the
dashboard, plus idempotent ``minecraft_daily_posts`` claims.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from db.pool import DatabasePool, get_pool

if TYPE_CHECKING:
    from minecraft.models import DayRecap, Death, Session


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


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        from config import EASTERN

        return value.replace(tzinfo=EASTERN)
    return value


def _normalize_end_reason(reason: str | None, *, still_online: bool) -> str:
    if still_online:
        return "still_online"
    text = (reason or "leave").lower()
    if text == "rejoin":
        return "rejoin"
    if text.startswith("server_") or "server_stop" in text or "server_start" in text:
        return "server_stop"
    return "leave"


def _death_message(player: str, raw: str | None) -> str:
    """Collector stores the full system-chat line; recap wants the cause stem."""
    message = (raw or "").strip()
    prefix = f"{player} "
    if message.startswith(prefix):
        return message[len(prefix) :].strip() or message
    return message


class MinecraftRepository:
    def __init__(self, pool: DatabasePool | None = None) -> None:
        self._pool = pool

    @property
    def pool(self) -> DatabasePool:
        return self._pool or get_pool()

    def fetch_sessions(
        self,
        window_start: datetime,
        window_end: datetime,
        *,
        as_of: datetime | None = None,
    ) -> list[Session]:
        """Sessions overlapping ``[window_start, window_end)`` from the collector."""
        from minecraft.models import Session

        close_at = _as_aware(as_of or window_end)
        rows = self.pool.fetchall(
            """
            SELECT player, uuid, joined_at, left_at, end_reason
            FROM minecraft.sessions
            WHERE joined_at < %s
              AND (left_at IS NULL OR left_at > %s)
            ORDER BY joined_at
            """,
            (window_end, window_start),
        )
        sessions: list[Session] = []
        for player, uuid, joined_at, left_at, end_reason in rows:
            start = _as_aware(joined_at)
            still_online = left_at is None
            end = close_at if still_online else _as_aware(left_at)
            if end <= start:
                continue
            sessions.append(
                Session(
                    player=str(player),
                    start=start,
                    end=end,
                    end_reason=_normalize_end_reason(
                        end_reason, still_online=still_online
                    ),
                    uuid=str(uuid) if uuid else None,
                )
            )
        return sessions

    def fetch_deaths(self, window_start: datetime, window_end: datetime) -> list[Death]:
        """Non-rolled-back deaths inside ``[window_start, window_end)``."""
        from minecraft.models import Death

        rows = self.pool.fetchall(
            """
            SELECT player, ts, message, killer_player
            FROM minecraft.deaths
            WHERE ts >= %s AND ts < %s
              AND NOT rolled_back
            ORDER BY ts
            """,
            (window_start, window_end),
        )
        deaths: list[Death] = []
        for player, ts, message, killer in rows:
            name = str(player)
            deaths.append(
                Death(
                    player=name,
                    at=_as_aware(ts),
                    message=_death_message(name, message),
                    killer_player=str(killer) if killer else None,
                )
            )
        return deaths

    def persist_day(self, recap: DayRecap) -> None:
        """Replace one play day's session slices for the dashboard."""
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
                )
                for sl in recap.slices
            ],
            (0, 1, 3),
        )

        with self.pool.transaction() as cursor:
            cursor.execute(
                "DELETE FROM minecraft_sessions WHERE activity_date = %s", (day,)
            )
            if session_rows:
                cursor.executemany(
                    """
                    INSERT INTO minecraft_sessions (
                        activity_date, player_name, player_uuid, started_at, ended_at,
                        slice_started_at, slice_ended_at, duration_seconds, end_reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    session_rows,
                )

    def claim_daily_post(self, activity_date: date, channel_id: int) -> str:
        """Atomically claim one day's recap post.

        Returns ``claimed`` or ``already_posted``. A leftover ``pending`` row
        from a prior crash is reclaimed so restarts can still deliver the recap.
        """
        with self.pool.transaction() as cursor:
            cursor.execute(
                "SELECT status FROM minecraft_daily_posts "
                "WHERE activity_date = %s FOR UPDATE",
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
        """Return ``(names, uuids)`` with any collector session before ``window_start``."""
        rows = self.pool.fetchall(
            """
            SELECT player, uuid FROM minecraft.sessions
            WHERE joined_at < %s
            """,
            (window_start,),
        )
        names = {str(name) for name, _uuid in rows if name}
        uuids = {str(uuid).lower() for _name, uuid in rows if uuid}
        return names, uuids
