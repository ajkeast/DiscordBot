"""PostgreSQL persistence for DinkCoin balances, transfers, and requests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from db.pool import DatabaseError, DatabasePool, get_pool


class InsufficientBalanceError(DatabaseError):
    """Raised when a payer does not hold enough DINK for a transfer."""


class RequestStateError(DatabaseError):
    """Raised when a DINK request is missing, unauthorized, expired, or already handled."""


@dataclass(frozen=True)
class LedgerEntry:
    user_id: str
    balance: float


@dataclass(frozen=True)
class DinkRequestRecord:
    id: int
    requester_id: int
    payer_id: int
    amount: int
    status: str
    expires_at: datetime


class DinkCoinRepository:
    def __init__(self, pool: DatabasePool | None = None) -> None:
        self._pool = pool

    @property
    def pool(self) -> DatabasePool:
        return self._pool or get_pool()

    def get_balance(self, user_id: int | str) -> float:
        row = self.pool.fetchone(
            "SELECT balance FROM dinkcoin_balances WHERE user_id = %s",
            (str(user_id),),
        )
        return float(row[0]) if row is not None else 0.0

    def get_ledger(self, limit: int = 20) -> list[LedgerEntry]:
        rows = self.pool.fetchall(
            """
            SELECT user_id, balance
            FROM dinkcoin_balances
            WHERE balance > 0
            ORDER BY balance DESC
            LIMIT %s
            """,
            (limit,),
        )
        return [LedgerEntry(user_id=str(uid), balance=float(bal)) for uid, bal in rows]

    def get_total_circulation(self) -> float:
        row = self.pool.fetchone(
            "SELECT COALESCE(SUM(balance), 0) FROM dinkcoin_balances"
        )
        return float(row[0]) if row is not None else 0.0

    def mint(self, user_id: int | str, amount: float) -> None:
        """Credit minted DINK and record the mint transaction atomically."""
        uid = str(user_id)
        with self.pool.transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO dinkcoin_balances (user_id, balance)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    balance = dinkcoin_balances.balance + EXCLUDED.balance
                """,
                (uid, amount),
            )
            cursor.execute(
                """
                INSERT INTO dinkcoin_transactions
                    (from_user_id, to_user_id, amount, tx_type)
                VALUES (NULL, %s, %s, 'mint')
                """,
                (uid, amount),
            )

    def transfer(
        self, from_user_id: int | str, to_user_id: int | str, amount: float
    ) -> float:
        """Atomically transfer DINK and log the transaction; return sender's new balance."""
        sender = str(from_user_id)
        recipient = str(to_user_id)
        with self.pool.transaction() as cursor:
            cursor.execute(
                """
                UPDATE dinkcoin_balances
                SET balance = balance - %s
                WHERE user_id = %s AND balance >= %s
                RETURNING balance
                """,
                (amount, sender, amount),
            )
            row = cursor.fetchone()
            if row is None:
                raise InsufficientBalanceError("Insufficient DINK balance")
            new_balance = float(row[0])
            cursor.execute(
                """
                INSERT INTO dinkcoin_balances (user_id, balance)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    balance = dinkcoin_balances.balance + EXCLUDED.balance
                """,
                (recipient, amount),
            )
            cursor.execute(
                """
                INSERT INTO dinkcoin_transactions
                    (from_user_id, to_user_id, amount, tx_type)
                VALUES (%s, %s, %s, 'transfer')
                """,
                (sender, recipient, amount),
            )
            return new_balance

    def create_request(
        self,
        requester_id: int,
        payer_id: int,
        amount: int,
        expires_at: datetime,
    ) -> int:
        with self.pool.transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO dinkcoin_requests (requester_id, payer_id, amount, expires_at)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (requester_id, payer_id, amount, expires_at),
            )
            row = cursor.fetchone()
            assert row is not None
            return int(row[0])

    def get_request(self, request_id: int) -> DinkRequestRecord | None:
        row = self.pool.fetchone(
            """
            SELECT id, requester_id, payer_id, amount, status, expires_at
            FROM dinkcoin_requests
            WHERE id = %s
            """,
            (request_id,),
        )
        if row is None:
            return None
        return DinkRequestRecord(
            id=int(row[0]),
            requester_id=int(row[1]),
            payer_id=int(row[2]),
            amount=int(row[3]),
            status=str(row[4]),
            expires_at=row[5],
        )

    def accept_request(
        self,
        request_id: int,
        actor_id: int,
        *,
        now: datetime | None = None,
    ) -> DinkRequestRecord:
        """Atomically verify, settle, and mark a pending DINK request accepted."""
        current_time = now or datetime.now(timezone.utc)
        with self.pool.transaction() as cursor:
            cursor.execute(
                """
                SELECT id, requester_id, payer_id, amount, status, expires_at
                FROM dinkcoin_requests
                WHERE id = %s
                FOR UPDATE
                """,
                (request_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RequestStateError("That DINK request no longer exists.")
            record = DinkRequestRecord(
                id=int(row[0]),
                requester_id=int(row[1]),
                payer_id=int(row[2]),
                amount=int(row[3]),
                status=str(row[4]),
                expires_at=row[5],
            )
            if record.payer_id != actor_id:
                raise RequestStateError("Only the requested user can respond to this.")
            if record.status != "pending":
                raise RequestStateError("This DINK request has already been handled.")
            expires_at = record.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if current_time >= expires_at:
                cursor.execute(
                    """
                    UPDATE dinkcoin_requests
                    SET status = 'expired', resolved_at = NOW()
                    WHERE id = %s
                    """,
                    (request_id,),
                )
                raise RequestStateError("This DINK request has expired.")

            sender = str(record.payer_id)
            recipient = str(record.requester_id)
            cursor.execute(
                """
                UPDATE dinkcoin_balances
                SET balance = balance - %s
                WHERE user_id = %s AND balance >= %s
                RETURNING balance
                """,
                (record.amount, sender, record.amount),
            )
            if cursor.fetchone() is None:
                raise InsufficientBalanceError("Insufficient DINK balance")
            cursor.execute(
                """
                INSERT INTO dinkcoin_balances (user_id, balance)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    balance = dinkcoin_balances.balance + EXCLUDED.balance
                """,
                (recipient, record.amount),
            )
            cursor.execute(
                """
                INSERT INTO dinkcoin_transactions
                    (from_user_id, to_user_id, amount, tx_type)
                VALUES (%s, %s, %s, 'transfer')
                """,
                (sender, recipient, record.amount),
            )
            cursor.execute(
                """
                UPDATE dinkcoin_requests
                SET status = 'accepted', resolved_at = NOW()
                WHERE id = %s
                """,
                (request_id,),
            )
            return DinkRequestRecord(
                id=record.id,
                requester_id=record.requester_id,
                payer_id=record.payer_id,
                amount=record.amount,
                status="accepted",
                expires_at=record.expires_at,
            )

    def decline_request(
        self,
        request_id: int,
        actor_id: int,
        *,
        now: datetime | None = None,
    ) -> DinkRequestRecord:
        current_time = now or datetime.now(timezone.utc)
        with self.pool.transaction() as cursor:
            cursor.execute(
                """
                SELECT id, requester_id, payer_id, amount, status, expires_at
                FROM dinkcoin_requests
                WHERE id = %s
                FOR UPDATE
                """,
                (request_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RequestStateError("That DINK request no longer exists.")
            record = DinkRequestRecord(
                id=int(row[0]),
                requester_id=int(row[1]),
                payer_id=int(row[2]),
                amount=int(row[3]),
                status=str(row[4]),
                expires_at=row[5],
            )
            if record.payer_id != actor_id:
                raise RequestStateError("Only the requested user can respond to this.")
            if record.status != "pending":
                raise RequestStateError("This DINK request has already been handled.")
            expires_at = record.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if current_time >= expires_at:
                cursor.execute(
                    """
                    UPDATE dinkcoin_requests
                    SET status = 'expired', resolved_at = NOW()
                    WHERE id = %s
                    """,
                    (request_id,),
                )
                raise RequestStateError("This DINK request has expired.")

            cursor.execute(
                """
                UPDATE dinkcoin_requests
                SET status = 'declined', resolved_at = NOW()
                WHERE id = %s
                """,
                (request_id,),
            )
            return DinkRequestRecord(
                id=record.id,
                requester_id=record.requester_id,
                payer_id=record.payer_id,
                amount=record.amount,
                status="declined",
                expires_at=record.expires_at,
            )
