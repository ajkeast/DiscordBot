"""Pooled PostgreSQL access via psycopg3 + psycopg_pool."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import psycopg
from psycopg_pool import ConnectionPool

from config import DatabaseConfig, db_config

logger = logging.getLogger(__name__)


class DatabaseError(Exception):
    """Raised when a PostgreSQL connection or query fails."""


def _conninfo(cfg: DatabaseConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "host": cfg.host,
        "port": cfg.port,
    }
    if cfg.user is not None:
        kwargs["user"] = cfg.user
    if cfg.password is not None:
        kwargs["password"] = cfg.password
    if cfg.dbname is not None:
        kwargs["dbname"] = cfg.dbname
    return kwargs


class DatabasePool:
    """Lazy-initialized PostgreSQL connection pool with safe transactions."""

    def __init__(self, cfg: DatabaseConfig | None = None) -> None:
        self._cfg = cfg
        self._pool: ConnectionPool | None = None

    def _ensure_pool(self) -> ConnectionPool:
        if self._pool is None:
            cfg = self._cfg or db_config()
            try:
                self._pool = ConnectionPool(
                    kwargs=_conninfo(cfg),
                    min_size=cfg.min_size,
                    max_size=cfg.max_size,
                    open=True,
                )
            except psycopg.Error as exc:
                raise DatabaseError(f"Database pool error: {exc}") from exc
        return self._pool

    def open(self) -> None:
        self._ensure_pool()

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        pool = self._ensure_pool()
        try:
            with pool.connection() as conn:
                yield conn
        except DatabaseError:
            raise
        except psycopg.Error as exc:
            raise DatabaseError(f"Database connection error: {exc}") from exc

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Cursor]:
        """Yield a cursor inside an explicit transaction (commits or rolls back)."""
        with self.connection() as conn:
            try:
                with conn.transaction():
                    with conn.cursor() as cursor:
                        yield cursor
            except DatabaseError:
                raise
            except psycopg.Error as exc:
                raise DatabaseError(f"Database operation error: {exc}") from exc

    def execute(self, query: str, params: Sequence[Any] | None = None) -> int:
        with self.transaction() as cursor:
            cursor.execute(query, params)
            return cursor.rowcount

    def executemany(self, query: str, params_seq: Sequence[Sequence[Any]]) -> None:
        if not params_seq:
            return
        with self.transaction() as cursor:
            cursor.executemany(query, params_seq)

    def fetchone(
        self, query: str, params: Sequence[Any] | None = None
    ) -> tuple[Any, ...] | None:
        with self.connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchone()
            except psycopg.Error as exc:
                raise DatabaseError(f"Database query error: {exc}") from exc

    def fetchall(
        self, query: str, params: Sequence[Any] | None = None
    ) -> list[tuple[Any, ...]]:
        with self.connection() as conn:
            try:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchall()
            except psycopg.Error as exc:
                raise DatabaseError(f"Database query error: {exc}") from exc


_default_pool: DatabasePool | None = None


def get_pool() -> DatabasePool:
    """Return the process-wide default connection pool."""
    global _default_pool
    if _default_pool is None:
        _default_pool = DatabasePool()
    return _default_pool


def reset_pool() -> None:
    """Close and reset the process-wide pool (useful on shutdown or in tests)."""
    global _default_pool
    if _default_pool is not None:
        _default_pool.close()
        _default_pool = None
