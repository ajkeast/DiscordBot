"""Tests for structured sentiment scoring schema, prompt formatting, and batch runner."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from db.sentiment import UnscoredMessage
from services.sentiment import (
    SentimentBatch,
    SentimentResult,
    build_batch_user_prompt,
    format_context_block,
    results_to_rows,
    run_sentiment_nightly,
    score_batch_with_grok,
)


def _payload(message_id: str, **overrides) -> dict:
    base = {
        "message_id": message_id,
        "polarity": "neutral",
        "polarity_score": 0.0,
        "emotions": ["neutral"],
        "sarcasm": False,
        "toxicity": "none",
        "directed_at": "general",
        "confidence": 0.85,
        "rationale": "Routine chat message",
    }
    base.update(overrides)
    return base


def test_sentiment_schema_enforces_closed_enums_and_bounds():
    with pytest.raises(ValidationError):
        SentimentResult.model_validate(_payload("1", polarity="frustrated"))
    with pytest.raises(ValidationError):
        SentimentResult.model_validate(_payload("1", emotions=[]))
    with pytest.raises(ValidationError):
        SentimentResult.model_validate(_payload("1", polarity_score=1.5))
    with pytest.raises(ValidationError):
        SentimentResult.model_validate(_payload("1", confidence=-0.1))


def test_context_formatting_and_row_clamping():
    ctx = format_context_block([("bob", "prior")], "alice", "target")
    assert "[bob] prior" in ctx
    assert ">>> TARGET [alice] target" in ctx

    prompt = build_batch_user_prompt(
        [{"message_id": "111", "channel_name": "general", "context_text": ctx}]
    )
    assert "message_id: 111" in prompt
    assert "#general" in prompt

    constructed = SentimentResult.model_construct(
        message_id="123",
        polarity="negative",
        polarity_score=5.0,
        emotions=["anger"],
        sarcasm=True,
        toxicity="mild",
        directed_at="person",
        confidence=-0.5,
        rationale="r" * 300,
    )
    rows = results_to_rows([constructed], model="grok-4.3")
    assert rows[0][2] == 1.0
    assert rows[0][7] == 0.0
    assert len(rows[0][8]) == 255


def test_score_batch_rejects_mismatched_ids():
    client = MagicMock()
    chat = MagicMock()
    client.chat.create.return_value = chat
    chat.parse.return_value = (
        MagicMock(),
        SentimentBatch(results=[SentimentResult.model_validate(_payload("999"))]),
    )
    with pytest.raises(ValueError, match="do not match"):
        score_batch_with_grok(
            [{"message_id": "111", "channel_name": "general", "context_text": "hi"}],
            model="grok-4.3",
            client=client,
        )


def test_run_sentiment_nightly_uses_single_lateral_read(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("SENTIMENT_BATCH_SIZE", "2")

    repo = MagicMock()
    repo.fetch_unscored_with_priors.return_value = [
        UnscoredMessage(
            message_id=1,
            member_id=10,
            channel_id=20,
            content="gg!",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            author_name="alice",
            channel_name="general",
            priors=(("bob", "nice match"),),
        ),
        UnscoredMessage(
            message_id=2,
            member_id=11,
            channel_id=20,
            content="oof",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            author_name="bob",
            channel_name="general",
            priors=(),
        ),
    ]
    repo.upsert_scores.return_value = 2

    client = MagicMock()
    chat = MagicMock()
    client.chat.create.return_value = chat
    chat.parse.return_value = (
        MagicMock(),
        SentimentBatch(
            results=[
                SentimentResult.model_validate(_payload("1", polarity="positive", polarity_score=0.7)),
                SentimentResult.model_validate(_payload("2", polarity="negative", polarity_score=-0.3)),
            ]
        ),
    )

    written = run_sentiment_nightly(repo=repo, client=client)
    assert written == 2
    repo.fetch_unscored_with_priors.assert_called_once()
    repo.upsert_scores.assert_called_once()
