"""Live smoke tests for xAI / Grok API. Requires XAI_API_KEY."""

import os

import pytest
from dotenv import load_dotenv

from tests.reporting import SECTION_LIVE_XAI

load_dotenv()

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def api_key():
    key = os.getenv("XAI_API_KEY")
    if not key:
        pytest.fail("XAI_API_KEY not set. Add it to .env or GitHub secrets.")
    return key


def test_raw_sdk_chat(report, api_key):
    from xai_sdk import Client
    from xai_sdk.chat import user
    from chatgpt_functions import DEFAULT_GROK_MODEL

    prompt = "What is 101 times 3? Reply in one short sentence."
    client = Client(api_key=api_key, timeout=60)
    chat = client.chat.create(model=DEFAULT_GROK_MODEL, store_messages=False)
    chat.append(user(prompt))

    response = chat.sample()
    content = response.content or ""

    report.record("prompt", prompt, prompt, section=SECTION_LIVE_XAI)
    report.record("response", "contains 303", content, section=SECTION_LIVE_XAI)
    report.record("response id", "present", getattr(response, "id", None), section=SECTION_LIVE_XAI)
    usage = getattr(response, "usage", None)
    if usage is not None:
        report.record("usage", "present", usage, section=SECTION_LIVE_XAI)

    assert getattr(response, "id", None)
    assert content
    assert "303" in content


def test_grok_client_send_message(report, api_key):
    from chatgpt_functions import GrokClient

    prompt = "What is 2 + 2? One sentence only."
    grok = GrokClient(api_key=api_key)
    next_id, text = grok.send_message(
        prompt,
        system_prompt="You are a helpful assistant. Be very brief.",
        user_id=None,
        message_id=None,
    )

    report.record("prompt", prompt, prompt, section=SECTION_LIVE_XAI)
    report.record("response", "contains four or 4", text, section=SECTION_LIVE_XAI)
    report.record("next_response_id", "present", next_id, section=SECTION_LIVE_XAI)

    assert next_id
    assert text
    assert "four" in text.lower() or "4" in text


def test_web_and_x_search(report, api_key):
    from xai_sdk import Client
    from xai_sdk.chat import user
    from xai_sdk.tools import web_search, x_search
    from chatgpt_functions import DEFAULT_GROK_MODEL

    prompt = "What are the latest updates from xAI? Reply in 2-3 short sentences."
    client = Client(api_key=api_key, timeout=60)
    chat = client.chat.create(
        model=DEFAULT_GROK_MODEL,
        store_messages=False,
        tools=[web_search(), x_search()],
    )
    chat.append(user(prompt))

    response = chat.sample()
    content = response.content or ""

    report.record("prompt", prompt, prompt, section=SECTION_LIVE_XAI)
    report.record("response", "non-empty (>10 chars)", content, section=SECTION_LIVE_XAI)
    citations = getattr(response, "citations", None)
    if citations:
        report.record("citations", "present", citations[:5], section=SECTION_LIVE_XAI)
    usage = getattr(response, "usage", None)
    if usage is not None:
        report.record("usage", "present", usage, section=SECTION_LIVE_XAI)
    server_usage = getattr(response, "server_side_tool_usage", None)
    if server_usage is not None:
        report.record("server_side_tool_usage", "present", server_usage, section=SECTION_LIVE_XAI)

    assert content
    assert len(content) > 10


def test_grok_imagine(report):
    from chatgpt_functions import call_grok_imagine, GROK_IMAGINE_FILENAME

    if not os.getenv("XAI_API_KEY"):
        pytest.fail("XAI_API_KEY not set. Add it to .env or GitHub secrets.")

    prompt = "A simple red circle on a white background"
    result = call_grok_imagine(prompt)
    image_bytes = result.get("image_bytes", b"")

    report.record("prompt", prompt, prompt, section=SECTION_LIVE_XAI)
    report.record("status", "success", result.get("status"), section=SECTION_LIVE_XAI)
    report.record("filename", GROK_IMAGINE_FILENAME, GROK_IMAGINE_FILENAME, section=SECTION_LIVE_XAI)
    report.record("image size", "> 0 bytes", f"{len(image_bytes)} bytes", section=SECTION_LIVE_XAI)
    if result.get("revised_prompt"):
        report.record("revised_prompt", "optional", result["revised_prompt"], section=SECTION_LIVE_XAI)

    assert result["status"] == "success"
    assert len(image_bytes) > 0
