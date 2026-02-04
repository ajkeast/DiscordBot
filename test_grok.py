"""
Local test script for the Grok/xAI API. Run from project root:

    python test_grok.py

Requires XAI_API_KEY in .env. No Discord or DB needed.

Response attributes (what the SDK returns):
  id                  - Response ID; use with previous_response_id for multi-turn.
  content             - The assistant's reply text (what we show the user).
  usage               - Token counts: prompt_tokens, completion_tokens, total_tokens,
                       reasoning_tokens, cached_prompt_text_tokens (reused context).
  role                - "assistant".
  created             - Timestamp.
  finish_reason       - Why generation stopped (e.g. "stop", "length").
  reasoning_content   - Internal "thinking" text (if model supports it; may be empty).
  encrypted_content   - Encrypted reasoning (if requested).
  citations           - Sources used (e.g. for search).
  inline_citations    - Inline references in the reply.
  tool_calls          - Function/tool calls the model requested (if any).
  tool_outputs        - Results from those tool calls.
  server_side_tool_usage - Usage of built-in tools (e.g. search).
  logprobs            - Token probabilities (if requested).
  request_settings    - Echo of request params.
  system_fingerprint  - Model/version identifier.
  debug_output        - Debug info.
  proto               - Raw underlying proto; use for low-level inspection.
  process_chunk       - Internal; used for streaming.
"""
import os
from dotenv import load_dotenv

load_dotenv()

