"""Mocked tests for AI cog commands."""

from unittest.mock import AsyncMock, MagicMock, patch

from discord.ext import commands

from bot import DinkBot
from cogs.ai import AI
from tests.reporting import SECTION_COMMANDS
from utils.constants import (
    IMAGINE_RATE_LIMIT,
    IMAGINE_RATE_PERIOD_SECONDS,
    MAX_IMAGINE_INPUT_IMAGES,
)

def _image_attachment(url: str) -> MagicMock:
    attachment = MagicMock()
    attachment.url = url
    attachment.content_type = "image/png"
    return attachment


async def test_ask_success(report, ai_cog, mock_ctx):
    expected = "Grok says hi"
    ai_cog.grok.send_message.return_value = ("new-id", expected)

    await ai_cog.ask.callback(ai_cog, mock_ctx, prompt="hello grok")

    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    report.record("last_response_id", "new-id", ai_cog.last_response_id, section=SECTION_COMMANDS)
    report.record("session turns", 1, ai_cog._session_turns, section=SECTION_COMMANDS)

    ai_cog.grok.send_message.assert_called_once()
    mock_ctx.send.assert_awaited_once_with(expected)
    assert ai_cog.last_response_id == "new-id"
    assert ai_cog._session_turns == 1


async def test_ask_slash_inserts_message_row(report, mock_db_ops, ai_cog, mock_ctx):
    """Slash skips on_message; AI logging FKs message_id → messages.id."""
    from datetime import datetime, timezone

    mock_ctx.interaction = MagicMock()
    mock_ctx.message.created_at = datetime(2024, 6, 11, tzinfo=timezone.utc)
    mock_ctx.send = AsyncMock()
    ai_cog.grok.send_message.return_value = ("new-id", "ok")

    await ai_cog.ask.callback(ai_cog, mock_ctx, prompt="hello slash")

    mock_db_ops.update_messages.assert_called_once()
    row = mock_db_ops.update_messages.call_args.args[0]
    report.record("message id logged", mock_ctx.message.id, row[0], section=SECTION_COMMANDS)
    report.record("message content", "hello slash", row[3], section=SECTION_COMMANDS)
    assert row[0] == mock_ctx.message.id
    assert row[3] == "hello slash"
    ai_cog.grok.send_message.assert_called_once()


async def test_ask_slash_single_message_with_prompt(report, mock_db_ops, ai_cog, mock_ctx):
    from datetime import datetime, timezone

    mock_ctx.interaction = MagicMock()
    mock_ctx.message.created_at = datetime(2024, 6, 11, tzinfo=timezone.utc)
    mock_ctx.send = AsyncMock()
    ai_cog.grok.send_message.return_value = ("new-id", "answer only")

    await ai_cog.ask.callback(ai_cog, mock_ctx, prompt="what is juice?")

    expected = f"{mock_ctx.author.mention}: what is juice?\n\nanswer only"
    actual = mock_ctx.send.call_args.args[0]
    report.record("slash ask message", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)


async def test_ask_prefix_skips_message_insert(report, mock_db_ops, ai_cog, mock_ctx):
    mock_ctx.interaction = None
    ai_cog.grok.send_message.return_value = ("new-id", "ok")

    await ai_cog.ask.callback(ai_cog, mock_ctx, prompt="hello prefix")

    mock_db_ops.update_messages.assert_not_called()
    report.record("prefix message insert", "skipped", "skipped", section=SECTION_COMMANDS)


def test_format_prompt_context_quotes_and_attributes(report, mock_author):
    from cogs.ai import _format_prompt_context

    text = _format_prompt_context(mock_author, "line one\nline two")
    report.record("format starts with quote", True, text.startswith("> line one"), section=SECTION_COMMANDS)
    assert "> line one" in text
    assert "> line two" in text
    assert mock_author.mention in text


def test_format_slash_ask_message(report, mock_author):
    from cogs.ai import _format_slash_ask_message

    text = _format_slash_ask_message(mock_author, "what is juice?", "minutes to midnight")
    expected = f"{mock_author.mention}: what is juice?\n\nminutes to midnight"
    report.record("slash ask format", expected, text, section=SECTION_COMMANDS)
    assert text == expected
    assert len(text) <= 2000


