"""Schema initialization helpers backed by db/schema.sql."""

from __future__ import annotations

from pathlib import Path

from db.pool import DatabasePool, get_pool

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_RUNTIME_MARKER = "-- Bot-managed runtime tables below"


def _split_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    for chunk in sql_text.split(";"):
        lines = [
            line
            for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        stmt = "\n".join(lines).strip()
        if stmt:
            statements.append(stmt)
    return statements


def runtime_schema_statements() -> list[str]:
    """Return additive CREATE TABLE/INDEX statements for bot-managed tables only.

    Core dinkboard-owned tables (like ``messages``) are never altered at runtime.
    """
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    _, _, runtime_section = text.partition(_RUNTIME_MARKER)
    return _split_statements(runtime_section)


def full_schema_statements() -> list[str]:
    """Return every CREATE TABLE/INDEX statement for fresh local/CI databases."""
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    return _split_statements(text)


def ensure_runtime_tables(pool: DatabasePool | None = None) -> None:
    """Create bot-managed tables in one transaction (called once at startup)."""
    target = pool or get_pool()
    with target.transaction() as cursor:
        for statement in runtime_schema_statements():
            cursor.execute(statement)


def apply_full_schema(pool: DatabasePool | None = None) -> None:
    """Apply the full canonical schema in one transaction (CI / fresh DBs)."""
    target = pool or get_pool()
    with target.transaction() as cursor:
        for statement in full_schema_statements():
            cursor.execute(statement)