def main():
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        print("ERROR: XAI_API_KEY not set. Add it to .env and try again.")
        return

    from xai_sdk import Client
    from xai_sdk.chat import user, system
    from xai_sdk.tools import web_search, x_search
    from chatgpt_functions import GrokClient, call_grok_imagine, DEFAULT_GROK_MODEL

    print("=" * 60)
    print("1. Raw SDK response (client.chat.create + sample)")
    print("=" * 60)

    client = Client(api_key=api_key, timeout=60)
    chat = client.chat.create(model=DEFAULT_GROK_MODEL, store_messages=False)
    chat.append(user("What is 101 times 3? Reply in one short sentence."))

    response = chat.sample()

    print("Type:", type(response).__name__)
    print("Attributes:", [a for a in dir(response) if not a.startswith("_")])
    print()
    print("response.id     =", getattr(response, "id", "<no id>"))
    print("response.content=", repr((response.content or "")[:200]))
    usage = getattr(response, "usage", None)
    if usage is not None:
        print("response.usage  =", usage)
        # Why so many prompt_tokens? The model injects a large system/context prompt
        # on the server. Most of it is cached (cached_prompt_text_tokens); you're
        # only billed a small amount for the cached part.
        print("  ^ prompt_tokens is high because the model adds internal context;")
        print("    cached_prompt_text_tokens = reused context (cheaper).")
    else:
        print("response.usage  = (not present)")
    proto = getattr(response, "proto", None)
    if proto is not None:
        print("response.proto  = (present, use for raw structure)")
        try:
            if hasattr(proto, "usage"):
                print("  proto.usage:", proto.usage)
        except Exception as e:
            print("  (could not read proto.usage:", e, ")")
    print()

    print("=" * 60)
    print("2. Via GrokClient.send_message (what the bot uses)")
    print("=" * 60)

    grok = GrokClient()
    next_id, text = grok.send_message(
        "What is 2 + 2? One sentence only.",
        system_prompt="You are a helpful assistant. Be very brief.",
        user_id=None,
    )
    print("next_response_id =", next_id)
    print("response_text    =", repr(text[:300]))
    print()
    print("=" * 60)
    print("3. Web search + X search (tools=[web_search(), x_search()])")
    print("=" * 60)

    chat = client.chat.create(
        model=DEFAULT_GROK_MODEL,
        store_messages=False,
        tools=[web_search(), x_search()],
    )
    chat.append(user("What are the latest updates from xAI? Reply in 2-3 short sentences."))

    response = chat.sample()
    print("response.id     =", getattr(response, "id", "<no id>"))
    print("response.content=", repr((response.content or "")[:400]))
    citations = getattr(response, "citations", None)
    if citations:
        print("response.citations (first 5) =", (citations[:5] if citations else []))
    usage = getattr(response, "usage", None)
    if usage:
        print("response.usage   =", usage)
    server_usage = getattr(response, "server_side_tool_usage", None)
    if server_usage is not None:
        print("server_side_tool_usage =", server_usage)
    print()

    print("=" * 60)
    print("4. Grok Imagine (image generation)")
    print("=" * 60)

    result = call_grok_imagine("A simple red circle on a white background")
    print("status       =", result.get("status"))
    if result.get("status") == "success":
        url = result.get("image_url", "")
        print("image_url (full, use this to verify in browser) =")
        print(url)
        print("revised_prompt =", result.get("revised_prompt"))
    else:
        print("error        =", result.get("error"))
    print()

    print("=" * 60)
    print("5. post_tweet tool (GrokClient + mocked _post_tweet, no real tweet)")
    print("=" * 60)

    from unittest.mock import patch

    call_log = []  # record what Grok asked to post

    def fake_post_tweet(text: str, image_urls=None):
        call_log.append({"text": text, "image_urls": image_urls or []})
        return {"status": "success", "tweet_text": text, "tweet_id": "999", "tweet_url": "https://twitter.com/i/status/999", "image_count": len(image_urls or [])}

    # Patch TOOLS_MAP so the real _post_tweet is never called (send_message uses TOOLS_MAP[name])
    with patch.dict("chatgpt_functions.TOOLS_MAP", {"post_tweet": fake_post_tweet}):
        grok = GrokClient()
        next_id, response_text = grok.send_message(
            "Post this to Twitter: test_grok.py check.",
            system_prompt="When the user asks to post to Twitter, use the post_tweet tool with the exact text they give. Then reply in one short sentence.",
            user_id=None,
        )
    print("next_response_id =", next_id)
    print("response_text    =", repr(response_text[:400]))
    if call_log:
        print("post_tweet was called:", call_log[-1])
    else:
        print("post_tweet was not called (Grok may have replied without using the tool).")
    print()

    print("=" * 60)
    print("6. Tweet with image: _post_tweet with real image URL")
    print("=" * 60)

    from chatgpt_functions import _post_tweet

    # Get a real image URL (Grok Imagine) so we test the same path as "generate then post"
    print("Generating image via Grok Imagine...")
    img_result = call_grok_imagine("A single red circle on white background, minimal")
    if img_result.get("status") != "success":
        print("Grok Imagine failed, using a public placeholder image URL for download/upload test.")
        image_url = "https://picsum.photos/400/300"
    else:
        image_url = img_result.get("image_url", "")
        print("Got image URL (first 80 chars):", (image_url[:80] + "..." if len(image_url) > 80 else image_url))

    if not image_url:
        print("No image URL available, skipping tweet-with-image test.")
    else:
        print("Calling _post_tweet(text=..., image_urls=[image_url])...")
        result = _post_tweet("test_grok image upload check", image_urls=[image_url])
        print("Result:", result)
        if result.get("status") == "success":
            print("image_count =", result.get("image_count", 0))
            if result.get("image_count", 0) == 0:
                print("(Tweet posted but image_count is 0 – image upload path may still be failing.)")
        else:
            print("Error:", result.get("error"))
    print()

    print("=" * 60)
    print("7. Does Grok pass image_urls? Post with explicit image URL (mocked post)")
    print("=" * 60)

    call_log2 = []

    def fake_post_with_log(text: str, image_urls=None):
        call_log2.append({"text": text, "image_urls": image_urls or []})
        n = len(image_urls or [])
        return {
            "status": "success",
            "tweet_text": text,
            "tweet_id": "888",
            "tweet_url": "https://twitter.com/i/status/888",
            "image_count": n,
        }

    with patch.dict("chatgpt_functions.TOOLS_MAP", {"post_tweet": fake_post_with_log}):
        grok = GrokClient()
        next_id, response_text = grok.send_message(
            "Post this to Twitter: 'Photo test' and attach this image: https://picsum.photos/200",
            system_prompt=(
                "When the user asks to post to Twitter and provides an image URL, use the post_tweet tool "
                "with both 'text' and 'image_urls' (array containing that URL). Always include image_urls when the user gives a URL."
            ),
            user_id=None,
        )
    print("response_text (excerpt):", repr(response_text[:350]))
    if call_log2:
        last = call_log2[-1]
        print("post_tweet was called with:", last)
        if last.get("image_urls"):
            print("  -> image_urls were passed to the tool.")
        else:
            print("  -> image_urls were NOT passed (model may be omitting them).")
    else:
        print("post_tweet was not called.")
    print()
    print("Done. Check output above to confirm response shape and content.")

if __name__ == "__main__":
    main()
