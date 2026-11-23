"""PostgreSQL logging for AI chat and image generation."""

from __future__ import annotations

import json
from typing import Any

from db.pool import DatabasePool, get_pool


class AILogsRepository:
    def __init__(self, pool: DatabasePool | None = None) -> None:
        self._pool = pool

    @property
    def pool(self) -> DatabasePool:
        return self._pool or get_pool()

    def log_chat_interaction(
        self,
        *,
        user_id: int,
        model: str,
        request_messages: list[dict[str, Any]],
        response_content: str,
        input_tokens: int,
        output_tokens: int,
        message_id: int,
        function_calls: list[dict[str, Any]] | None = None,
        image_urls: list[str] | None = None,
    ) -> None:
        """Persist a Grok interaction to the legacy ``chatgpt_logs`` table."""
        total_tokens = input_tokens + output_tokens
        self.pool.execute(
            """
            INSERT INTO chatgpt_logs
                (user_id, model, request_messages, response_content, input_tokens,
                 output_tokens, total_tokens, message_id, function_calls, image_urls)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            """,
            (
                str(user_id),
                model,
                json.dumps(request_messages),
                response_content,
                input_tokens,
                output_tokens,
                total_tokens,
                message_id,
                json.dumps(function_calls) if function_calls else None,
                json.dumps(image_urls) if image_urls else None,
            ),
        )

    def log_image_prompt(self, *, user_id: int, prompt: str, message_id: int) -> None:
        """Persist an /imagine prompt to the legacy ``dalle_3_prompts`` table."""
        self.pool.execute(
            """
            INSERT INTO dalle_3_prompts (user_id, prompt, message_id)
            VALUES (%s, %s, %s)
            """,
            (str(user_id), prompt, message_id),
        )
