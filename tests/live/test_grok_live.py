"""Live smoke tests for xAI Grok API. Requires XAI_API_KEY."""

from __future__ import annotations

import pytest
from xai_sdk.chat import user
from xai_sdk.tools import web_search, x_search

from config import DEFAULT_GROK_MODEL, GROK_IMAGINE_FILENAME, xai_api_key
from services.xai import GrokChatService, create_xai_client, generate_image
from tests.reporting import SECTION_LIVE_XAI

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def api_key() -> str:
    key = xai_api_key()
    if not key:
        pytest.fail("XAI_API_KEY not set. Add it to .env or GitHub secrets.")
    return key


def test_raw_sdk_chat(report, api_key):
    prompt = "What is 101 times 3? Reply in one short sentence."
    client = create_xai_client(api_key, timeout=60)
    chat = client.chat.create(model=DEFAULT_GROK_MODEL, store_messages=False)
    chat.append(user(prompt))

    response = chat.sample()
    content = response.content or ""
    report.record("response", "contains 303", content, section=SECTION_LIVE_XAI)
    assert getattr(response, "id", None)
    assert "303" in content


def test_grok_chat_service_send_message(report, api_key):
    svc = GrokChatService(api_key=api_key)
    next_id, text = svc.send_message(
        "What is 2 + 2? One sentence only.",
        system_prompt="You are a helpful assistant. Be very brief.",
    )
    report.record("response", "contains four or 4", text, section=SECTION_LIVE_XAI)
    assert next_id
    assert "four" in text.lower() or "4" in text


def test_web_and_x_search(report, api_key):
    client = create_xai_client(api_key, timeout=60)
    chat = client.chat.create(
        model=DEFAULT_GROK_MODEL,
        store_messages=False,
        tools=[web_search(), x_search()],
    )
    chat.append(user("What are the latest updates from xAI? Reply in 2-3 short sentences."))
    response = chat.sample()
    content = response.content or ""
    report.record("response", "non-empty (>10 chars)", content, section=SECTION_LIVE_XAI)
    assert len(content) > 10


def test_grok_imagine(report, api_key):
    result = generate_image("A simple red circle on a white background")
    image_bytes = result.get("image_bytes", b"")
    report.record("filename", GROK_IMAGINE_FILENAME, GROK_IMAGINE_FILENAME, section=SECTION_LIVE_XAI)
    report.record("image size", "> 0 bytes", f"{len(image_bytes)} bytes", section=SECTION_LIVE_XAI)
    assert result["status"] == "success"
    assert len(image_bytes) > 0
