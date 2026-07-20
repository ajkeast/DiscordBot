"""Plain validation for nightly Discord sentiment scoring (matches message_sentiment)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


POLARITIES = frozenset({"positive", "negative", "neutral", "mixed"})
TOXICITIES = frozenset({"none", "mild", "moderate", "severe"})
DIRECTED_ATS = frozenset({"general", "person", "group", "self", "topic"})
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


@dataclass(frozen=True)
class SentimentResult:
    message_id: str
    polarity: str
    polarity_score: float
    emotions: list[str]
    sarcasm: bool
    toxicity: str
    directed_at: str
    confidence: float
    rationale: str


def _shorten_rationale(value: str) -> str:
    words = value.strip().split()
    if len(words) > 15:
        return " ".join(words[:15])
    return value.strip()


def _clean_emotions(values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError("emotions must be a non-empty list")
    cleaned: list[str] = []
    for emotion in values:
        key = str(emotion).strip().lower()
        if key not in ALLOWED_EMOTIONS:
            raise ValueError(f"emotion '{emotion}' not in {sorted(ALLOWED_EMOTIONS)}")
        if key not in cleaned:
            cleaned.append(key)
    if not cleaned:
        raise ValueError("emotions must contain at least one label")
    return cleaned[:3]


def parse_sentiment_result(raw: Any) -> SentimentResult:
    if not isinstance(raw, dict):
        raise ValueError("result must be an object")

    message_id = str(raw.get("message_id", "")).strip()
    if not message_id:
        raise ValueError("message_id is required")

    polarity = str(raw.get("polarity", "")).strip().lower()
    if polarity not in POLARITIES:
        raise ValueError(f"invalid polarity: {polarity!r}")

    toxicity = str(raw.get("toxicity", "")).strip().lower()
    if toxicity not in TOXICITIES:
        raise ValueError(f"invalid toxicity: {toxicity!r}")

    directed_at = str(raw.get("directed_at", "")).strip().lower()
    if directed_at not in DIRECTED_ATS:
        raise ValueError(f"invalid directed_at: {directed_at!r}")

    polarity_score = float(raw["polarity_score"])
    if polarity_score < -1.0 or polarity_score > 1.0:
        raise ValueError(f"polarity_score out of range: {polarity_score}")

    confidence = float(raw["confidence"])
    if confidence < 0.0 or confidence > 1.0:
        raise ValueError(f"confidence out of range: {confidence}")

    return SentimentResult(
        message_id=message_id,
        polarity=polarity,
        polarity_score=polarity_score,
        emotions=_clean_emotions(raw.get("emotions")),
        sarcasm=bool(raw.get("sarcasm")),
        toxicity=toxicity,
        directed_at=directed_at,
        confidence=confidence,
        rationale=_shorten_rationale(str(raw.get("rationale", ""))),
    )


def parse_sentiment_batch(payload: Any) -> list[SentimentResult]:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("batch response must be an object")
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("results must be a non-empty list")
    return [parse_sentiment_result(item) for item in results]
