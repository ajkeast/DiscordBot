"""High-level orchestration for the daily Minecraft recap."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from xai_sdk import Client

from config import EASTERN, minecraft_data_dir
from db.minecraft import MinecraftRepository
from minecraft.analytics import summarize_day
from minecraft.models import DayRecap, MinecraftLogError, ParsedLogs, PreparedPost
from minecraft.narrative import generate_narrative
from minecraft.parser import (
    day_window,
    exclude_camera_sessions,
    load_camera_sessions,
    parse_data_dir,
)

logger = logging.getLogger(__name__)


def activity_date_due(now: datetime) -> date | None:
    """Return the finished play day due at/after 08:00 America/New_York, else None."""
    local = now.astimezone(EASTERN)
    if (local.hour, local.minute) < (8, 0):
        return None
    return local.date() - timedelta(days=1)


def yesterday_et(now: datetime) -> date:
    return now.astimezone(EASTERN).date() - timedelta(days=1)


def summary_title(recap: DayRecap) -> str:
    return recap.pretty_date


def _has_new_player_candidate(
    parsed: ParsedLogs, window_start: datetime, window_end: datetime
) -> bool:
    earliest: dict[str, datetime] = {}
    for session in parsed.sessions:
        prev = earliest.get(session.player)
        if prev is None or session.start < prev:
            earliest[session.player] = session.start
    return any(window_start <= first < window_end for first in earliest.values())


def build_day_recap(
    day: date,
    *,
    data_dir: Path | None = None,
    as_of: datetime | None = None,
    repo: MinecraftRepository | None = None,
) -> DayRecap:
    """Parse bounded logs, exclude camera time, verify prior players, and summarize."""
    root = data_dir if data_dir is not None else minecraft_data_dir()
    if not root.exists():
        raise MinecraftLogError(f"Minecraft data directory does not exist: {root}")

    parsed = parse_data_dir(root, day=day, as_of=as_of)
    exclude_camera_sessions(parsed, load_camera_sessions(root))

    window_start, window_end = day_window(day)
    prior_players: tuple[set[str], set[str]] = (set(), set())
    history_known = True
    if _has_new_player_candidate(parsed, window_start, window_end):
        repository = repo or MinecraftRepository()
        try:
            prior_players = repository.players_seen_before(window_start)
        except Exception:
            # Never falsely welcome regulars if Postgres history cannot be checked.
            logger.exception("Could not read Minecraft player history; skipping welcomes")
            history_known = False

    recap = summarize_day(
        parsed,
        day,
        as_of=as_of,
        prior_players=prior_players,
        history_known=history_known,
    )
    logger.info(
        "Minecraft %s: %s players, %s sessions, %s deaths",
        day.isoformat(),
        len(recap.players),
        recap.total_sessions,
        recap.total_deaths,
    )
    return recap


def prepare_post(
    day: date,
    *,
    as_of: datetime | None = None,
    data_dir: Path | None = None,
    client: Client | None = None,
    repo: MinecraftRepository | None = None,
    persist: bool = True,
) -> PreparedPost:
    """Build a playday recap, optionally persist it, and generate the narrative.

    Admin previews pass ``persist=False`` so they remain strictly read-only.
    """
    repository = repo or MinecraftRepository()
    recap = build_day_recap(day, data_dir=data_dir, as_of=as_of, repo=repository)
    persisted = False
    if persist:
        repository.persist_day(recap)
        persisted = True

    narrative, source = generate_narrative(recap, client=client)
    return PreparedPost(
        recap=recap,
        narrative=narrative,
        source=source,
        persisted=persisted,
    )
