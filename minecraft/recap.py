"""High-level orchestration for the daily Minecraft recap."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from xai_sdk import Client

from config import EASTERN, minecraft_data_dir
from db.minecraft import MinecraftRepository
from minecraft.analytics import summarize_day
from minecraft.camera import exclude_camera_sessions, load_camera_sessions
from minecraft.day import activity_date_due, day_window, pretty_date, yesterday_et
from minecraft.models import DayRecap, MinecraftDataError, PreparedPost
from minecraft.narrative import generate_narrative

logger = logging.getLogger(__name__)

# Re-export scheduling helpers used by the cog.
__all__ = [
    "activity_date_due",
    "build_day_recap",
    "prepare_post",
    "pretty_date",
    "summary_title",
    "yesterday_et",
]


def summary_title(recap: DayRecap) -> str:
    return recap.pretty_date


def _has_new_player_candidate(
    sessions: list, window_start: datetime, window_end: datetime
) -> bool:
    earliest: dict[str, datetime] = {}
    for session in sessions:
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
    """Load collector telemetry, exclude camera time, verify welcomes, summarize."""
    repository = repo or MinecraftRepository()
    window_start, window_end = day_window(day)
    now = as_of or datetime.now(EASTERN)

    try:
        sessions = repository.fetch_sessions(window_start, window_end, as_of=now)
        deaths = repository.fetch_deaths(window_start, window_end)
    except MinecraftDataError:
        raise
    except Exception as exc:
        raise MinecraftDataError(
            "Could not read Minecraft collector tables "
            "(minecraft.sessions / minecraft.deaths). "
            "Is the mc-telemetry schema applied and the collector running?"
        ) from exc

    root = data_dir if data_dir is not None else minecraft_data_dir()
    cameras = load_camera_sessions(root) if root.exists() else []
    if cameras:
        sessions = exclude_camera_sessions(sessions, cameras)

    prior_players: tuple[set[str], set[str]] = (set(), set())
    history_known = True
    if _has_new_player_candidate(sessions, window_start, window_end):
        try:
            prior_players = repository.players_seen_before(window_start)
        except Exception:
            logger.exception(
                "Could not read Minecraft player history; skipping welcomes"
            )
            history_known = False

    recap = summarize_day(
        sessions,
        deaths,
        day,
        as_of=now,
        prior_players=prior_players,
        history_known=history_known,
        cameras=cameras,
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
    """Build a playday recap, optionally persist session slices, and generate narrative.

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
