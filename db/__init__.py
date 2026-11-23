"""Database pool, canonical schema helpers, and domain repositories."""

from db.ai_logs import AILogsRepository
from db.dinkcoin import (
    DinkCoinRepository,
    DinkRequestRecord,
    InsufficientBalanceError,
    LedgerEntry,
    RequestStateError,
)
from db.first_game import FirstEntry, FirstGameRepository
from db.messages import MessagesRepository
from db.minecraft import MinecraftRepository
from db.pool import DatabaseError, DatabasePool, get_pool, reset_pool
from db.schema import apply_full_schema, ensure_runtime_tables
from db.sentiment import SentimentRepository, UnscoredMessage
from db.server import MonthlyCount, ServerRepository

__all__ = [
    "AILogsRepository",
    "DatabaseError",
    "DatabasePool",
    "DinkCoinRepository",
    "DinkRequestRecord",
    "FirstEntry",
    "FirstGameRepository",
    "InsufficientBalanceError",
    "LedgerEntry",
    "MessagesRepository",
    "MinecraftRepository",
    "MonthlyCount",
    "RequestStateError",
    "SentimentRepository",
    "ServerRepository",
    "UnscoredMessage",
    "apply_full_schema",
    "ensure_runtime_tables",
    "get_pool",
    "reset_pool",
]