def test_ask_image_option_is_optional(report, ai_cog):
    param = ai_cog.ask.clean_params["image"]
    report.record("image required", False, param.required, section=SECTION_COMMANDS)
    assert param.required is False


async def test_ask_api_error(report, ai_cog, mock_ctx):
    expected = "Something broke on my end, dude. Check the bot logs and try again."
    ai_cog.grok.send_message.side_effect = RuntimeError("API down")

    await ai_cog.ask.callback(ai_cog, mock_ctx, prompt="hello")

    actual = mock_ctx.send.call_args.args[0]
    report.record("error message", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)


async def test_clear_resets_session(report, ai_cog, mock_ctx):
    expected = "Chat history cleared! Starting fresh, dude! 🤙"
    ai_cog.last_response_id = "old-id"
    ai_cog._session_turns = 5

    await ai_cog.clear.callback(ai_cog, mock_ctx)

    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    report.record("last_response_id", None, ai_cog.last_response_id, section=SECTION_COMMANDS)
    report.record("session turns", 0, ai_cog._session_turns, section=SECTION_COMMANDS)

    assert ai_cog.last_response_id is None
    assert ai_cog._session_turns == 0
    mock_ctx.send.assert_awaited_once_with(expected)


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_success(mock_imagine, report, mock_db_ops, ai_cog, mock_ctx):
    mock_imagine.return_value = {
        "status": "success",
        "image_bytes": b"fake-jpeg-bytes",
        "revised_prompt": None,
    }

    await ai_cog.imagine.callback(ai_cog, mock_ctx, prompt="a red circle")

    sent_file = mock_ctx.send.call_args.kwargs.get("file")
    report.record("imagine status", "success", mock_imagine.return_value["status"], section=SECTION_COMMANDS)
    report.record("attachment sent", True, sent_file is not None, section=SECTION_COMMANDS)
    report.record("db write", "write_dalle_entry called", mock_db_ops.write_dalle_entry.called, section=SECTION_COMMANDS)

    mock_db_ops.write_dalle_entry.assert_called_once()
    mock_imagine.assert_called_once_with("a red circle", input_image_urls=None)
    mock_ctx.send.assert_awaited_once()
    assert sent_file is not None


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_with_multiple_input_images(mock_imagine, report, mock_db_ops, ai_cog, mock_ctx):
    urls = [
        "https://cdn.discordapp.com/attachments/1/a.png",
        "https://cdn.discordapp.com/attachments/1/b.png",
    ]
    mock_ctx.message.attachments = [_image_attachment(u) for u in urls]
    mock_imagine.return_value = {
        "status": "success",
        "image_bytes": b"fake-jpeg-bytes",
        "revised_prompt": None,
    }

    prompt = "Put the person from <IMAGE_0> into the scene from <IMAGE_1>"
    await ai_cog.imagine.callback(ai_cog, mock_ctx, prompt=prompt)

    mock_imagine.assert_called_once_with(prompt, input_image_urls=urls)
    embed = mock_ctx.send.call_args.kwargs["embed"]
    input_field = next(f for f in embed.fields if f.name == "Input images")
    report.record("input image count", "2 attached", input_field.value, section=SECTION_COMMANDS)
    assert input_field.value == "2 attached"
    mock_ctx.send.assert_awaited_once()


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_rejects_too_many_images(mock_imagine, report, mock_db_ops, ai_cog, mock_ctx):
    mock_ctx.message.attachments = [
        _image_attachment(f"https://cdn.discordapp.com/attachments/1/{i}.png")
        for i in range(MAX_IMAGINE_INPUT_IMAGES + 1)
    ]

    await ai_cog.imagine.callback(ai_cog, mock_ctx, prompt="combine these")

    expected = f"You can attach at most {MAX_IMAGINE_INPUT_IMAGES} images for `/imagine`."
    actual = mock_ctx.send.call_args.args[0]
    report.record("too many images message", expected, actual, section=SECTION_COMMANDS)
    mock_imagine.assert_not_called()
    mock_ctx.send.assert_awaited_once_with(expected)


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_failure(mock_imagine, report, ai_cog, mock_ctx):
    mock_imagine.return_value = {"status": "error", "error": "rate limited"}

    await ai_cog.imagine.callback(ai_cog, mock_ctx, prompt="a red circle")

    embed = mock_ctx.send.call_args.kwargs["embed"]
    expected_desc = "Failed to generate image. Check the bot logs and try again."
    report.record("embed title", "Error", embed.title, section=SECTION_COMMANDS)
    report.record("embed description", expected_desc, embed.description, section=SECTION_COMMANDS)

    mock_ctx.send.assert_awaited_once()
    assert embed.title == "❌ Error"
    assert embed.description == expected_desc
    assert "rate limited" not in (embed.description or "")


