"""Tests for unified xAI service: chat+tools loop, Imagine, and async TTS."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from services.xai import (
    GROK_IMAGINE_MODEL,
    MAX_TOOL_ROUNDS,
    GrokChatService,
    generate_image,
    synthesize_speech,
)


def _tool_call(name: str, arguments: dict, call_id: str = "call-1") -> MagicMock:
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


def test_grok_chat_executes_client_tools_skips_server_tools_and_logs():
    client = MagicMock()
    chat = MagicMock()
    client.chat.create.return_value = chat
    logs_repo = MagicMock()

    mixed_resp = MagicMock(
        tool_calls=[
            _tool_call("web_search", {"query": "dink"}),
            _tool_call("get_bot_documentation", {"topic": "dinkcoin"}),
        ],
        content="",
        id="r1",
    )
    final_resp = MagicMock(
        tool_calls=[],
        content="DINK is earned via /1st.",
        id="r2",
        usage=MagicMock(prompt_tokens=10, completion_tokens=5),
    )
    chat.sample.side_effect = [mixed_resp, final_resp]

    svc = GrokChatService(client=client, logs_repo=logs_repo)
    next_id, text = svc.send_message(
        "how does dinkcoin work?",
        system_prompt="sys",
        user_id=123,
        message_id=456,
    )

    assert next_id == "r2"
    assert text == "DINK is earned via /1st."
    assert chat.sample.call_count == 2
    # system + user + assistant(mixed) + 1 client tool_result (web_search skipped)
    assert chat.append.call_count == 4
    logs_repo.log_chat_interaction.assert_called_once()


def test_grok_chat_caps_tool_rounds():
    client = MagicMock()
    chat = MagicMock()
    client.chat.create.return_value = chat
    chat.sample.return_value = MagicMock(
        tool_calls=[_tool_call("list_bot_commands", {})],
        id="loop",
        content="done",
    )

    svc = GrokChatService(client=client, logs_repo=MagicMock())
    svc.send_message("hi", system_prompt="sys")
    assert chat.sample.call_count == MAX_TOOL_ROUNDS + 1


def test_generate_image_routing_and_error_handling():
    client = MagicMock()
    client.image.sample.return_value = MagicMock(image=b"jpeg-bytes")

    res = generate_image("a cat", client=client)
    assert res["status"] == "success"
    assert client.image.sample.call_args.kwargs["model"] == GROK_IMAGINE_MODEL
    assert "image_url" not in client.image.sample.call_args.kwargs

    res_edit = generate_image("add a hat", input_image_url="https://cdn/a.png", client=client)
    assert res_edit["status"] == "success"
    assert client.image.sample.call_args.kwargs["image_url"] == "https://cdn/a.png"

    client.image.sample.side_effect = RuntimeError("api down")
    err = generate_image("a cat", client=client)
    assert err == {"status": "error", "error": "api down"}


async def test_synthesize_speech_posts_async_and_returns_bytes():
    resp = AsyncMock()
    resp.raise_for_status = MagicMock()
    resp.read = AsyncMock(return_value=b"mp3-bytes")
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.post.return_value = cm

    with patch("services.xai.xai_api_key", return_value="test-key"):
        audio = await synthesize_speech(session, "hello world")

    assert audio == b"mp3-bytes"
    session.post.assert_called_once()
    assert session.post.call_args.kwargs["timeout"] is not None
