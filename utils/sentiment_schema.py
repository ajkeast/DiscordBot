"""Pydantic schema for nightly Discord sentiment scoring (matches message_sentiment)."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Polarity(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class Toxicity(str, Enum):
    NONE = "none"
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


class DirectedAt(str, Enum):
    GENERAL = "general"
    PERSON = "person"
    GROUP = "group"
    SELF = "self"
    TOPIC = "topic"


ALLOWED_EMOTIONS = frozenset(
    {
        "joy",
        "anger",
        "annoyance",
        "amusement",
        "sadness",
        "fear",
        "surprise",
        "disgust",
        "neutral",
    }
)


class SentimentResult(BaseModel):
    message_id: str
    polarity: Polarity
    polarity_score: float = Field(..., ge=-1.0, le=1.0)
    emotions: list[str] = Field(..., min_length=1, max_length=3)
    sarcasm: bool
    toxicity: Toxicity
    directed_at: DirectedAt
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = Field(..., max_length=200)

    @field_validator("emotions")
    @classmethod
    def validate_emotions(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for emotion in values:
            key = emotion.strip().lower()
            if key not in ALLOWED_EMOTIONS:
                raise ValueError(
                    f"emotion '{emotion}' not in {sorted(ALLOWED_EMOTIONS)}"
                )
            if key not in cleaned:
                cleaned.append(key)
        if not cleaned:
            raise ValueError("emotions must contain at least one label")
        return cleaned[:3]

    @field_validator("rationale")
    @classmethod
    def shorten_rationale(cls, value: str) -> str:
        words = value.strip().split()
        if len(words) > 15:
            return " ".join(words[:15])
        return value.strip()

    @field_validator("message_id", mode="before")
    @classmethod
    def coerce_message_id(cls, value: Any) -> str:
        return str(value)


class SentimentBatchResponse(BaseModel):
    results: list[SentimentResult]
