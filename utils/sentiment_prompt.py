"""Prompt templates for contextual Discord sentiment scoring."""

from __future__ import annotations

SYSTEM_PROMPT = """You are analyzing Discord chat messages for sentiment.

Score ONLY the message marked >>> TARGET.
Use the preceding messages solely as local context (sarcasm, irony, in-jokes, replies).

Copy each message_id exactly.
polarity_score runs from -1 (very negative) to 1 (very positive); mixed is near 0.
Set sarcasm true when the surface tone conflicts with the context.
Short Discord slang (lol, lmao, gg) can still carry clear polarity.
Keep each rationale to at most 15 words and do not quote the message.
Return one result per input item.
"""


def build_batch_user_prompt(items: list[dict]) -> str:
    """Build the user prompt for a batch of targets.

    Each item needs message_id, channel_name, and context_text.
    """
    blocks = [f"Score these {len(items)} Discord message(s).\n"]
    for idx, item in enumerate(items, start=1):
        blocks.append(
            f"--- ITEM {idx} ---\n"
            f"message_id: {item['message_id']}\n"
            f"channel: #{item.get('channel_name', 'unknown')}\n"
            f"{item['context_text']}\n"
        )
    return "\n".join(blocks)
