"""PostgreSQL persistence for incremental message sentiment scoring."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from db.pool import DatabasePool, get_pool

# Single-query LATERAL join: fetches newest unscored messages alongside up to
# `context_size` chronological prior in-channel messages in one round-trip.
# Intentionally reads original messages.content (not edited_content).
UNSCORED_WITH_PRIORS_SQL = """
WITH targets AS (
    SELECT
        m.id AS message_id,
        m.member_id,
        m.channel_id,
        m.content,
        m.created_at,
        COALESCE(mem.display_name, mem.user_name, m.member_id::text) AS author_name,
        COALESCE(c.channel_name, 'unknown') AS channel_name
    FROM messages m
    LEFT JOIN members mem ON m.member_id = mem.id
    LEFT JOIN channels c ON m.channel_id = c.id
    LEFT JOIN message_sentiment s ON s.message_id = m.id
    WHERE s.message_id IS NULL
      AND m.content IS NOT NULL
      AND TRIM(m.content) <> ''
    ORDER BY m.created_at DESC, m.id DESC
    {limit_clause}
)
SELECT
    t.message_id,
    t.member_id,
    t.channel_id,
    t.content,
    t.created_at,
    t.author_name,
    t.channel_name,
    COALESCE(p.priors, '[]'::json) AS priors
FROM targets t
LEFT JOIN LATERAL (
    SELECT json_agg(
        json_build_array(sub.author_name, sub.content)
        ORDER BY sub.created_at ASC, sub.id ASC
    ) AS priors
    FROM (
        SELECT
            pm.id,
            pm.created_at,
            pm.content,
            COALESCE(pmem.display_name, pmem.user_name, pm.member_id::text) AS author_name
        FROM messages pm
        LEFT JOIN members pmem ON pm.member_id = pmem.id
        WHERE pm.channel_id = t.channel_id
          AND pm.content IS NOT NULL
          AND TRIM(pm.content) <> ''
          AND (
              pm.created_at < t.created_at
              OR (pm.created_at = t.created_at AND pm.id < t.message_id)
          )
        ORDER BY pm.created_at DESC, pm.id DESC
        LIMIT %s
    ) sub
) p ON TRUE
ORDER BY t.created_at DESC, t.message_id DESC
"""

UPSERT_SENTIMENT_SQL = """
INSERT INTO message_sentiment (
    message_id, polarity, polarity_score, emotions, sarcasm, toxicity,
    directed_at, confidence, rationale, model
) VALUES (
    %s, %s, %s, %s, %s::boolean, %s, %s, %s, %s, %s
)
ON CONFLICT (message_id) DO UPDATE SET
    polarity = EXCLUDED.polarity,
    polarity_score = EXCLUDED.polarity_score,
    emotions = EXCLUDED.emotions,
    sarcasm = EXCLUDED.sarcasm,
    toxicity = EXCLUDED.toxicity,
    directed_at = EXCLUDED.directed_at,
    confidence = EXCLUDED.confidence,
    rationale = EXCLUDED.rationale,
    model = EXCLUDED.model,
    updated_at = CURRENT_TIMESTAMP
"""


@dataclass(frozen=True)
class UnscoredMessage:
    message_id: int
    member_id: int | None
    channel_id: int | None
    content: str
    created_at: datetime | None
    author_name: str
    channel_name: str
    priors: tuple[tuple[str, str], ...]


def _parse_priors(raw: Any) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    data = json.loads(raw) if isinstance(raw, str) else raw
    parsed: list[tuple[str, str]] = []
    for entry in data or []:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        author = str(entry[0] or "unknown")
        content = str(entry[1] or "").strip()
        if content:
            parsed.append((author, content))
    return tuple(parsed)


class SentimentRepository:
    def __init__(self, pool: DatabasePool | None = None) -> None:
        self._pool = pool

    @property
    def pool(self) -> DatabasePool:
        return self._pool or get_pool()

    def fetch_unscored_with_priors(
        self,
        *,
        limit: int | None = None,
        context_size: int = 3,
    ) -> list[UnscoredMessage]:
        if limit is not None:
            sql = UNSCORED_WITH_PRIORS_SQL.format(limit_clause="LIMIT %s")
            params: tuple[Any, ...] = (int(limit), int(context_size))
        else:
            sql = UNSCORED_WITH_PRIORS_SQL.format(limit_clause="")
            params = (int(context_size),)

        rows = self.pool.fetchall(sql, params)
        results: list[UnscoredMessage] = []
        for (
            message_id,
            member_id,
            channel_id,
            content,
            created_at,
            author_name,
            channel_name,
            raw_priors,
        ) in rows:
            results.append(
                UnscoredMessage(
                    message_id=int(message_id),
                    member_id=int(member_id) if member_id is not None else None,
                    channel_id=int(channel_id) if channel_id is not None else None,
                    content=str(content or ""),
                    created_at=created_at,
                    author_name=str(author_name or "unknown"),
                    channel_name=str(channel_name or "unknown"),
                    priors=_parse_priors(raw_priors),
                )
            )
        return results

    def upsert_scores(self, rows: Sequence[Sequence[Any]]) -> int:
        if not rows:
            return 0
        self.pool.executemany(UPSERT_SENTIMENT_SQL, rows)
        return len(rows)
