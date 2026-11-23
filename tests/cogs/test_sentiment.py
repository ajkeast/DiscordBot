"""Tests for Sentiment cog authorization, lifecycle, and manual trigger."""

from __future__ import annotations

from unittest.mock import patch

from cogs.sentiment import Sentiment


async def test_score_sentiment_rejects_non_admin(monkeypatch, mock_bot, mock_ctx):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    cog = Sentiment(mock_bot)

    await cog.score_sentiment.callback(cog, mock_ctx, limit=5)

    assert "Only server admins" in mock_ctx.send.call_args.args[0]


async def test_score_sentiment_allows_admin_and_reports_count(
    monkeypatch, mock_bot, mock_ctx, mock_admin_author
):
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    mock_ctx.author = mock_admin_author
    cog = Sentiment(mock_bot)

    with patch("cogs.sentiment.run_sentiment_nightly", return_value=12) as run_mock:
        await cog.score_sentiment.callback(cog, mock_ctx, limit=25)

    run_mock.assert_called_once_with(limit=25)
    mock_ctx.send.assert_awaited_once_with("Scored 12 messages.")


async def test_score_sentiment_validates_limit_and_disabled_state(
    monkeypatch, mock_bot, mock_ctx, mock_admin_author
):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    mock_ctx.author = mock_admin_author
    cog = Sentiment(mock_bot)

    await cog.score_sentiment.callback(cog, mock_ctx)
    assert "disabled" in mock_ctx.send.call_args.args[0].lower()

    mock_ctx.send.reset_mock()
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    cog = Sentiment(mock_bot)
    await cog.score_sentiment.callback(cog, mock_ctx, limit=0)
    assert "at least 1" in mock_ctx.send.call_args.args[0].lower()
