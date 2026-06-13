"""Mocked tests for AI cog commands."""

from unittest.mock import MagicMock, patch

import discord

from cogs.ai import AI


async def test_ask_success(ai_cog, mock_ctx):
    ai_cog.grok.send_message.return_value = ("new-id", "Grok says hi")

    await ai_cog.ask.callback(ai_cog, mock_ctx, arg="hello grok")

    ai_cog.grok.send_message.assert_called_once()
    mock_ctx.send.assert_awaited_once_with("Grok says hi")
    assert ai_cog.last_response_id == "new-id"
    assert ai_cog._session_turns == 1


async def test_ask_api_error(ai_cog, mock_ctx):
    ai_cog.grok.send_message.side_effect = RuntimeError("API down")

    await ai_cog.ask.callback(ai_cog, mock_ctx, arg="hello")

    mock_ctx.send.assert_awaited_once_with(
        "Something broke on my end, dude. Check the bot logs and try again."
    )


async def test_clear_resets_session(ai_cog, mock_ctx):
    ai_cog.last_response_id = "old-id"
    ai_cog._session_turns = 5

    await ai_cog.clear.callback(ai_cog, mock_ctx)

    assert ai_cog.last_response_id is None
    assert ai_cog._session_turns == 0
    mock_ctx.send.assert_awaited_once_with("Chat history cleared! Starting fresh, dude! 🤙")


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_success(mock_imagine, mock_db_ops, ai_cog, mock_ctx):
    mock_imagine.return_value = {
        "status": "success",
        "image_bytes": b"fake-jpeg-bytes",
        "revised_prompt": None,
    }

    await ai_cog.imagine.callback(ai_cog, mock_ctx, arg="a red circle")

    mock_db_ops.write_dalle_entry.assert_called_once()
    mock_ctx.send.assert_awaited_once()
    assert mock_ctx.send.call_args.kwargs.get("file") is not None


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_failure(mock_imagine, ai_cog, mock_ctx):
    mock_imagine.return_value = {"status": "error", "error": "rate limited"}

    await ai_cog.imagine.callback(ai_cog, mock_ctx, arg="a red circle")

    mock_ctx.send.assert_awaited_once()
    embed = mock_ctx.send.call_args.kwargs["embed"]
    assert embed.title == "❌ Error"


@patch("cogs.ai.requests.post")
@patch("cogs.ai.os.getenv", return_value="test-key")
async def test_voice_success(mock_getenv, mock_post, ai_cog, mock_ctx):
    ai_cog.grok.send_message.return_value = ("tts-id", "spoken text")
    mock_response = MagicMock()
    mock_response.content = b"fake-mp3"
    mock_response.raise_for_status = MagicMock()
    mock_post.return_value = mock_response

    await ai_cog.voice.callback(ai_cog, mock_ctx, text="say hello")

    mock_post.assert_called_once()
    mock_ctx.send.assert_awaited_once()
    assert mock_ctx.send.call_args.kwargs["file"].filename == "voice.mp3"


@patch("cogs.ai.os.getenv", return_value=None)
async def test_voice_missing_api_key(_mock_getenv, ai_cog, mock_ctx):
    await ai_cog.voice.callback(ai_cog, mock_ctx, text="hello")

    mock_ctx.send.assert_awaited_once_with("XAI API key not configured.")
