"""Incremental nightly message sentiment scoring via xAI structured outputs."""

from __future__ import annotations

import logging
from typing import Literal, Sequence

from pydantic import BaseModel, Field
from xai_sdk import Client
from xai_sdk.chat import system, user

from config import (
    sentiment_batch_size,
    sentiment_model,
    sentiment_nightly_limit,
    xai_api_key,
)
from db.sentiment import SentimentRepository, UnscoredMessage
from services.xai import create_xai_client

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_SIZE = 3
DEFAULT_MAX_CONTENT_CHARS = 500
SENTIMENT_SEED = 7
PROGRESS_EVERY = 25

Polarity = Literal["positive", "negative", "neutral", "mixed"]
Toxicity = Literal["none", "mild", "moderate", "severe"]
DirectedAt = Literal["general", "person", "group", "self", "topic"]
Emotion = Literal[
    "joy",
    "anger",
    "annoyance",
    "amusement",
    "sadness",
    "fear",
    "surprise",
    "disgust",
    "neutral",
]

SYSTEM_PROMPT = """You are analyzing Discord chat messages for sentiment.

Score ONLY the message marked >>> TARGET.
Use the preceding messages solely as local context (sarcasm, irony, in-jokes, replies).

Copy each message_id exactly.
polarity_score runs from -1 (very negative) to 1 (very positive); mixed is near 0.
Set sarcasm true when the surface tone conflicts with the context.
Short Discord slang (lol, lmao, gg) can still carry clear polarity.
Keep each rationale to at most 15 words and do not quote the message.
Return one result per input item.
"""


class SentimentResult(BaseModel):
    """One scored message. Categorical fields are closed enums."""

    message_id: str = Field(description="Copy the input message_id exactly")
    polarity: Polarity
    polarity_score: float = Field(
        ge=-1,
        le=1,
        description="-1 very negative, 1 very positive; mixed near 0",
    )
    emotions: list[Emotion] = Field(min_length=1, max_length=3)
    sarcasm: bool
    toxicity: Toxicity
    directed_at: DirectedAt
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=255, description="At most 15 words")


class SentimentBatch(BaseModel):
    """One result per input message."""

    results: list[SentimentResult] = Field(min_length=1)


def sentiment_enabled() -> bool:
    return bool(xai_api_key())


def _truncate(text: str, max_chars: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def format_context_block(
    priors: Sequence[tuple[str, str]],
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


def build_batch_user_prompt(items: Sequence[dict[str, str]]) -> str:
    blocks = [f"Score these {len(items)} Discord message(s).\n"]
    for idx, item in enumerate(items, start=1):
        blocks.append(
            f"--- ITEM {idx} ---\n"
            f"message_id: {item['message_id']}\n"
            f"channel: #{item.get('channel_name', 'unknown')}\n"
            f"{item['context_text']}\n"
        )
    return "\n".join(blocks)


def build_prompt_items(
    messages: Sequence[UnscoredMessage],
    *,
    max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
) -> list[dict[str, str]]:
    return [
        {
            "message_id": str(msg.message_id),
            "channel_name": msg.channel_name,
            "context_text": format_context_block(
                msg.priors,
                msg.author_name,
                msg.content,
                max_content_chars=max_content_chars,
            ),
        }
        for msg in messages
    ]


def iter_batches(items: list[dict[str, str]], batch_size: int) -> list[list[dict[str, str]]]:
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def score_batch_with_grok(
    items: list[dict[str, str]],
    *,
    model: str,
    client: Client | None = None,
) -> list[SentimentResult]:
    """Score one batch with structured outputs; raises on malformed IDs."""
    if not items:
        return []
    xai = client or create_xai_client(timeout=120)

    chat = xai.chat.create(
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


def results_to_rows(
    results: Sequence[SentimentResult], *, model: str
) -> list[tuple]:
    rows: list[tuple] = []
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
    return rows


def run_sentiment_nightly(
    *,
    limit: int | None = None,
    repo: SentimentRepository | None = None,
    client: Client | None = None,
) -> int:
    """Score unscored messages in batches using one SQL LATERAL read."""
    if not sentiment_enabled() and client is None:
        raise RuntimeError("XAI_API_KEY is required for sentiment scoring")

    effective_limit = limit if limit is not None else sentiment_nightly_limit()
    batch_size = sentiment_batch_size()
    model = sentiment_model()
    xai = client or create_xai_client(timeout=120)
    repository = repo or SentimentRepository()

    unscored = repository.fetch_unscored_with_priors(
        limit=effective_limit, context_size=DEFAULT_CONTEXT_SIZE
    )
    if not unscored:
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
            results = score_batch_with_grok(batch, model=model, client=xai)
            rows = results_to_rows(results, model=model)
            written += repository.upsert_scores(rows)
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
