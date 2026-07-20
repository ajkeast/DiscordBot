"""Tests for nightly Grok sentiment job wiring."""

from unittest.mock import MagicMock, patch

import pandas as pd

from cogs.sentiment import Sentiment
from utils import sentiment_job
from utils.sentiment_prompt import SYSTEM_PROMPT, build_batch_user_prompt
from utils.sentiment_schema import (
    ALLOWED_EMOTIONS,
    SentimentResult,
    parse_sentiment_batch,
)


def test_system_prompt_lists_allowed_emotions():
    for emotion in ALLOWED_EMOTIONS:
        assert emotion in SYSTEM_PROMPT


def test_build_batch_user_prompt_includes_message_ids():
    prompt = build_batch_user_prompt(
        [
            {
                "message_id": "111",
                "channel_name": "general",
                "context_text": ">>> TARGET [alice] hello",
            }
        ]
    )
    assert "message_id: 111" in prompt
    assert "#general" in prompt


def test_format_context_block_marks_target():
    text = sentiment_job.format_context_block(
        [("bob", "prior msg")],
        "alice",
        "target msg",
    )
    assert ">>> TARGET [alice] target msg" in text
    assert "[bob] prior msg" in text


def test_sentiment_enabled_requires_api_key(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert sentiment_job.sentiment_enabled() is False
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    assert sentiment_job.sentiment_enabled() is True


def test_sentiment_model_default_and_override(monkeypatch):
    monkeypatch.delenv("GROK_SENTIMENT_MODEL", raising=False)
    assert sentiment_job.sentiment_model() == "grok-4.3"
    monkeypatch.setenv("GROK_SENTIMENT_MODEL", "grok-build-0.1")
    assert sentiment_job.sentiment_model() == "grok-build-0.1"


def test_iter_batches():
    items = [{"message_id": str(i)} for i in range(10)]
    batches = sentiment_job.iter_batches(items, 4)
    assert [len(b) for b in batches] == [4, 4, 2]


def test_upsert_results_writes_rows():
    result = SentimentResult(
        message_id="123",
        polarity="positive",
        polarity_score=0.5,
        emotions=["joy"],
        sarcasm=False,
        toxicity="none",
        directed_at="general",
        confidence=0.9,
        rationale="Friendly greeting",
    )
    with patch.object(sentiment_job.db_ops.db, "executemany") as execmany:
        written = sentiment_job.upsert_results([result], model="grok-4.3")
    assert written == 1
    execmany.assert_called_once()
    rows = execmany.call_args.args[1]
    assert rows[0][0] == 123
    assert rows[0][1] == "positive"
    assert rows[0][3] == "joy"
    assert rows[0][9] == "grok-4.3"


def test_run_sentiment_nightly_scores_and_upserts(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    unscored = pd.DataFrame(
        [
            {
                "message_id": 99,
                "member_id": "1",
                "channel_id": 2,
                "content": "gg",
                "created_at": pd.Timestamp("2026-07-20"),
                "author_name": "alice",
                "channel_name": "general",
            }
        ]
    )
    scored = [
        SentimentResult(
            message_id="99",
            polarity="positive",
            polarity_score=0.4,
            emotions=["amusement"],
            sarcasm=False,
            toxicity="none",
            directed_at="topic",
            confidence=0.8,
            rationale="Casual cheer",
        )
    ]

    with patch.object(sentiment_job, "ensure_sentiment_table"):
        with patch.object(sentiment_job, "fetch_unscored_messages", return_value=unscored):
            with patch.object(
                sentiment_job,
                "build_prompt_items",
                return_value=[
                    {
                        "message_id": "99",
                        "channel_name": "general",
                        "context_text": ">>> TARGET [alice] gg",
                    }
                ],
            ):
                with patch.object(
                    sentiment_job, "score_batch_with_grok", return_value=scored
                ) as score:
                    with patch.object(sentiment_job, "upsert_results", return_value=1) as upsert:
                        written = sentiment_job.run_sentiment_nightly(limit=1)

    assert written == 1
    score.assert_called_once()
    assert score.call_args.kwargs["model"] == "grok-4.3"
    upsert.assert_called_once_with(scored, model="grok-4.3")


def test_parse_sentiment_batch_round_trip():
    payload = {
        "results": [
            {
                "message_id": "42",
                "polarity": "neutral",
                "polarity_score": 0.0,
                "emotions": ["neutral"],
                "sarcasm": False,
                "toxicity": "none",
                "directed_at": "general",
                "confidence": 0.7,
                "rationale": "No clear valence",
            }
        ]
    }
    parsed = parse_sentiment_batch(payload)
    assert parsed[0].message_id == "42"
    assert parsed[0].polarity == "neutral"


def test_sentiment_cog_starts_when_api_key_present(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    bot = MagicMock()
    with patch("discord.ext.tasks.Loop.start") as start:
        cog = Sentiment(bot)
    assert cog._enabled is True
    start.assert_called_once()


def test_sentiment_cog_disabled_without_api_key(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    bot = MagicMock()
    with patch("discord.ext.tasks.Loop.start") as start:
        cog = Sentiment(bot)
    assert cog._enabled is False
    start.assert_not_called()


def test_run_sentiment_rejects_overlap(monkeypatch):
    import asyncio

    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    bot = MagicMock()
    with patch("discord.ext.tasks.Loop.start"):
        cog = Sentiment(bot)

    async def _check():
        await cog._run_lock.acquire()
        try:
            raised = False
            try:
                await cog._run_sentiment(limit=1)
            except RuntimeError as exc:
                raised = True
                assert "already running" in str(exc)
            assert raised
        finally:
            cog._run_lock.release()

    asyncio.run(_check())
