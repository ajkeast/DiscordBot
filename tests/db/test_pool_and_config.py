"""Tests for centralized config parsing, pool configuration, and schema guardrails."""

from __future__ import annotations

import config
from db.schema import full_schema_statements, runtime_schema_statements


def test_db_config_honors_sql_port_and_host_port_override(monkeypatch):
    monkeypatch.setenv("SQL_HOST", "postgres")
    monkeypatch.setenv("SQL_PORT", "6432")
    cfg = config.db_config()
    assert cfg.host == "postgres"
    assert cfg.port == 6432

    monkeypatch.setenv("SQL_HOST", "db.internal:5555")
    cfg = config.db_config()
    assert cfg.host == "db.internal"
    assert cfg.port == 5555


def test_env_int_helpers_fall_back_on_invalid_values(monkeypatch):
    monkeypatch.setenv("SENTIMENT_BATCH_SIZE", "not-a-number")
    assert config.sentiment_batch_size() == config.DEFAULT_SENTIMENT_BATCH_SIZE

    monkeypatch.setenv("MUSIC_INACTIVE_TIMEOUT", "0")
    assert config.music_inactive_timeout() == config.DEFAULT_MUSIC_INACTIVE_TIMEOUT

    monkeypatch.setenv("SENTIMENT_NIGHTLY_LIMIT", "-5")
    assert config.sentiment_nightly_limit() is None

    monkeypatch.setenv("SENTIMENT_NIGHTLY_LIMIT", "250")
    assert config.sentiment_nightly_limit() == 250


def test_runtime_schema_is_additive_and_never_alters_messages():
    stmts = runtime_schema_statements()
    assert stmts
    joined = "\n".join(stmts).lower()
    assert "alter table" not in joined
    assert "drop " not in joined
    assert "create table if not exists dinkcoin_balances" in joined
    assert "create table if not exists dinkcoin_requests" in joined
    assert "create table if not exists message_sentiment" in joined
    assert "create table if not exists minecraft_sessions" in joined
    assert "create table if not exists monthly_report_posts" in joined


def test_full_schema_includes_core_tables():
    joined = "\n".join(full_schema_statements()).lower()
    for table in (
        "members",
        "emojis",
        "channels",
        "messages",
        "firstlist_id",
        "chatgpt_logs",
        "dalle_3_prompts",
        "dinkcoin_balances",
        "minecraft_daily_posts",
    ):
        assert f"create table if not exists {table}" in joined
