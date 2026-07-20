"""Incremental nightly sentiment scoring with cheap Grok (xAI).

Only scores messages that are not yet in message_sentiment. Uses the same
prompt/schema shape as the Sentiment-Analysis backfill so rows stay compatible.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from xai_sdk import Client
from xai_sdk.chat import system, user

from utils.db import db_ops
from utils.sentiment_prompt import SYSTEM_PROMPT, build_batch_user_prompt
from utils.sentiment_schema import SentimentResult, parse_sentiment_batch

load_dotenv()

logger = logging.getLogger(__name__)

# grok-4.3 + reasoning_effort=none is the cheap chat path after fast-model retirement.
DEFAULT_SENTIMENT_MODEL = "grok-4.3"
DEFAULT_BATCH_SIZE = 15
DEFAULT_CONTEXT_SIZE = 3
DEFAULT_MAX_CONTENT_CHARS = 500
DEFAULT_MAX_RETRIES = 3

ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS message_sentiment (
  message_id BIGINT(20) NOT NULL,
  polarity ENUM('positive','negative','neutral','mixed') NOT NULL,
  polarity_score FLOAT NOT NULL,
  emotions VARCHAR(128) NOT NULL,
  sarcasm TINYINT(1) NOT NULL,
  toxicity ENUM('none','mild','moderate','severe') NOT NULL,
  directed_at ENUM('general','person','group','self','topic') NOT NULL,
  confidence FLOAT NOT NULL,
  rationale VARCHAR(255) NOT NULL,
  model VARCHAR(64) NOT NULL,
  scored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (message_id),
  KEY idx_sentiment_scored_at (scored_at),
  KEY idx_sentiment_polarity (polarity),
  CONSTRAINT fk_sentiment_message FOREIGN KEY (message_id) REFERENCES messages (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

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
ORDER BY m.channel_id, m.created_at, m.id
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
  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON DUPLICATE KEY UPDATE
  polarity = VALUES(polarity),
  polarity_score = VALUES(polarity_score),
  emotions = VALUES(emotions),
  sarcasm = VALUES(sarcasm),
  toxicity = VALUES(toxicity),
  directed_at = VALUES(directed_at),
  confidence = VALUES(confidence),
  rationale = VALUES(rationale),
  model = VALUES(model),
  updated_at = CURRENT_TIMESTAMP
"""


def sentiment_model() -> str:
    return os.getenv("GROK_SENTIMENT_MODEL", DEFAULT_SENTIMENT_MODEL).strip() or DEFAULT_SENTIMENT_MODEL


def sentiment_enabled() -> bool:
    return bool(os.getenv("XAI_API_KEY", "").strip())


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
    df = db_ops.db.fetch_df(UNSCORED_SQL)
    if df.empty:
        return df
    if limit is not None:
        df = df.head(limit).copy()
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
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def score_batch_with_grok(
    items: list[dict],
    *,
    model: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> list[SentimentResult]:
    """Score one batch via Grok structured JSON (no tools / no reasoning)."""
    api_key = os.getenv("XAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("XAI_API_KEY is required for sentiment scoring")

    client = Client(api_key=api_key, timeout=600)
    user_prompt = build_batch_user_prompt(items)
    expected_ids = {item["message_id"] for item in items}
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            chat = client.chat.create(
                model=model,
                temperature=0.2,
                reasoning_effort="none",
                response_format="json_object",
                store_messages=False,
            )
            chat.append(system(SYSTEM_PROMPT))
            chat.append(user(user_prompt))
            response = chat.sample()
            parsed = parse_sentiment_batch(json.loads(response.content))
            results = [r for r in parsed if r.message_id in expected_ids]
            got_ids = {r.message_id for r in results}
            missing = expected_ids - got_ids
            if missing:
                raise ValueError(f"Model omitted message_ids: {sorted(missing)[:5]}")
            return results
        except Exception as exc:  # noqa: BLE001 — retry transient/parse failures
            last_error = exc
            logger.warning(
                "Sentiment batch attempt %s/%s failed: %s",
                attempt + 1,
                max_retries,
                exc,
            )
            time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Sentiment batch failed after {max_retries} attempts: {last_error}")


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
                float(r.polarity_score),
                ",".join(r.emotions),
                int(bool(r.sarcasm)),
                r.toxicity,
                r.directed_at,
                float(r.confidence),
                r.rationale[:255],
                model,
            )
        )
    if not rows:
        return 0
    db_ops.db.executemany(UPSERT_SQL, rows)
    return len(rows)


def run_sentiment_nightly(*, limit: int | None = None) -> int:
    """
    Score unscored messages with Grok and upsert into message_sentiment.

    Returns number of rows written.
    """
    if not sentiment_enabled():
        raise RuntimeError("XAI_API_KEY is required for sentiment scoring")

    if limit is None:
        raw = os.getenv("SENTIMENT_NIGHTLY_LIMIT", "").strip()
        if raw:
            limit = int(raw)

    batch_size = int(os.getenv("SENTIMENT_BATCH_SIZE", str(DEFAULT_BATCH_SIZE)))
    model = sentiment_model()

    ensure_sentiment_table()
    unscored = fetch_unscored_messages(limit=limit)
    if unscored.empty:
        logger.info("No unscored messages")
        return 0

    items = build_prompt_items(unscored)
    logger.info("Scoring %s unscored messages with %s...", len(items), model)

    written = 0
    for batch in iter_batches(items, batch_size):
        results = score_batch_with_grok(batch, model=model)
        written += upsert_results(results, model=model)

    logger.info("Upserted %s sentiment rows", written)
    return written
