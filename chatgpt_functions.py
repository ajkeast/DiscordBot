"""
Grok Responses API client and Grok Imagine image generation.
Uses xAI's stateful Responses API: sessions are stored on xAI servers (30 days).
Includes native search (web_search, x_search).
Logging to DB for every interaction.
"""
import logging
import os

from dotenv import load_dotenv
from xai_sdk import Client
from xai_sdk.chat import user, system, image
from xai_sdk.tools import web_search, x_search

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_GROK_MODEL = "grok-4.3"


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
        message_id: int | None = None,
        image_urls: list[str] | None = None,
    ) -> tuple[str | None, str]:
        """
        Send a user message to Grok and return (next_response_id, response_text).

        - If previous_response_id is set, the message is appended to that conversation.
        - When image_urls are provided, store_messages is False (per xAI docs) and
          the returned response_id should not be used to continue (next turn starts fresh).
        - Logs the interaction when user_id and message_id are provided.
        """
        has_images = bool(image_urls)
        store = not has_images

        tools_list = [web_search(), x_search()]
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
        server_usage = getattr(response, "server_side_tool_usage", None)
        if server_usage:
            logger.info("Grok server-side tools used: %s", server_usage)

        response_id = getattr(response, "id", None)
        content = (response.content or "").strip() or "Sorry, I couldn't generate a reply this time. Please try again."

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        if user_id is not None and message_id is not None:
            self._log_interaction(
                user_id=user_id,
                prompt=prompt,
                response_content=content,
                response_id=response_id,
                message_id=message_id,
                image_urls=image_urls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        next_id = response_id if store else None
        return next_id, content[:2000]

    def _log_interaction(
        self,
        user_id: int,
        prompt: str,
        response_content: str,
        response_id: str | None,
        message_id: int,
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
            message_id=message_id,
            function_calls=None,
            image_urls=image_urls,
        )


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
            "revised_prompt": None,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
