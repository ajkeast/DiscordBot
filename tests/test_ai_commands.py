"""Mocked tests for AI cog commands."""

from unittest.mock import MagicMock, patch

from cogs.ai import AI
from tests.reporting import SECTION_COMMANDS
from utils.constants import MAX_IMAGINE_INPUT_IMAGES


def _image_attachment(url: str) -> MagicMock:
    attachment = MagicMock()
    attachment.url = url
    attachment.content_type = "image/png"
    return attachment


async def test_ask_success(report, ai_cog, mock_ctx):
    expected = "Grok says hi"
    ai_cog.grok.send_message.return_value = ("new-id", expected)

    await ai_cog.ask.callback(ai_cog, mock_ctx, arg="hello grok")

    actual = mock_ctx.send.call_args.args[0]
    report.record("ctx.send", expected, actual, section=SECTION_COMMANDS)
    report.record("last_response_id", "new-id", ai_cog.last_response_id, section=SECTION_COMMANDS)
    report.record("session turns", 1, ai_cog._session_turns, section=SECTION_COMMANDS)

    ai_cog.grok.send_message.assert_called_once()
    mock_ctx.send.assert_awaited_once_with(expected)
    assert ai_cog.last_response_id == "new-id"
    assert ai_cog._session_turns == 1


async def test_ask_api_error(report, ai_cog, mock_ctx):
    expected = "Something broke on my end, dude. Check the bot logs and try again."
    ai_cog.grok.send_message.side_effect = RuntimeError("API down")

    await ai_cog.ask.callback(ai_cog, mock_ctx, arg="hello")

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

    await ai_cog.imagine.callback(ai_cog, mock_ctx, arg="a red circle")

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
    await ai_cog.imagine.callback(ai_cog, mock_ctx, arg=prompt)

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

    await ai_cog.imagine.callback(ai_cog, mock_ctx, arg="combine these")

    expected = f"You can attach at most {MAX_IMAGINE_INPUT_IMAGES} images for `_imagine`."
    actual = mock_ctx.send.call_args.args[0]
    report.record("too many images message", expected, actual, section=SECTION_COMMANDS)
    mock_imagine.assert_not_called()
    mock_ctx.send.assert_awaited_once_with(expected)


@patch("cogs.ai.call_grok_imagine")
async def test_imagine_failure(mock_imagine, report, ai_cog, mock_ctx):
    mock_imagine.return_value = {"status": "error", "error": "rate limited"}

    await ai_cog.imagine.callback(ai_cog, mock_ctx, arg="a red circle")

    embed = mock_ctx.send.call_args.kwargs["embed"]
    report.record("embed title", "Error", embed.title, section=SECTION_COMMANDS)
    report.record("error detail", "rate limited", mock_imagine.return_value["error"], section=SECTION_COMMANDS)

    mock_ctx.send.assert_awaited_once()
    assert embed.title == "❌ Error"


@patch("cogs.ai.requests.post")
@patch("cogs.ai.os.getenv", return_value="test-key")
async def test_voice_success(mock_getenv, mock_post, report, ai_cog, mock_ctx):
    ai_cog.grok.send_message.return_value = ("tts-id", "spoken text")
    mock_response = MagicMock()
    mock_response.content = b"fake-mp3"
    mock_response.raise_for_status = MagicMock()
    mock_post.return_value = mock_response

    await ai_cog.voice.callback(ai_cog, mock_ctx, text="say hello")

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
    await ai_cog.voice.callback(ai_cog, mock_ctx, text="hello")

    actual = mock_ctx.send.call_args.args[0]
    report.record("error message", expected, actual, section=SECTION_COMMANDS)
    mock_ctx.send.assert_awaited_once_with(expected)
