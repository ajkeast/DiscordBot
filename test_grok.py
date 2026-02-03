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
    print("Done. Check output above to confirm response shape and content.")

if __name__ == "__main__":
    main()
