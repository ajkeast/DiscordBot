"""Incremental nightly sentiment scoring with cheap Grok (xAI).

Only scores messages that are not yet in message_sentiment. Structured outputs
fix the categorical fields; written rows stay compatible with message_sentiment.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from xai_sdk import Client
from xai_sdk.chat import system, user

from utils.db import db_ops
from utils.sentiment_prompt import SYSTEM_PROMPT, build_batch_user_prompt
from utils.sentiment_schema import SentimentBatch, SentimentResult

load_dotenv()

logger = logging.getLogger(__name__)

# grok-4.3 + reasoning_effort=none is the cheap chat path after fast-model retirement.
DEFAULT_SENTIMENT_MODEL = "grok-4.3"
DEFAULT_BATCH_SIZE = 10
DEFAULT_CONTEXT_SIZE = 3
DEFAULT_MAX_CONTENT_CHARS = 500
# Best-effort deterministic sampling. xAI honors seed when it is set.
SENTIMENT_SEED = 7
PROGRESS_EVERY = 25

ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS message_sentiment (
  message_id BIGINT PRIMARY KEY REFERENCES messages(id),
  polarity TEXT NOT NULL CHECK (polarity IN ('positive','negative','neutral','mixed')),
  polarity_score REAL NOT NULL,
  emotions VARCHAR(128) NOT NULL,
  sarcasm BOOLEAN NOT NULL,
  toxicity TEXT NOT NULL CHECK (toxicity IN ('none','mild','moderate','severe')),
  directed_at TEXT NOT NULL CHECK (directed_at IN ('general','person','group','self','topic')),
  confidence REAL NOT NULL,
  rationale VARCHAR(255) NOT NULL,
  model VARCHAR(64) NOT NULL,
  scored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

# Newest first so recent holes are filled before ancient backfill gaps.
# messages.content is the original text. Edits live in edited_content and are
# not rescored; a row already in message_sentiment stays tied to the original.
UNSCORED_SQL = """
SELECT
    m.id AS message_id,
    m.member_id,
    m.channel_id,
    m.content,
    m.created_at,
    COALESCE(mem.display_name, mem.user_name, m.member_id) AS author_name,
    COALESCE(c.channel_name, 'unknown') AS channel_name
FROM messages m
LEFT JOIN members mem ON m.member_id = mem.id
LEFT JOIN channels c ON m.channel_id = c.id
LEFT JOIN message_sentiment s ON s.message_id = m.id
WHERE s.message_id IS NULL
  AND m.content IS NOT NULL
  AND TRIM(m.content) <> ''
ORDER BY m.created_at DESC, m.id DESC
"""

PRIORS_SQL = """
SELECT
    m.id,
    m.member_id,
    m.content,
    m.created_at,
    COALESCE(mem.display_name, mem.user_name, m.member_id) AS author_name
FROM messages m
LEFT JOIN members mem ON m.member_id = mem.id
WHERE m.channel_id = %s
  AND (
    m.created_at < %s
    OR (m.created_at = %s AND m.id < %s)
  )
ORDER BY m.created_at DESC, m.id DESC
LIMIT %s
"""

UPSERT_SQL = """
INSERT INTO message_sentiment (
  message_id, polarity, polarity_score, emotions, sarcasm, toxicity,
  directed_at, confidence, rationale, model
) VALUES (
  %s, %s, %s, %s, %s::boolean, %s, %s, %s, %s, %s
)
ON CONFLICT (message_id) DO UPDATE SET
  polarity = EXCLUDED.polarity,
  polarity_score = EXCLUDED.polarity_score,
  emotions = EXCLUDED.emotions,
  sarcasm = EXCLUDED.sarcasm,
  toxicity = EXCLUDED.toxicity,
  directed_at = EXCLUDED.directed_at,
  confidence = EXCLUDED.confidence,
  rationale = EXCLUDED.rationale,
  model = EXCLUDED.model,
  updated_at = CURRENT_TIMESTAMP
