"""
Grok Responses API client and Grok Imagine image generation.
Uses xAI's stateful Responses API: sessions are stored on xAI servers (30 days).
Includes native search (web_search, x_search) and optional post_tweet tool.
Logging to DB for every interaction.
"""
import json
import os
from io import BytesIO

import requests
import tweepy
from dotenv import load_dotenv
from xai_sdk import Client
from xai_sdk.chat import user, system, image, tool, tool_result
from xai_sdk.tools import web_search, x_search

load_dotenv()

DEFAULT_GROK_MODEL = "grok-4-1-fast-non-reasoning"


class GrokClient:
    """Client for xAI Grok Responses API. Stateful sessions stored on xAI servers."""

    def __init__(self, api_key=None):
        self._client = Client(
            api_key=api_key or os.getenv("XAI_API_KEY"),
            timeout=3600,
        )
        self.model = DEFAULT_GROK_MODEL

    def send_message(
        self,
        prompt: str,
        *,
        previous_response_id: str | None = None,
        system_prompt: str | None = None,
        user_id: int | None = None,
        image_urls: list[str] | None = None,
    ) -> tuple[str | None, str]:
        """
        Send a user message to Grok and return (next_response_id, response_text).

        - If previous_response_id is set, the message is appended to that conversation.
        - When image_urls are provided, store_messages is False (per xAI docs) and
          the returned response_id should not be used to continue (next turn starts fresh).
        - Logs the interaction when user_id is provided.
        """
        has_images = bool(image_urls)
        store = not has_images

        tools_list = [web_search(), x_search(), POST_TWEET_TOOL]
        if previous_response_id and store:
            chat = self._client.chat.create(
                model=self.model,
                previous_response_id=previous_response_id,
                store_messages=True,
                tools=tools_list,
                tool_choice="auto",
            )
            chat.append(user(prompt))
        else:
            chat = self._client.chat.create(
                model=self.model,
                store_messages=store,
                tools=tools_list,
                tool_choice="auto",
            )
            if system_prompt:
                chat.append(system(system_prompt))
            if has_images:
                image_parts = [image(image_url=url, detail="high") for url in image_urls]
                chat.append(user(prompt, *image_parts))
            else:
                chat.append(user(prompt))

        response = chat.sample()
        while getattr(response, "tool_calls", None):
            chat.append(response)
            for tc in response.tool_calls:
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", None)
                raw = getattr(fn, "arguments", None) or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                except json.JSONDecodeError:
                    args = {}
                result = TOOLS_MAP[name](**args) if name in TOOLS_MAP else {"status": "error", "error": f"Unknown tool: {name}"}
                chat.append(tool_result(result, tool_call_id=getattr(tc, "id", None)))
            response = chat.sample()

        response_id = getattr(response, "id", None)
        content = (response.content or "").strip() or "Sorry, I couldn't generate a reply this time. Please try again."

        # xAI returns usage with prompt_tokens and completion_tokens (use final response)
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        if user_id is not None:
            self._log_interaction(
                user_id=user_id,
                prompt=prompt,
                response_content=content,
                response_id=response_id,
                image_urls=image_urls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        # When we didn't store (e.g. had images), don't return response_id so caller starts fresh next time
        next_id = response_id if store else None
        return next_id, content[:2000]

    def _log_interaction(
        self,
        user_id: int,
        prompt: str,
        response_content: str,
        response_id: str | None,
        image_urls: list[str] | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Log this turn to the database (same shape as legacy ChatGPT logs)."""
        from utils.db import db_ops

        request_messages = [{"role": "user", "content": prompt}]
        db_ops.log_chatgpt_interaction(
            user_id=user_id,
            model=self.model,
            request_messages=request_messages,
            response_content=response_content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            function_calls=None,
            image_urls=image_urls,
        )


def _post_tweet(text: str, image_urls: list[str] | None = None) -> dict:
    """Post a tweet (text + optional image URLs). Uses tweepy v2 for create_tweet, v1.1 for media upload."""
    text = (text or "").strip()[:280]
    if not text:
        return {"status": "error", "error": "Tweet text is required and cannot be empty."}
    tw = {k: os.getenv(k) for k in ("TWITTER_API_KEY", "TWITTER_API_KEY_SECRET", "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN_SECRET")}
    if not all(tw.values()):
        return {"status": "error", "error": "Twitter API credentials not configured."}
    urls = (image_urls or [])[:4]
    try:
        client = tweepy.Client(
            consumer_key=tw["TWITTER_API_KEY"],
            consumer_secret=tw["TWITTER_API_KEY_SECRET"],
            access_token=tw["TWITTER_ACCESS_TOKEN"],
            access_token_secret=tw["TWITTER_ACCESS_TOKEN_SECRET"],
        )
        media_ids = []
        if urls:
            api_v1 = tweepy.API(tweepy.OAuth1UserHandler(*tw.values()))
            for url in urls:
                try:
                    r = requests.get(url, timeout=15)
                    r.raise_for_status()
                    if r.content:
                        media_ids.append(api_v1.media_upload(filename="image.png", file=BytesIO(r.content)).media_id)
                except Exception as e:
                    return {"status": "error", "error": f"Image upload failed: {e}"}
        kwargs = {"text": text}
        if media_ids:
            kwargs["media_ids"] = media_ids
        tweet = client.create_tweet(**kwargs)
        tid = tweet.data["id"]
        return {"status": "success", "tweet_text": text, "tweet_id": tid, "tweet_url": f"https://twitter.com/i/status/{tid}", "image_count": len(media_ids)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


POST_TWEET_TOOL = tool(
    name="post_tweet",
    description="Post a tweet to Twitter/X when the user asks to post or share there. Can attach images via URLs (e.g. from image generation).",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Tweet text (max 280 chars)."},
            "image_urls": {"type": "array", "items": {"type": "string"}, "description": "Optional image URLs (max 4)."},
        },
        "required": ["text"],
    },
)
TOOLS_MAP = {"post_tweet": _post_tweet}


def call_grok_imagine(prompt: str, input_image_url: str | None = None) -> dict:
    """Generate or edit an image using xAI Grok Imagine API (xAI SDK).

    - prompt: Text description for generation, or edit instructions when input_image_url is set.
    - input_image_url: Optional URL of an image to edit (e.g. Discord CDN URL). Pass as-is; no base64.
    """
    try:
        client = Client(api_key=os.getenv("XAI_API_KEY"))
        if input_image_url:
            response = client.image.sample(
                model="grok-imagine-image",
                image_url=input_image_url,
                prompt=prompt,
                image_format="url",
            )
        else:
            response = client.image.sample(
                model="grok-imagine-image",
                prompt=prompt,
                image_format="url",
            )
        return {
            "status": "success",
            "image_url": response.url,
            "revised_prompt": None,  # Grok Imagine does not return a revised prompt
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
