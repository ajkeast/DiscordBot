"""Live smoke tests for xAI / Grok API. Requires XAI_API_KEY."""

import os

import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def api_key():
    key = os.getenv("XAI_API_KEY")
    if not key:
        pytest.fail("XAI_API_KEY not set. Add it to .env or GitHub secrets.")
    return key


def test_raw_sdk_chat(api_key):
    from xai_sdk import Client
    from xai_sdk.chat import user
    from chatgpt_functions import DEFAULT_GROK_MODEL

    client = Client(api_key=api_key, timeout=60)
    chat = client.chat.create(model=DEFAULT_GROK_MODEL, store_messages=False)
    chat.append(user("What is 101 times 3? Reply in one short sentence."))

    response = chat.sample()

    assert getattr(response, "id", None)
    assert response.content
    assert "303" in response.content


def test_grok_client_send_message(api_key):
    from chatgpt_functions import GrokClient

    grok = GrokClient(api_key=api_key)
    next_id, text = grok.send_message(
        "What is 2 + 2? One sentence only.",
        system_prompt="You are a helpful assistant. Be very brief.",
        user_id=None,
        message_id=None,
    )

    assert next_id
    assert text
    assert "four" in text.lower() or "4" in text


def test_web_and_x_search(api_key):
    from xai_sdk import Client
    from xai_sdk.chat import user
    from xai_sdk.tools import web_search, x_search
    from chatgpt_functions import DEFAULT_GROK_MODEL

    client = Client(api_key=api_key, timeout=60)
    chat = client.chat.create(
        model=DEFAULT_GROK_MODEL,
        store_messages=False,
        tools=[web_search(), x_search()],
    )
    chat.append(user("What are the latest updates from xAI? Reply in 2-3 short sentences."))

    response = chat.sample()

    assert response.content
    assert len(response.content) > 10


def test_grok_imagine():
    from chatgpt_functions import call_grok_imagine

    if not os.getenv("XAI_API_KEY"):
        pytest.fail("XAI_API_KEY not set. Add it to .env or GitHub secrets.")

    result = call_grok_imagine("A simple red circle on a white background")

    assert result["status"] == "success"
    assert len(result.get("image_bytes", b"")) > 0
