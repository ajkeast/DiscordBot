"""Mocked tests for AI cog commands."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from bot import DinkBot
from cogs.ai import ImagineEditModal, ImagineResultView, _CooldownMessage
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


def _view_button(view, custom_id: str):
    return next(item for item in view.children if item.custom_id == custom_id)


def _completed_imagine_kwargs(mock_send):
    """Kwargs used to replace the 'is imagining…' placeholder with the result."""
    return mock_send.return_value.edit.call_args.kwargs


def _interaction_for_imagine(mock_author, *, embeds, send_return=None):
    placeholder = AsyncMock()
    interaction = AsyncMock()
    interaction.user = mock_author
    interaction.client = MagicMock()
    interaction.client.user = MagicMock()
    interaction.client.user.display_name = "Peter Dinklage"
    interaction.client.get_cog.return_value = None
    interaction.me = None
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock(return_value=send_return)
    interaction.original_response = AsyncMock(return_value=placeholder)
    interaction.message = MagicMock()
    interaction.message.embeds = embeds
    interaction.message.attachments = []
    return interaction, placeholder


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

    status = mock_ctx.send.call_args.args[0]
    kwargs = _completed_imagine_kwargs(mock_ctx.send)
    sent_file = kwargs.get("attachments", [None])[0]
    view = kwargs.get("view")
    embed = kwargs["embed"]
    report.record("imagine status", "success", mock_imagine.return_value["status"], section=SECTION_COMMANDS)
    report.record("imagining text", True, "is imagining" in status, section=SECTION_COMMANDS)
    report.record("attachment sent", True, sent_file is not None, section=SECTION_COMMANDS)
    report.record("edit view", True, isinstance(view, ImagineResultView), section=SECTION_COMMANDS)
    report.record("db write", "write_dalle_entry called", mock_db_ops.write_dalle_entry.called, section=SECTION_COMMANDS)

    mock_db_ops.write_dalle_entry.assert_called_once()
    mock_imagine.assert_called_once_with("a red circle", input_image_url=None)
    mock_ctx.send.assert_awaited_once()
    mock_ctx.send.return_value.edit.assert_awaited_once()
    assert "is imagining" in status
    assert sent_file is not None
    assert isinstance(view, ImagineResultView)
    assert view.timeout is None
    report.record("embed title", None, embed.title, section=SECTION_COMMANDS)
    assert embed.title is None
    assert embed.url is None


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_with_one_input_image(mock_imagine, report, mock_db_ops, ai_cog, mock_ctx):
    url = "https://cdn.discordapp.com/attachments/1/a.png"
    mock_ctx.message.attachments = [_image_attachment(url)]
    mock_imagine.return_value = {
        "status": "success",
        "image_bytes": b"fake-jpeg-bytes",
        "revised_prompt": None,
    }

    await ai_cog.imagine.callback(ai_cog, mock_ctx, prompt="make it night")

    mock_imagine.assert_called_once_with("make it night", input_image_url=url)
    embed = _completed_imagine_kwargs(mock_ctx.send)["embed"]
    field_names = [f.name for f in embed.fields]
    report.record("input image field", False, "Input image" in field_names, section=SECTION_COMMANDS)
    report.record("stored source url", url, embed.url, section=SECTION_COMMANDS)
    assert "Input image" not in field_names
    assert embed.title is None
    assert embed.url == url
    mock_ctx.send.assert_awaited_once()


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_uses_replied_image(mock_imagine, report, mock_db_ops, ai_cog, mock_ctx):
    url = "https://cdn.discordapp.com/attachments/1/replied.png"
    resolved = MagicMock(spec=discord.Message)
    resolved.attachments = [_image_attachment(url)]
    resolved.embeds = []
    mock_ctx.message.reference = MagicMock()
    mock_ctx.message.reference.resolved = resolved
    mock_imagine.return_value = {
        "status": "success",
        "image_bytes": b"fake-jpeg-bytes",
        "revised_prompt": None,
    }

    await ai_cog.imagine.callback(ai_cog, mock_ctx, prompt="add a hat")

    mock_imagine.assert_called_once_with("add a hat", input_image_url=url)
    report.record("reply image url", url, mock_imagine.call_args.kwargs["input_image_url"], section=SECTION_COMMANDS)


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_rejects_too_many_images(mock_imagine, report, mock_db_ops, ai_cog, mock_ctx):
    mock_ctx.message.attachments = [
        _image_attachment(f"https://cdn.discordapp.com/attachments/1/{i}.png")
        for i in range(MAX_IMAGINE_INPUT_IMAGES + 1)
    ]

    await ai_cog.imagine.callback(ai_cog, mock_ctx, prompt="combine these")

    expected = f"You can attach at most {MAX_IMAGINE_INPUT_IMAGES} image for `/imagine`."
    actual = mock_ctx.send.call_args.args[0]
    report.record("too many images message", expected, actual, section=SECTION_COMMANDS)
    mock_imagine.assert_not_called()
    mock_ctx.send.assert_awaited_once_with(expected)


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_failure(mock_imagine, report, ai_cog, mock_ctx):
    mock_imagine.return_value = {"status": "error", "error": "rate limited"}

    await ai_cog.imagine.callback(ai_cog, mock_ctx, prompt="a red circle")

    embed = _completed_imagine_kwargs(mock_ctx.send)["embed"]
    expected_desc = "Failed to generate image. Check the bot logs and try again."
    report.record("embed title", "Error", embed.title, section=SECTION_COMMANDS)
    report.record("embed description", expected_desc, embed.description, section=SECTION_COMMANDS)
    report.record("imagining then error", True, "is imagining" in mock_ctx.send.call_args.args[0], section=SECTION_COMMANDS)

    mock_ctx.send.assert_awaited_once()
    mock_ctx.send.return_value.edit.assert_awaited_once()
    assert embed.title == "❌ Error"
    assert embed.description == expected_desc
    assert "rate limited" not in (embed.description or "")


def test_imagine_image_option_is_optional(report, ai_cog):
    param = ai_cog.imagine.clean_params["image"]
    report.record("image required", False, param.required, section=SECTION_COMMANDS)
    assert param.required is False
    assert "image1" not in ai_cog.imagine.clean_params
    assert "image2" not in ai_cog.imagine.clean_params
    assert "image3" not in ai_cog.imagine.clean_params


def test_imagine_result_view_is_persistent(report):
    view = ImagineResultView()
    edit = _view_button(view, "imagine:edit")
    retry = _view_button(view, "imagine:retry")
    report.record("timeout", None, view.timeout, section=SECTION_COMMANDS)
    report.record("edit custom_id", "imagine:edit", edit.custom_id, section=SECTION_COMMANDS)
    report.record("retry custom_id", "imagine:retry", retry.custom_id, section=SECTION_COMMANDS)
    assert view.timeout is None
    assert edit.label == "Edit"
    assert retry.label == "Retry"
    assert len(view.children) == 2


async def test_imagine_edit_button_opens_modal(report):
    view = ImagineResultView()
    interaction = AsyncMock()
    interaction.message = MagicMock()
    interaction.message.attachments = [
        _image_attachment("https://cdn.discordapp.com/attachments/1/out.jpg")
    ]
    interaction.message.embeds = []
    interaction.response.send_modal = AsyncMock()

    await _view_button(view, "imagine:edit").callback(interaction)

    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.call_args.args[0]
    report.record("modal type", "ImagineEditModal", type(modal).__name__, section=SECTION_COMMANDS)
    assert isinstance(modal, ImagineEditModal)
    assert modal.image_url == "https://cdn.discordapp.com/attachments/1/out.jpg"


async def test_imagine_edit_button_without_image(report):
    view = ImagineResultView()
    interaction = AsyncMock()
    interaction.message = MagicMock()
    interaction.message.attachments = []
    interaction.message.embeds = []
    interaction.response.send_message = AsyncMock()

    await _view_button(view, "imagine:edit").callback(interaction)

    actual = interaction.response.send_message.call_args.args[0]
    expected = "Couldn't find an image on that message to edit."
    report.record("missing image message", expected, actual, section=SECTION_COMMANDS)
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_retry_regenerates_prompt(mock_imagine, report, mock_author):
    mock_imagine.return_value = {
        "status": "success",
        "image_bytes": b"fake-jpeg-bytes",
        "revised_prompt": None,
    }
    prompt_field = MagicMock()
    prompt_field.name = "Prompt"
    prompt_field.value = "a red circle"
    embed = MagicMock()
    embed.fields = [prompt_field]
    embed.url = None

    view = ImagineResultView()
    interaction, placeholder = _interaction_for_imagine(mock_author, embeds=[embed])

    await _view_button(view, "imagine:retry").callback(interaction)

    mock_imagine.assert_called_once_with("a red circle", input_image_url=None)
    interaction.response.send_message.assert_awaited_once()
    status = interaction.response.send_message.call_args.args[0]
    report.record("retry imagining", True, "is imagining" in status, section=SECTION_COMMANDS)
    assert "is imagining" in status
    placeholder.edit.assert_awaited_once()
    result_embed = placeholder.edit.call_args.kwargs["embed"]
    report.record("retry title", None, result_embed.title, section=SECTION_COMMANDS)
    assert result_embed.title is None


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_retry_reuses_source_image(mock_imagine, report, mock_author):
    mock_imagine.return_value = {
        "status": "success",
        "image_bytes": b"fake-jpeg-bytes",
        "revised_prompt": None,
    }
    source = "https://cdn.discordapp.com/attachments/1/source.png"
    prompt_field = MagicMock()
    prompt_field.name = "Prompt"
    prompt_field.value = "make it night"
    embed = MagicMock()
    embed.fields = [prompt_field]
    embed.url = source

    view = ImagineResultView()
    interaction, placeholder = _interaction_for_imagine(mock_author, embeds=[embed])

    await _view_button(view, "imagine:retry").callback(interaction)

    mock_imagine.assert_called_once_with("make it night", input_image_url=source)
    report.record("retry source url", source, mock_imagine.call_args.kwargs["input_image_url"], section=SECTION_COMMANDS)
    result_embed = placeholder.edit.call_args.kwargs["embed"]
    assert result_embed.url == source


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_retry_edits_after_callback_response(mock_imagine, report, mock_author):
    """discord.py 2.5+ send_message returns InteractionCallbackResponse, not a Message."""
    mock_imagine.return_value = {
        "status": "success",
        "image_bytes": b"fake-jpeg-bytes",
        "revised_prompt": None,
    }
    prompt_field = MagicMock()
    prompt_field.name = "Prompt"
    prompt_field.value = "a red circle"
    embed = MagicMock()
    embed.fields = [prompt_field]
    embed.url = None

    class CallbackResponse:
        pass

    view = ImagineResultView()
    interaction, placeholder = _interaction_for_imagine(
        mock_author, embeds=[embed], send_return=CallbackResponse()
    )

    await _view_button(view, "imagine:retry").callback(interaction)

    interaction.original_response.assert_awaited_once()
    placeholder.edit.assert_awaited_once()
    report.record("callback response used original_response", True, True, section=SECTION_COMMANDS)


async def test_imagine_retry_without_prompt(report):
    view = ImagineResultView()
    interaction = AsyncMock()
    interaction.message = MagicMock()
    interaction.message.embeds = []
    interaction.response.send_message = AsyncMock()

    await _view_button(view, "imagine:retry").callback(interaction)

    actual = interaction.response.send_message.call_args.args[0]
    expected = "Couldn't find the original prompt to retry."
    report.record("missing prompt message", expected, actual, section=SECTION_COMMANDS)
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_edit_modal_submits(mock_imagine, report, mock_author):
    mock_imagine.return_value = {
        "status": "success",
        "image_bytes": b"fake-jpeg-bytes",
        "revised_prompt": None,
    }
    modal = ImagineEditModal(image_url="https://cdn.example.com/a.png")
    modal.prompt_input._value = "make it night"

    interaction, placeholder = _interaction_for_imagine(mock_author, embeds=[])

    await modal.on_submit(interaction)

    mock_imagine.assert_called_once_with(
        "make it night",
        input_image_url="https://cdn.example.com/a.png",
    )
    status = interaction.response.send_message.call_args.args[0]
    report.record("edit imagining", True, "is imagining" in status, section=SECTION_COMMANDS)
    assert "is imagining" in status
    placeholder.edit.assert_awaited_once()
    view = placeholder.edit.call_args.kwargs.get("view")
    embed = placeholder.edit.call_args.kwargs["embed"]
    report.record("followup has edit view", True, isinstance(view, ImagineResultView), section=SECTION_COMMANDS)
    report.record("followup title", None, embed.title, section=SECTION_COMMANDS)
    assert isinstance(view, ImagineResultView)
    assert embed.title is None
    assert embed.url == "https://cdn.example.com/a.png"
    assert "Input image" not in [f.name for f in embed.fields]


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_edit_modal_on_cooldown(mock_imagine, report, ai_cog, mock_author):
    buckets = ai_cog.imagine._buckets
    for _ in range(IMAGINE_RATE_LIMIT):
        retry = buckets.update_rate_limit(_CooldownMessage(mock_author))
        assert retry is None

    modal = ImagineEditModal(image_url="https://cdn.example.com/a.png")
    modal.prompt_input._value = "make it night"
    interaction = AsyncMock()
    interaction.user = mock_author
    interaction.client = MagicMock()
    interaction.client.get_cog.return_value = ai_cog
    interaction.response = AsyncMock()

    await modal.on_submit(interaction)

    mock_imagine.assert_not_called()
    actual = interaction.response.send_message.call_args.args[0]
    report.record("cooldown blocks edit", True, "limit" in actual, section=SECTION_COMMANDS)
    assert "limit" in actual
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True
    interaction.response.defer.assert_not_called()


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_slash_image_option(mock_imagine, report, mock_db_ops, ai_cog, mock_ctx):
    url = "https://cdn.discordapp.com/attachments/1/slash.png"
    mock_imagine.return_value = {
        "status": "success",
        "image_bytes": b"fake-jpeg-bytes",
        "revised_prompt": None,
    }

    await ai_cog.imagine.callback(
        ai_cog, mock_ctx, prompt="make it a sketch", image=_image_attachment(url)
    )

    mock_imagine.assert_called_once_with("make it a sketch", input_image_url=url)
    report.record("slash image url", url, mock_imagine.call_args.kwargs["input_image_url"], section=SECTION_COMMANDS)


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
    # Prefix: audio only (no quoted prompt / reply chain).
    assert not mock_ctx.send.call_args.args


@patch("cogs.ai.requests.post")
@patch("cogs.ai.os.getenv", return_value="test-key")
async def test_voice_slash_single_message_with_prompt(
    mock_getenv, mock_post, report, mock_db_ops, ai_cog, mock_ctx
):
    from datetime import datetime, timezone

    mock_ctx.interaction = MagicMock()
    mock_ctx.message.created_at = datetime(2024, 6, 11, tzinfo=timezone.utc)
    mock_ctx.send = AsyncMock()
    ai_cog.grok.send_message.return_value = ("tts-id", "spoken text")
    mock_response = MagicMock()
    mock_response.content = b"fake-mp3"
    mock_response.raise_for_status = MagicMock()
    mock_post.return_value = mock_response

    await ai_cog.voice.callback(ai_cog, mock_ctx, prompt="say hello")

    expected = f"{mock_ctx.author.mention}: say hello\n\nspoken text"
    actual = mock_ctx.send.call_args.args[0]
    audio_file = mock_ctx.send.call_args.kwargs["file"]
    report.record("slash voice message", expected, actual, section=SECTION_COMMANDS)
    report.record("single send", 1, mock_ctx.send.await_count, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected, file=audio_file)
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


@patch("cogs.ai.requests.post")
@patch("cogs.ai.os.getenv", return_value="test-key")
async def test_voice_slash_error_single_message(mock_getenv, mock_post, report, mock_db_ops, ai_cog, mock_ctx):
    import requests
    from datetime import datetime, timezone

    mock_ctx.interaction = MagicMock()
    mock_ctx.message.created_at = datetime(2024, 6, 11, tzinfo=timezone.utc)
    mock_ctx.send = AsyncMock()
    ai_cog.grok.send_message.return_value = ("tts-id", "spoken text")
    mock_post.side_effect = requests.exceptions.RequestException("connection reset")

    await ai_cog.voice.callback(ai_cog, mock_ctx, prompt="say hello")

    expected = (
        f"{mock_ctx.author.mention}: say hello\n\n"
        "Something broke generating speech. Check the bot logs and try again."
    )
    actual = mock_ctx.send.call_args.args[0]
    report.record("slash voice error", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)
