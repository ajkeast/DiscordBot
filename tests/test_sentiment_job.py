"""Tests for nightly Grok sentiment job wiring."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from pydantic import ValidationError

from cogs.sentiment import Sentiment
from utils import sentiment_job
from utils.sentiment_prompt import SYSTEM_PROMPT, build_batch_user_prompt
from utils.sentiment_schema import SentimentBatch, SentimentResult


def _payload(message_id: str, **overrides) -> dict:
    payload = {
        "message_id": message_id,
        "polarity": "neutral",
        "polarity_score": 0.0,
        "emotions": ["neutral"],
        "sarcasm": False,
        "toxicity": "none",
        "directed_at": "general",
        "confidence": 0.8,
        "rationale": "Test result",
    }
    payload.update(overrides)
    return payload


def _result(message_id: str, *, polarity: str = "neutral") -> SentimentResult:
    return SentimentResult.model_validate(
        _payload(
            message_id,
            polarity=polarity,
            polarity_score=0.0 if polarity == "neutral" else 0.4,
            emotions=["neutral"] if polarity == "neutral" else ["amusement"],
        )
    )


def _chat_returning(batch: SentimentBatch) -> MagicMock:
    chat = MagicMock()
    chat.parse.return_value = (MagicMock(), batch)
    client = MagicMock()
    client.chat.create.return_value = chat
    return client


def test_system_prompt_scores_only_the_target():
    assert ">>> TARGET" in SYSTEM_PROMPT
    assert "message_id" in SYSTEM_PROMPT
    assert "sarcasm" in SYSTEM_PROMPT


def test_schema_closes_categorical_fields_and_bounds():
    schema = SentimentResult.model_json_schema()
    props = schema["properties"]
    assert props["polarity"]["enum"] == ["positive", "negative", "neutral", "mixed"]
    assert props["toxicity"]["enum"] == ["none", "mild", "moderate", "severe"]
    assert props["directed_at"]["enum"] == ["general", "person", "group", "self", "topic"]
    assert set(props["emotions"]["items"]["enum"]) == {
        "joy",
        "anger",
        "annoyance",
        "amusement",
        "sadness",
        "fear",
        "surprise",
        "disgust",
        "neutral",
    }
    assert props["emotions"]["minItems"] == 1
    assert props["emotions"]["maxItems"] == 3
    assert props["sarcasm"]["type"] == "boolean"
    assert props["polarity_score"]["minimum"] == -1
    assert props["polarity_score"]["maximum"] == 1
    assert props["confidence"]["minimum"] == 0
    assert props["confidence"]["maximum"] == 1
    assert props["rationale"]["maxLength"] == 255
    assert schema.get("additionalProperties", False) is False

    batch = SentimentBatch.model_json_schema()
    assert batch["properties"]["results"]["minItems"] == 1
    assert "$ref" in batch["properties"]["results"]["items"]


def test_schema_rejects_invented_labels_and_out_of_range_numbers():
    with pytest.raises(ValidationError):
        SentimentResult.model_validate(_payload("1", polarity="frustrated"))
    with pytest.raises(ValidationError):
        SentimentResult.model_validate(_payload("1", emotions=["frustrated"]))
    with pytest.raises(ValidationError):
        SentimentResult.model_validate(_payload("1", emotions=[]))
    with pytest.raises(ValidationError):
        SentimentResult.model_validate(_payload("1", polarity_score=1.5))
    with pytest.raises(ValidationError):
        SentimentResult.model_validate(_payload("1", confidence=-0.1))
    with pytest.raises(ValidationError):
        SentimentResult.model_validate(_payload("1", rationale="x" * 256))


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
    assert "message_id: 111" in prompt
    assert "message_id: 222" in prompt
    assert "#memes" in prompt
    assert "--- ITEM 2 ---" in prompt
    assert ">>> TARGET [bob] lol" in prompt


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
    result = _result("123", polarity="positive")
    with patch.object(sentiment_job.db_ops.db, "executemany") as execmany:
        written = sentiment_job.upsert_results([result], model="grok-4.3")
    assert written == 1
    execmany.assert_called_once()
    # psycopg3 may adapt Python bool as smallint; cast in SQL so Postgres accepts it.
    assert "%s::boolean" in execmany.call_args.args[0]
    rows = execmany.call_args.args[1]
    assert rows[0][0] == 123
    assert rows[0][1] == "positive"
    assert rows[0][3] == "amusement"
    assert rows[0][4] is False
    assert isinstance(rows[0][4], bool)
    assert rows[0][9] == "grok-4.3"


def test_upsert_clamps_scores_and_slices_rationale():
    result = SentimentResult.model_construct(
        message_id="123",
        polarity="negative",
        polarity_score=4,
        emotions=["anger"],
        sarcasm=False,
        toxicity="mild",
        directed_at="person",
        confidence=-0.2,
        rationale="y" * 300,
    )
    with patch.object(sentiment_job.db_ops.db, "executemany") as execmany:
        written = sentiment_job.upsert_results([result], model="grok-4.3")
    assert written == 1
    row = execmany.call_args.args[1][0]
    assert row[2] == 1.0
    assert row[7] == 0.0
    assert row[8] == "y" * 255


def test_score_batch_uses_structured_output():
    items = [
        {"message_id": "1", "channel_name": "general", "context_text": "t1"},
        {"message_id": "2", "channel_name": "general", "context_text": "t2"},
    ]
    parsed = SentimentBatch(results=[_result("1"), _result("2")])
    client = _chat_returning(parsed)

    results = sentiment_job.score_batch_with_grok(items, model="grok-4.3", client=client)

    assert [row.message_id for row in results] == ["1", "2"]
    kwargs = client.chat.create.call_args.kwargs
    assert kwargs["model"] == "grok-4.3"
    assert kwargs["temperature"] == 0
    assert kwargs["seed"] == sentiment_job.SENTIMENT_SEED
    assert kwargs["reasoning_effort"] == "none"
    assert kwargs["response_format"] is SentimentBatch
    assert kwargs["store_messages"] is False
    client.chat.create.return_value.parse.assert_called_once_with(SentimentBatch)


def test_score_batch_rejects_mismatched_ids():
    items = [
        {"message_id": "1", "channel_name": "general", "context_text": "t1"},
        {"message_id": "2", "channel_name": "general", "context_text": "t2"},
    ]
    client = _chat_returning(SentimentBatch(results=[_result("1"), _result("1")]))
    with pytest.raises(ValueError, match="message_ids"):
        sentiment_job.score_batch_with_grok(items, model="grok-4.3", client=client)
    assert client.chat.create.return_value.parse.call_count == 1


def test_score_batch_does_not_retry():
    items = [{"message_id": "1", "channel_name": "general", "context_text": "t1"}]
    chat = MagicMock()
    chat.parse.side_effect = RuntimeError("down")
    client = MagicMock()
    client.chat.create.return_value = chat
    with pytest.raises(RuntimeError, match="down"):
        sentiment_job.score_batch_with_grok(items, model="grok-4.3", client=client)
    assert chat.parse.call_count == 1


def test_score_batch_empty_items_skips_the_api():
    client = MagicMock()
    assert sentiment_job.score_batch_with_grok([], model="grok-4.3", client=client) == []
    client.chat.create.assert_not_called()


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

    with patch.object(sentiment_job, "_grok_client"):
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

    with patch.object(sentiment_job, "_grok_client"):
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
