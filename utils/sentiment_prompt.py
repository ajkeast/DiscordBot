"""Prompt templates for contextual Discord sentiment scoring."""

from __future__ import annotations

from utils.sentiment_schema import ALLOWED_EMOTIONS, POLARITIES

SYSTEM_PROMPT = f"""You are analyzing Discord chat messages for sentiment.

Score ONLY the message marked >>> TARGET.
Use the preceding messages solely as local context (sarcasm, irony, in-jokes, replies).

Return a single JSON object (not an array) with:
- message_id (string, copy exactly from the input)
- polarity: one of {", ".join(sorted(POLARITIES))} — never an emotion label
- polarity_score: float from -1 (very negative) to 1 (very positive); mixed near 0
- emotions: 1–3 labels from this fixed set only: {", ".join(sorted(ALLOWED_EMOTIONS))}
- sarcasm: boolean (true if ironic / sarcastic given context)
- toxicity: none | mild | moderate | severe
- directed_at: general | person | group | self | topic
- confidence: 0–1
- rationale: at most 15 words

Rules:
- polarity and emotions are different fields; do not put emotion names in polarity.
- Short Discord slang (lol, lmao, gg) can still carry clear polarity.
- Prefer sarcasm=true when surface polarity conflicts with context.
- Keep rationales terse; do not quote long message text.
"""


def build_user_prompt(item: dict) -> str:
    """Build the user prompt for one target message.

    Item needs: message_id, channel_name, context_text
    """
    return (
        "Analyze this Discord message. Return one JSON object.\n\n"
        f"message_id: {item['message_id']}\n"
        f"channel: #{item.get('channel_name', 'unknown')}\n"
        f"{item['context_text']}\n"
    )
