"""Pydantic schema for message_sentiment rows.

Passed to xAI as JSON Schema via chat.parse. The output engine structurally
enforces enum, numeric minimum/maximum (no bound limit), maxLength up to
2048, and minItems/maxItems up to 256. The upsert still clamps scores and
slices rationale so a value outside those bounds still fits the table.
"""

from typing import Literal

from pydantic import BaseModel, Field

Polarity = Literal["positive", "negative", "neutral", "mixed"]
Toxicity = Literal["none", "mild", "moderate", "severe"]
DirectedAt = Literal["general", "person", "group", "self", "topic"]
Emotion = Literal[
    "joy",
    "anger",
    "annoyance",
    "amusement",
    "sadness",
    "fear",
    "surprise",
    "disgust",
    "neutral",
]


class SentimentResult(BaseModel):
    """One scored message. Categorical fields are closed enums."""

    message_id: str = Field(description="Copy the input message_id exactly")
    polarity: Polarity
    polarity_score: float = Field(
        ge=-1,
        le=1,
        description="-1 very negative, 1 very positive; mixed near 0",
    )
    emotions: list[Emotion] = Field(min_length=1, max_length=3)
    sarcasm: bool
    toxicity: Toxicity
    directed_at: DirectedAt
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=255, description="At most 15 words")


class SentimentBatch(BaseModel):
    """One result per input message."""

    results: list[SentimentResult] = Field(min_length=1)
