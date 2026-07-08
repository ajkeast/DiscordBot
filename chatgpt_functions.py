"""
Grok Responses API client and Grok Imagine image generation.
Uses xAI's stateful Responses API: sessions are stored on xAI servers (30 days).

Tools available to Grok on every request:
- Server-side (run on xAI): web_search, x_search.
- Client-side (run here): self-knowledge tools from utils/self_knowledge.py,
  so Grok can look up the bot's own docs, command list, and live game/ledger
  data when users ask about the bot itself.

Logging to DB for every interaction.
"""
import json
import logging
import os

from dotenv import load_dotenv
from xai_sdk import Client
from xai_sdk.chat import user, system, image, tool, tool_result
from xai_sdk.tools import web_search, x_search

from utils import self_knowledge

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_GROK_MODEL = "grok-4.3"
GROK_IMAGINE_FILENAME = "grok-imagine.jpg"  # xAI base64 responses are JPEG

# Safety cap on client-side tool round-trips per user message
MAX_TOOL_ROUNDS = 5


class GrokClient:
    """Client for xAI Grok Responses API. Stateful sessions stored on xAI servers.

    Pass a Discord bot instance to enable live self-knowledge tools (command
    list, first-game stats, DINK ledger). Without it, documentation lookups
    still work but live data tools degrade gracefully.
    """

    def __init__(self, api_key=None, bot=None):
        self._client = Client(
            api_key=api_key or os.getenv("XAI_API_KEY"),
            timeout=3600,
        )
        self.model = DEFAULT_GROK_MODEL
        self._tool_handlers = self_knowledge.build_tool_handlers(bot)
        self._client_tools = [
            tool(
                name=schema["name"],
                description=schema["description"],
                parameters=schema["parameters"],
            )
            for schema in self_knowledge.TOOL_SCHEMAS
        ]

    def _build_tools(self) -> list:
        return [web_search(), x_search(), *self._client_tools]

    def _execute_tool_call(self, tool_call) -> str:
        """Run one client-side tool call and return its string result."""
        name = tool_call.function.name
        handler = self._tool_handlers.get(name)
        if handler is None:
            logger.warning("Grok requested unknown tool: %s", name)
            return f"Error: tool '{name}' is not available."
        try:
            args = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        try:
            result = handler(args)
            logger.info("Grok self-knowledge tool used: %s(%s)", name, args)
            return result
        except Exception:
            logger.exception("Self-knowledge tool %s failed", name)
            return f"Error: tool '{name}' failed to execute."

    def _sample_with_tools(self, chat):
        """Sample a response, executing client-side tool calls until Grok answers."""
        response = chat.sample()
        rounds = 0
        while getattr(response, "tool_calls", None) and rounds < MAX_TOOL_ROUNDS:
            chat.append(response)
            for tool_call in response.tool_calls:
                output = self._execute_tool_call(tool_call)
                chat.append(tool_result(output, tool_call_id=tool_call.id))
            response = chat.sample()
            rounds += 1
        return response

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
        - Client-side self-knowledge tool calls are executed transparently before
          the final text is returned.
        - Logs the interaction when user_id and message_id are provided.
        """
        has_images = bool(image_urls)
        store = not has_images

        tools_list = self._build_tools()
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

        response = self._sample_with_tools(chat)
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
    - Returns JPEG bytes (base64 from API) so callers can upload to Discord CDN instead of hotlinking.
    """
    try:
        client = Client(api_key=os.getenv("XAI_API_KEY"))
        sample_kwargs = {
            "model": "grok-imagine-image",
            "prompt": prompt,
            "image_format": "base64",
        }
        if input_image_url:
            sample_kwargs["image_url"] = input_image_url
        response = client.image.sample(**sample_kwargs)
        if not response.image:
            raise ValueError("Grok Imagine response did not include image data")
        return {
            "status": "success",
            "image_bytes": response.image,
            "revised_prompt": None,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