"""


def sentiment_model() -> str:
    return os.getenv("GROK_SENTIMENT_MODEL", DEFAULT_SENTIMENT_MODEL).strip() or DEFAULT_SENTIMENT_MODEL


def sentiment_enabled() -> bool:
    return bool(os.getenv("XAI_API_KEY", "").strip())


def sentiment_batch_size() -> int:
    raw = os.getenv("SENTIMENT_BATCH_SIZE", str(DEFAULT_BATCH_SIZE)).strip()
    try:
        size = int(raw)
    except ValueError:
        logger.warning(
            "Invalid SENTIMENT_BATCH_SIZE=%r; using %s",
            raw,
            DEFAULT_BATCH_SIZE,
        )
        return DEFAULT_BATCH_SIZE
    if size < 1:
        logger.warning(
            "SENTIMENT_BATCH_SIZE=%s must be >= 1; using %s",
            size,
            DEFAULT_BATCH_SIZE,
        )
        return DEFAULT_BATCH_SIZE
    return size


def _truncate(text: str, max_chars: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def format_context_block(
    priors: list[tuple[str, str]],
    target_author: str,
    target_content: str,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
) -> str:
    lines: list[str] = []
    for author, content in priors:
        lines.append(f"[{_truncate(author, 40)}] {_truncate(content, max_content_chars)}")
    lines.append(
        f">>> TARGET [{_truncate(target_author, 40)}] "
        f"{_truncate(target_content, max_content_chars)}"
    )
    return "\n".join(lines)


def ensure_sentiment_table() -> None:
    db_ops.db.execute(ENSURE_TABLE_SQL)


def fetch_unscored_messages(limit: int | None = None) -> pd.DataFrame:
    if limit is not None:
        df = db_ops.db.fetch_df(UNSCORED_SQL + " LIMIT %s", (int(limit),))
    else:
        df = db_ops.db.fetch_df(UNSCORED_SQL)
    if df.empty:
        return df
    df["message_id"] = df["message_id"].astype("int64")
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["content"] = df["content"].fillna("").astype(str)
    df["author_name"] = df["author_name"].fillna("unknown").astype(str)
    df["channel_name"] = df["channel_name"].fillna("unknown").astype(str)
    return df


def fetch_priors(
    channel_id: Any,
    created_at: Any,
    message_id: int,
    *,
    context_size: int = DEFAULT_CONTEXT_SIZE,
) -> list[tuple[str, str]]:
    """Return up to context_size prior in-channel messages oldest→newest."""
    df = db_ops.db.fetch_df(
        PRIORS_SQL,
        (channel_id, created_at, created_at, message_id, context_size),
    )
    if df.empty:
        return []
    # Query returns newest-first; prompt wants chronological priors.
    rows = list(reversed(df.to_dict(orient="records")))
    priors: list[tuple[str, str]] = []
    for row in rows:
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        author = str(row.get("author_name") or "unknown")
        priors.append((author, content))
    return priors


def build_prompt_items(
    unscored: pd.DataFrame,
    *,
    context_size: int = DEFAULT_CONTEXT_SIZE,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
) -> list[dict]:
    items: list[dict] = []
    for row in unscored.itertuples(index=False):
        priors = fetch_priors(
            row.channel_id,
            row.created_at,
            int(row.message_id),
            context_size=context_size,
        )
        items.append(
            {
                "message_id": str(row.message_id),
                "channel_name": str(row.channel_name),
                "context_text": format_context_block(
                    priors,
                    str(row.author_name),
                    str(row.content),
                    max_content_chars=max_content_chars,
                ),
            }
        )
    return items


def iter_batches(items: list[dict], batch_size: int) -> list[list[dict]]:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def _grok_client() -> Client:
    api_key = os.getenv("XAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("XAI_API_KEY is required for sentiment scoring")
    return Client(api_key=api_key, timeout=120)


def score_batch_with_grok(
    items: list[dict],
    *,
    model: str,
    client: Client | None = None,
) -> list[SentimentResult]:
    """Score one batch with structured outputs.

    Raises if the call fails or the returned message_ids do not match the batch.
    The caller logs and skips that batch; those messages are retried next run.
    """
    if not items:
        return []
    if client is None:
        client = _grok_client()

    chat = client.chat.create(
        model=model,
        temperature=0,
        seed=SENTIMENT_SEED,
        reasoning_effort="none",
        response_format=SentimentBatch,
        store_messages=False,
    )
    chat.append(system(SYSTEM_PROMPT))
    chat.append(user(build_batch_user_prompt(items)))
    _response, batch = chat.parse(SentimentBatch)

    expected = {item["message_id"] for item in items}
    got = [row.message_id for row in batch.results]
    if len(got) != len(set(got)) or set(got) != expected:
        raise ValueError(f"message_ids {got} do not match the batch {sorted(expected)}")
    return list(batch.results)


def upsert_results(results: list[SentimentResult], *, model: str) -> int:
    if not results:
        return 0
    rows = []
    for r in results:
        mid = str(r.message_id).strip()
        if not mid.isdigit() or len(mid) > 20:
            logger.warning("Skipping invalid message_id=%r", mid)
            continue
        rows.append(
            (
                int(mid),
                r.polarity,
                min(1.0, max(-1.0, float(r.polarity_score))),
                ",".join(r.emotions),
                bool(r.sarcasm),
                r.toxicity,
                r.directed_at,
                min(1.0, max(0.0, float(r.confidence))),
                r.rationale[:255],
                model,
            )
        )
    if not rows:
        return 0
    db_ops.db.executemany(UPSERT_SQL, rows)
    return len(rows)


def run_sentiment_nightly(*, limit: int | None = None) -> int:
    """Score unscored messages and upsert them into message_sentiment.

    One structured-output call per batch, newest first. A failed batch is logged
    and skipped; those messages stay unscored and are picked up on the next run.

    Returns the number of rows written.
    """
    if not sentiment_enabled():
        raise RuntimeError("XAI_API_KEY is required for sentiment scoring")

    if limit is None:
        raw = os.getenv("SENTIMENT_NIGHTLY_LIMIT", "").strip()
        if raw:
            limit = int(raw)

    batch_size = sentiment_batch_size()
    model = sentiment_model()
    client = _grok_client()

    ensure_sentiment_table()
    unscored = fetch_unscored_messages(limit=limit)
    if unscored.empty:
        logger.info("No unscored messages")
        return 0

    items = build_prompt_items(unscored)
    batches = iter_batches(items, batch_size)
    logger.info(
        "Scoring %s unscored messages with %s (batch_size=%s, batches=%s)...",
        len(items),
        model,
        batch_size,
        len(batches),
    )

    written = 0
    failures = 0
    processed = 0
    for batch_idx, batch in enumerate(batches, start=1):
        try:
            results = score_batch_with_grok(batch, model=model, client=client)
            written += upsert_results(results, model=model)
        except Exception:
            failures += len(batch)
            logger.exception(
                "Sentiment batch %s/%s failed entirely (%s messages)",
                batch_idx,
                len(batches),
                len(batch),
            )
        processed += len(batch)
        if processed % PROGRESS_EVERY < len(batch) or batch_idx == len(batches):
            logger.info(
                "Sentiment progress %s/%s (written=%s, failures=%s, batch=%s/%s)",
                processed,
                len(items),
                written,
                failures,
                batch_idx,
                len(batches),
            )

    logger.info("Upserted %s sentiment rows (%s failures)", written, failures)
    return written
