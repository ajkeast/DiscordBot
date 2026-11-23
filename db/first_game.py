"""PostgreSQL persistence for the daily /1st game."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from config import EASTERN
from db.pool import DatabasePool, get_pool

_FIRST_CLAIM_LOCK_KEY = 421_001


@dataclass(frozen=True)
class FirstEntry:
    user_id: str
    timesent: datetime


def _to_eastern_date(ts: datetime) -> date:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(EASTERN).date()


class FirstGameRepository:
    def __init__(self, pool: DatabasePool | None = None) -> None:
        self._pool = pool

    @property
    def pool(self) -> DatabasePool:
        return self._pool or get_pool()

    def get_all_entries(self) -> list[FirstEntry]:
        rows = self.pool.fetchall(
            "SELECT user_id, timesent FROM firstlist_id ORDER BY timesent ASC"
        )
        return [FirstEntry(user_id=str(uid), timesent=ts) for uid, ts in rows]

    def claim_first_for_day(
        self,
        user_id: int,
        eastern_day: date,
        mint_amount: float,
    ) -> bool:
        """Atomically claim today's /1st and mint DINK in one transaction.

        Uses a transaction-scoped PostgreSQL advisory lock so concurrent
        midnight claims cannot double-win or double-mint.
        """
        uid = str(user_id)
        with self.pool.transaction() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_FIRST_CLAIM_LOCK_KEY,))
            cursor.execute(
                "SELECT timesent FROM firstlist_id ORDER BY timesent DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row is not None and _to_eastern_date(row[0]) == eastern_day:
                return False

            cursor.execute("INSERT INTO firstlist_id (user_id) VALUES (%s)", (uid,))
            cursor.execute(
                """
                INSERT INTO dinkcoin_balances (user_id, balance)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    balance = dinkcoin_balances.balance + EXCLUDED.balance
                """,
                (uid, mint_amount),
            )
            cursor.execute(
                """
                INSERT INTO dinkcoin_transactions
                    (from_user_id, to_user_id, amount, tx_type)
                VALUES (NULL, %s, %s, 'mint')
                """,
                (uid, mint_amount),
            )
            return True
