"""Tests for nightly Grok sentiment job wiring."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from cogs.sentiment import Sentiment
from utils import sentiment_job
from utils.sentiment_prompt import SYSTEM_PROMPT, build_batch_user_prompt, build_user_prompt
from utils.sentiment_schema import (
    ALLOWED_EMOTIONS,
    POLARITIES,
    SentimentResult,
    parse_sentiment_batch_response,
    parse_sentiment_response,
    parse_sentiment_result,
)


def _result(message_id: str, *, polarity: str = "neutral") -> SentimentResult:
    return SentimentResult(
        message_id=message_id,
        polarity=polarity,
        polarity_score=0.0 if polarity == "neutral" else 0.4,
        emotions=["neutral"] if polarity == "neutral" else ["amusement"],
        sarcasm=False,
        toxicity="none",
        directed_at="general",
        confidence=0.8,
        rationale="Test result",
    )


def test_system_prompt_lists_allowed_emotions_and_polarities():
    for emotion in ALLOWED_EMOTIONS:
        assert emotion in SYSTEM_PROMPT
    for polarity in POLARITIES:
        assert polarity in SYSTEM_PROMPT


def test_build_user_prompt_includes_message_id():
    prompt = build_user_prompt(
        {
            "message_id": "111",
            "channel_name": "general",
            "context_text": ">>> TARGET [alice] hello",
        }
    )
    assert "message_id: 111" in prompt
    assert "#general" in prompt
    assert ">>> TARGET [alice] hello" in prompt


def test_build_batch_user_prompt_includes_all_items():
    prompt = build_batch_user_prompt(
        [
            {
                "message_id": "111",
                "channel_name": "general",
                "context_text": ">>> TARGET [alice] hello",
            },
            {
                "message_id": "222",
                "channel_name": "memes",
                "context_text": ">>> TARGET [bob] lol",
            },
        ]
    )
    assert 'Return JSON {"results": [...]}' in prompt
    assert "message_id: 111" in prompt
    assert "message_id: 222" in prompt
    assert "#memes" in prompt
    assert "--- ITEM 2 ---" in prompt


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


def test_sentiment_batch_size_default_and_override(monkeypatch):
    monkeypatch.delenv("SENTIMENT_BATCH_SIZE", raising=False)
    assert sentiment_job.sentiment_batch_size() == 10
    monkeypatch.setenv("SENTIMENT_BATCH_SIZE", "5")
    assert sentiment_job.sentiment_batch_size() == 5


def test_sentiment_batch_size_rejects_invalid(monkeypatch):
    monkeypatch.setenv("SENTIMENT_BATCH_SIZE", "nope")
    assert sentiment_job.sentiment_batch_size() == 10
    monkeypatch.setenv("SENTIMENT_BATCH_SIZE", "0")
    assert sentiment_job.sentiment_batch_size() == 10


def test_iter_batches_splits_items():
    items = [{"message_id": str(i)} for i in range(5)]
    batches = sentiment_job.iter_batches(items, 2)
    assert batches == [items[0:2], items[2:4], items[4:5]]


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


def test_run_sentiment_nightly_scores_in_batches(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("SENTIMENT_BATCH_SIZE", "2")
    unscored = pd.DataFrame(
        [
            {
                "message_id": mid,
                "member_id": "1",
                "channel_id": 2,
                "content": "gg",
                "created_at": pd.Timestamp("2026-07-20"),
                "author_name": "alice",
                "channel_name": "general",
            }
            for mid in (99, 100, 101)
        ]
    )
    items = [
        {"message_id": "99", "channel_name": "general", "context_text": "t99"},
        {"message_id": "100", "channel_name": "general", "context_text": "t100"},
        {"message_id": "101", "channel_name": "general", "context_text": "t101"},
    ]
    scored_batches = [
        [_result("99", polarity="positive"), _result("100", polarity="positive")],
        [_result("101", polarity="positive")],
    ]

    with patch.object(sentiment_job, "Client"):
        with patch.object(sentiment_job, "ensure_sentiment_table"):
            with patch.object(sentiment_job, "fetch_unscored_messages", return_value=unscored):
                with patch.object(sentiment_job, "build_prompt_items", return_value=items):
                    with patch.object(
                        sentiment_job,
                        "score_batch_with_grok",
                        side_effect=scored_batches,
                    ) as score_batch:
                        with patch.object(
                            sentiment_job, "upsert_results", side_effect=[2, 1]
                        ) as upsert:
                            written = sentiment_job.run_sentiment_nightly(limit=3)

    assert written == 3
    assert score_batch.call_count == 2
    assert score_batch.call_args_list[0].args[0] == items[:2]
    assert score_batch.call_args_list[1].args[0] == items[2:]
    assert upsert.call_count == 2
    upsert.assert_any_call(scored_batches[0], model="grok-4.3")
    upsert.assert_any_call(scored_batches[1], model="grok-4.3")


def test_run_sentiment_nightly_continues_after_batch_failure(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    monkeypatch.setenv("SENTIMENT_BATCH_SIZE", "1")
    unscored = pd.DataFrame(
        [
            {
                "message_id": mid,
                "member_id": "1",
                "channel_id": 2,
                "content": "hi",
                "created_at": pd.Timestamp("2026-07-20"),
                "author_name": "alice",
                "channel_name": "general",
            }
            for mid in (1, 2)
        ]
    )
    ok = _result("2")

    def _score(batch, *, model, client=None):
        if batch[0]["message_id"] == "1":
            raise RuntimeError("boom")
        return [ok]

    with patch.object(sentiment_job, "Client"):
        with patch.object(sentiment_job, "ensure_sentiment_table"):
            with patch.object(sentiment_job, "fetch_unscored_messages", return_value=unscored):
                with patch.object(
                    sentiment_job,
                    "build_prompt_items",
                    return_value=[
                        {"message_id": "1", "channel_name": "general", "context_text": "t1"},
                        {"message_id": "2", "channel_name": "general", "context_text": "t2"},
                    ],
                ):
                    with patch.object(sentiment_job, "score_batch_with_grok", side_effect=_score):
                        with patch.object(sentiment_job, "upsert_results", return_value=1) as upsert:
                            written = sentiment_job.run_sentiment_nightly()

    assert written == 1
    upsert.assert_called_once_with([ok], model="grok-4.3")


def test_score_batch_with_grok_falls_back_to_singles(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    items = [
        {"message_id": "1", "channel_name": "general", "context_text": "t1"},
        {"message_id": "2", "channel_name": "general", "context_text": "t2"},
    ]
    ok1 = _result("1", polarity="positive")
    ok2 = _result("2")

    with patch.object(
        sentiment_job,
        "_score_batch_once",
        side_effect=ValueError("bad json"),
    ) as batch_once:
        with patch.object(
            sentiment_job,
            "score_message_with_grok",
            side_effect=[ok1, ok2],
        ) as score_one:
            with patch.object(sentiment_job.time, "sleep"):
                results = sentiment_job.score_batch_with_grok(
                    items,
                    model="grok-4.3",
                    client=MagicMock(),
                    max_retries=2,
                )

    assert results == [ok1, ok2]
    assert batch_once.call_count == 2
    assert score_one.call_count == 2


def test_score_batch_with_grok_skips_failed_fallback_items(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    items = [
        {"message_id": "1", "channel_name": "general", "context_text": "t1"},
        {"message_id": "2", "channel_name": "general", "context_text": "t2"},
    ]
    ok2 = _result("2")

    def _score_one(item, *, model, client=None, max_retries=3):
        if item["message_id"] == "1":
            raise RuntimeError("still bad")
        return ok2

    with patch.object(sentiment_job, "_score_batch_once", side_effect=ValueError("bad json")):
        with patch.object(sentiment_job, "score_message_with_grok", side_effect=_score_one):
            with patch.object(sentiment_job.time, "sleep"):
                results = sentiment_job.score_batch_with_grok(
                    items,
                    model="grok-4.3",
                    client=MagicMock(),
                    max_retries=1,
                )

    assert results == [ok2]


def test_parse_sentiment_response_single_object():
    payload = {
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
    parsed = parse_sentiment_response(payload)
    assert parsed.message_id == "42"
    assert parsed.polarity == "neutral"


def test_parse_sentiment_response_legacy_results_wrapper():
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
    parsed = parse_sentiment_response(payload)
    assert parsed.message_id == "42"


def test_parse_sentiment_batch_response_validates_coverage():
    payload = {
        "results": [
            {
                "message_id": "1",
                "polarity": "positive",
                "polarity_score": 0.5,
                "emotions": ["joy"],
                "sarcasm": False,
                "toxicity": "none",
                "directed_at": "general",
                "confidence": 0.9,
                "rationale": "Upbeat",
            },
            {
                "message_id": "2",
                "polarity": "neutral",
                "polarity_score": 0.0,
                "emotions": ["neutral"],
                "sarcasm": False,
                "toxicity": "none",
                "directed_at": "general",
                "confidence": 0.7,
                "rationale": "Flat",
            },
        ]
    }
    parsed = parse_sentiment_batch_response(payload, expected_ids={"1", "2"})
    assert [r.message_id for r in parsed] == ["1", "2"]


def test_parse_sentiment_batch_response_rejects_missing_ids():
    payload = {
        "results": [
            {
                "message_id": "1",
                "polarity": "neutral",
                "polarity_score": 0.0,
                "emotions": ["neutral"],
                "sarcasm": False,
                "toxicity": "none",
                "directed_at": "general",
                "confidence": 0.7,
                "rationale": "Only one",
            }
        ]
    }
    with pytest.raises(ValueError, match="omitted message_ids"):
        parse_sentiment_batch_response(payload, expected_ids={"1", "2"})


def test_parse_sentiment_batch_response_rejects_duplicates():
    payload = {
        "results": [
            {
                "message_id": "1",
                "polarity": "neutral",
                "polarity_score": 0.0,
                "emotions": ["neutral"],
                "sarcasm": False,
                "toxicity": "none",
                "directed_at": "general",
                "confidence": 0.7,
                "rationale": "First",
            },
            {
                "message_id": "1",
                "polarity": "positive",
                "polarity_score": 0.4,
                "emotions": ["joy"],
                "sarcasm": False,
                "toxicity": "none",
                "directed_at": "general",
                "confidence": 0.8,
                "rationale": "Dup",
            },
        ]
    }
    with pytest.raises(ValueError, match="duplicate message_ids"):
        parse_sentiment_batch_response(payload, expected_ids={"1", "2"})


def test_coerce_emotion_used_as_polarity():
    result = parse_sentiment_result(
        {
            "message_id": "7",
            "polarity": "surprise",
            "polarity_score": 0.1,
            "emotions": ["joy"],
            "sarcasm": False,
            "toxicity": "none",
            "directed_at": "general",
            "confidence": 0.8,
            "rationale": "Unexpected twist",
        }
    )
    assert result.polarity == "neutral"
    assert result.emotions[0] == "surprise"
    assert "joy" in result.emotions


def test_invalid_polarity_still_raises():
    with pytest.raises(ValueError, match="invalid polarity"):
        parse_sentiment_result(
            {
                "message_id": "7",
                "polarity": "ecstatic",
                "polarity_score": 0.9,
                "emotions": ["joy"],
                "sarcasm": False,
                "toxicity": "none",
                "directed_at": "general",
                "confidence": 0.8,
                "rationale": "Too happy",
            }
        )


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
