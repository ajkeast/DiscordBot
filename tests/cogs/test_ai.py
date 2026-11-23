"""Tests for AI cog commands, Imagine UI, session locking, and async TTS."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import discord
import pytest

from cogs.ai import AI, _format_slash_ask_message
from cogs.ai_imagine_ui import (
    ImagineCooldown,
    ImagineEditModal,
    ImagineResultView,
)
from tests.reporting import SECTION_COMMANDS


@pytest.fixture
def ai_cog(mock_bot):
    mock_grok = MagicMock()
    mock_grok.send_message.return_value = ("resp-id-1", "AI reply text")
    ai_logs_repo = MagicMock()
    messages_repo = MagicMock()
    return AI(
        mock_bot,
        grok=mock_grok,
        ai_logs_repo=ai_logs_repo,
        messages_repo=messages_repo,
    )


def _image_attachment(url: str) -> MagicMock:
    att = MagicMock()
    att.url = url
    att.content_type = "image/png"
    att.filename = "pic.png"
    return att


async def test_ask_prefix_and_slash(report, ai_cog, mock_ctx):
    ai_cog.grok.send_message.return_value = ("new-id", "Grok says hi")

    await ai_cog.ask.callback(ai_cog, mock_ctx, prompt="hello grok")

    assert ai_cog.last_response_id == "new-id"
    assert ai_cog._session_turns == 1
    mock_ctx.send.assert_awaited_once_with("Grok says hi")
    ai_cog.messages_repo.insert_message.assert_not_called()

    mock_ctx.send.reset_mock()
    mock_ctx.interaction = MagicMock()
    mock_ctx.message.created_at = datetime(2026, 6, 11, tzinfo=timezone.utc)
    ai_cog.grok.send_message.return_value = ("new-id-2", "answer only")

    await ai_cog.ask.callback(ai_cog, mock_ctx, prompt="what is juice?")

    ai_cog.messages_repo.insert_message.assert_called_once()
    sent = mock_ctx.send.call_args.args[0]
    report.record("slash ask format", "mention + prompt + answer", sent, section=SECTION_COMMANDS)
    assert sent == f"{mock_ctx.author.mention}: what is juice?\n\nanswer only"


def test_format_slash_ask_message_respects_2000_char_limit(mock_author):
    text = _format_slash_ask_message(mock_author, "p" * 1500, "a" * 1800)
    assert len(text) <= 2000
    assert text.startswith(f"{mock_author.mention}: ")


async def test_ask_api_error_sends_friendly_message(ai_cog, mock_ctx):
    ai_cog.grok.send_message.side_effect = RuntimeError("API down")
    await ai_cog.ask.callback(ai_cog, mock_ctx, prompt="hello")
    assert "Something broke on my end" in mock_ctx.send.call_args.args[0]


async def test_clear_and_daily_clear_reset_session(ai_cog, mock_ctx):
    ai_cog.last_response_id = "old-id"
    ai_cog._session_turns = 5

    await ai_cog.clear.callback(ai_cog, mock_ctx)
    assert ai_cog.last_response_id is None
    assert ai_cog._session_turns == 0

    ai_cog.last_response_id = "stale-id"
    ai_cog._session_turns = 8
    await ai_cog.daily_chat_clear.coro(ai_cog)
    assert ai_cog.last_response_id is None
    assert ai_cog._session_turns == 0


async def test_cog_unload_stops_registered_view(ai_cog):
    ai_cog._imagine_view.stop = MagicMock()
    await ai_cog.cog_unload()
    ai_cog._imagine_view.stop.assert_called_once()


@patch("cogs.ai_imagine_ui.generate_image")
async def test_imagine_success_and_shared_cooldown(mock_gen, ai_cog, mock_ctx):
    mock_gen.return_value = {
        "status": "success",
        "image_bytes": b"fake-jpeg",
        "revised_prompt": None,
    }
    ai_cog.imagine_cooldown = ImagineCooldown(rate=1, per=3600)

    await ai_cog.imagine.callback(ai_cog, mock_ctx, prompt="a red circle")
    ai_cog.ai_logs_repo.log_image_prompt.assert_called_once()
    mock_gen.assert_called_once_with("a red circle", input_image_url=None)
    kwargs = mock_ctx.send.return_value.edit.call_args.kwargs
    assert isinstance(kwargs["view"], ImagineResultView)
    assert kwargs["view"].timeout is None

    # Second call within window hits shared cooldown without calling API
    mock_ctx.send.reset_mock()
    await ai_cog.imagine.callback(ai_cog, mock_ctx, prompt="another")
    assert "limit" in mock_ctx.send.call_args.args[0].lower()
    assert mock_gen.call_count == 1


@patch("cogs.ai_imagine_ui.generate_image")
async def test_imagine_with_attachment_and_retry_button(mock_gen, ai_cog, mock_ctx, mock_author):
    url = "https://cdn.discordapp.com/attachments/1/a.png"
    mock_ctx.message.attachments = [_image_attachment(url)]
    mock_gen.return_value = {"status": "success", "image_bytes": b"jpeg", "revised_prompt": None}

    await ai_cog.imagine.callback(ai_cog, mock_ctx, prompt="make it neon")
    mock_gen.assert_called_once_with("make it neon", input_image_url=url)

    # Trigger Retry button on produced embed
    embed = mock_ctx.send.return_value.edit.call_args.kwargs["embed"]
    assert embed.url == url

    interaction = AsyncMock()
    interaction.user = mock_author
    interaction.client = MagicMock()
    interaction.client.get_cog.return_value = ai_cog
    interaction.message = MagicMock(embeds=[embed], attachments=[])
    interaction.response = AsyncMock()
    placeholder = AsyncMock()
    interaction.response.send_message = AsyncMock(return_value=placeholder)

    view = ImagineResultView(cooldown=ai_cog.imagine_cooldown)
    retry_btn = next(item for item in view.children if item.custom_id == "imagine:retry")
    await retry_btn.callback(interaction)
    assert mock_gen.call_count == 2
    assert mock_gen.call_args.kwargs["input_image_url"] == url


async def test_imagine_edit_modal_enforces_cooldown(mock_author):
    cooldown = ImagineCooldown(rate=1, per=3600)
    cooldown.check_and_consume(mock_author)  # exhaust quota

    modal = ImagineEditModal("https://cdn.example/img.png", cooldown=cooldown)
    modal.prompt_input._value = "turn blue"

    interaction = AsyncMock()
    interaction.user = mock_author
    interaction.response = AsyncMock()

    await modal.on_submit(interaction)
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True
    assert "limit" in interaction.response.send_message.call_args.args[0].lower()


@patch("cogs.ai.synthesize_speech", new_callable=AsyncMock)
async def test_voice_uses_async_tts_and_handles_client_error(
    mock_tts, monkeypatch, ai_cog, mock_ctx
):
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    ai_cog.grok.send_message.return_value = ("v-1", "Spoken reply")
    mock_tts.return_value = b"ID3-fake-mp3"

    await ai_cog.voice.callback(ai_cog, mock_ctx, prompt="say hi")
    mock_tts.assert_awaited_once()
    sent_file = mock_ctx.send.call_args.kwargs["file"]
    assert isinstance(sent_file, discord.File)
    assert sent_file.filename == "voice.mp3"

    mock_ctx.send.reset_mock()
    mock_tts.side_effect = aiohttp.ClientError("boom")
    await ai_cog.voice.callback(ai_cog, mock_ctx, prompt="say hi")
    assert "Something broke generating speech" in mock_ctx.send.call_args.args[0]