def test_imagine_has_hourly_rate_limit(report, ai_cog):
    buckets = ai_cog.imagine._buckets
    cooldown = buckets._cooldown
    report.record("rate", IMAGINE_RATE_LIMIT, cooldown.rate, section=SECTION_COMMANDS)
    report.record("per seconds", IMAGINE_RATE_PERIOD_SECONDS, cooldown.per, section=SECTION_COMMANDS)
    report.record("bucket type", commands.BucketType.user, buckets.type, section=SECTION_COMMANDS)
    assert cooldown.rate == IMAGINE_RATE_LIMIT
    assert cooldown.per == IMAGINE_RATE_PERIOD_SECONDS
    assert buckets.type == commands.BucketType.user


async def test_imagine_cooldown_error_message(report, mock_ctx):
    expected = (
        "You've hit the `/imagine` limit (30 per hour). "
        "Try again in about 4 minutes."
    )
    bot = DinkBot.__new__(DinkBot)
    error = commands.CommandOnCooldown(
        cooldown=commands.Cooldown(IMAGINE_RATE_LIMIT, IMAGINE_RATE_PERIOD_SECONDS),
        retry_after=240.0,
        type=commands.BucketType.user,
    )
    mock_ctx.send = AsyncMock()

    await DinkBot.on_command_error(bot, mock_ctx, error)

    actual = mock_ctx.send.call_args.args[0]
    report.record("cooldown message", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)


@patch("cogs.ai.requests.post")
@patch("cogs.ai.os.getenv", return_value="test-key")
async def test_voice_success(mock_getenv, mock_post, report, ai_cog, mock_ctx):
    ai_cog.grok.send_message.return_value = ("tts-id", "spoken text")
    mock_response = MagicMock()
    mock_response.content = b"fake-mp3"
    mock_response.raise_for_status = MagicMock()
    mock_post.return_value = mock_response

    await ai_cog.voice.callback(ai_cog, mock_ctx, prompt="say hello")

    audio_file = mock_ctx.send.call_args.kwargs["file"]
    report.record("grok reply", "spoken text", ai_cog.grok.send_message.return_value[1], section=SECTION_COMMANDS)
    report.record("tts request", "POST api.x.ai/v1/tts", mock_post.call_args.args[0], section=SECTION_COMMANDS)
    report.record("audio filename", "voice.mp3", audio_file.filename, section=SECTION_COMMANDS)

    mock_post.assert_called_once()
    mock_ctx.send.assert_awaited_once()
    assert audio_file.filename == "voice.mp3"


@patch("cogs.ai.os.getenv", return_value=None)
async def test_voice_missing_api_key(_mock_getenv, report, ai_cog, mock_ctx):
    expected = "XAI API key not configured."
    await ai_cog.voice.callback(ai_cog, mock_ctx, prompt="hello")

    actual = mock_ctx.send.call_args.args[0]
    report.record("error message", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)


@patch("cogs.ai.requests.post")
@patch("cogs.ai.os.getenv", return_value="test-key")
async def test_voice_tts_failure_hides_exception(mock_getenv, mock_post, report, ai_cog, mock_ctx):
    import requests

    expected = "Something broke generating speech. Check the bot logs and try again."
    ai_cog.grok.send_message.return_value = ("tts-id", "spoken text")
    mock_post.side_effect = requests.exceptions.RequestException("connection reset")

    await ai_cog.voice.callback(ai_cog, mock_ctx, prompt="say hello")

    actual = mock_ctx.send.call_args.args[0]
    report.record("error message", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)
    assert "connection reset" not in actual
