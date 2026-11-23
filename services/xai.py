"""Unified xAI Grok client factory, chat+tools, Imagine, and async TTS."""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
from xai_sdk import Client
from xai_sdk.chat import image, system, tool, tool_result, user
from xai_sdk.tools import get_tool_call_type, web_search, x_search

from config import (
    DEFAULT_GROK_MODEL,
    GROK_IMAGINE_FILENAME,
    GROK_IMAGINE_MODEL,
    xai_api_key,
)
from db.ai_logs import AILogsRepository
from services import self_knowledge

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5
TTS_URL = "https://api.x.ai/v1/tts"
DEFAULT_TTS_TIMEOUT_SECONDS = 60.0

# Server-side tool function names (already executed by xAI; observability only).
_SERVER_SIDE_TOOL_NAMES = frozenset(
    {
        "web_search",
        "web_search_with_snippets",
        "browse_page",
        "open_page",
        "open_page_with_find",
        "search_images",
        "x_user_search",
        "x_keyword_search",
        "x_semantic_search",
        "x_thread_fetch",
        "code_execution",
        "view_x_video",
        "view_image",
        "collections_search",
    }
)


def create_xai_client(
    api_key: str | None = None,
    *,
    timeout: int = 120,
) -> Client:
    """Create a configured xAI SDK client using the shared API key lookup."""
    key = (api_key if api_key is not None else xai_api_key()).strip()
    if not key:
        raise RuntimeError("XAI_API_KEY is not configured")
    return Client(api_key=key, timeout=timeout)


class GrokChatService:
    """Stateful xAI Grok Responses API chat client with self-knowledge tools."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        bot=None,
        model: str = DEFAULT_GROK_MODEL,
        logs_repo: AILogsRepository | None = None,
        client: Client | None = None,
    ) -> None:
        self._client = client or Client(
            api_key=(api_key if api_key is not None else xai_api_key()) or None,
            timeout=300,
        )
        self.model = model
        self._logs_repo = logs_repo
        self._tool_handlers = self_knowledge.build_tool_handlers(bot)
        self._client_tools = [
            tool(
                name=schema["name"],
                description=schema["description"],
                parameters=schema["parameters"],
            )
            for schema in self_knowledge.TOOL_SCHEMAS
        ]

    @property
    def logs_repo(self) -> AILogsRepository:
        if self._logs_repo is None:
            self._logs_repo = AILogsRepository()
        return self._logs_repo

    def _build_tools(self) -> list[Any]:
        return [web_search(), x_search(), *self._client_tools]

    def _is_client_side_tool_call(self, tool_call) -> bool:
        """Return True only for local tools; xAI already ran server-side tools."""
        name = getattr(getattr(tool_call, "function", None), "name", "") or ""
        if name in _SERVER_SIDE_TOOL_NAMES:
            return False
        try:
            return get_tool_call_type(tool_call) == "client_side_tool"
        except Exception:
            return True

    def _client_side_tool_calls(self, response) -> list[Any]:
        calls = getattr(response, "tool_calls", None) or []
        client_calls: list[Any] = []
        for tool_call in calls:
            if self._is_client_side_tool_call(tool_call):
                client_calls.append(tool_call)
            else:
                name = getattr(getattr(tool_call, "function", None), "name", "?")
                logger.info("Grok server-side tool used: %s", name)
        return client_calls

    def _execute_tool_call(self, tool_call) -> str:
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
        response = chat.sample()
        rounds = 0
        while rounds < MAX_TOOL_ROUNDS:
            client_calls = self._client_side_tool_calls(response)
            if not client_calls:
                break
            chat.append(response)
            for tool_call in client_calls:
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
        """Send one turn to Grok and return ``(next_response_id, response_text)``."""
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
                image_parts = [
                    image(image_url=url, detail="high") for url in image_urls or ()
                ]
                chat.append(user(prompt, *image_parts))
            else:
                chat.append(user(prompt))

        response = self._sample_with_tools(chat)
        server_usage = getattr(response, "server_side_tool_usage", None)
        if server_usage:
            logger.info("Grok server-side tools used: %s", server_usage)

        response_id = getattr(response, "id", None)
        content = (
            (response.content or "").strip()
            or "Sorry, I couldn't generate a reply this time. Please try again."
        )

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        if user_id is not None and message_id is not None:
            self.logs_repo.log_chat_interaction(
                user_id=user_id,
                model=self.model,
                request_messages=[{"role": "user", "content": prompt}],
                response_content=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                message_id=message_id,
                function_calls=None,
                image_urls=image_urls,
            )

        next_id = response_id if store else None
        return next_id, content[:2000]


def generate_image(
    prompt: str,
    input_image_url: str | None = None,
    *,
    client: Client | None = None,
) -> dict[str, Any]:
    """Generate or edit an image using xAI Grok Imagine."""
    try:
        xai = client or create_xai_client(timeout=120)
        sample_kwargs: dict[str, Any] = {
            "model": GROK_IMAGINE_MODEL,
            "prompt": prompt,
            "image_format": "base64",
        }
        if input_image_url:
            sample_kwargs["image_url"] = input_image_url
        response = xai.image.sample(**sample_kwargs)
        if not response.image:
            raise ValueError("Grok Imagine response did not include image data")
        return {
            "status": "success",
            "image_bytes": response.image,
            "revised_prompt": None,
        }
    except Exception as exc:
        logger.exception("Grok Imagine failed")
        return {"status": "error", "error": str(exc)}


async def synthesize_speech(
    session: aiohttp.ClientSession,
    text: str,
    *,
    api_key: str | None = None,
    voice_id: str = "leo",
    language: str = "en",
    timeout_seconds: float = DEFAULT_TTS_TIMEOUT_SECONDS,
) -> bytes:
    """Synthesize speech audio via xAI's TTS endpoint without blocking the loop."""
    key = (api_key if api_key is not None else xai_api_key()).strip()
    if not key:
        raise RuntimeError("XAI_API_KEY is not configured")

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with session.post(
        TTS_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "voice_id": voice_id,
            "language": language,
        },
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        return await resp.read()


__all__ = [
    "DEFAULT_GROK_MODEL",
    "GROK_IMAGINE_FILENAME",
    "GROK_IMAGINE_MODEL",
    "MAX_TOOL_ROUNDS",
    "GrokChatService",
    "create_xai_client",
    "generate_image",
    "synthesize_speech",
]
