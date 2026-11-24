"""Typed models for the daily Minecraft recap."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from config import EASTERN

MAX_DEATH_EVENTS_IN_PROMPT = 60


class MinecraftDataError(RuntimeError):
    """Raised when collector data or the Minecraft data directory is unavailable."""


@dataclass(frozen=True)
class Session:
    player: str
    start: datetime
    end: datetime
    end_reason: str
    uuid: str | None = None

    @property
    def duration_seconds(self) -> int:
        return max(0, int((self.end - self.start).total_seconds()))


@dataclass(frozen=True)
class SessionSlice:
    player_name: str
    player_uuid: str | None
    started_at: datetime
    ended_at: datetime
    slice_started_at: datetime
    slice_ended_at: datetime
    duration_seconds: int
    full_seconds: int
    end_reason: str
    likely_afk: bool


@dataclass(frozen=True)
class Death:
    player: str
    at: datetime
    message: str
    killer_player: str | None = None


@dataclass(frozen=True)
class CameraSession:
    """One camera-mode interval. ``end`` is None while still open."""

    player: str
    start: datetime
    end: datetime | None = None


@dataclass(frozen=True)
class PeakStretch:
    start: datetime
    end: datetime
    start_label: str
    end_label: str
    peak_players: int
    players: tuple[str, ...]


@dataclass(frozen=True)
class PlayerSummary:
    name: str
    uuid: str | None
    playtime_seconds: int
    playtime: str
    sessions: int
    longest_session_seconds: int
    longest_session: str | None
    likely_afk: bool
    still_online: bool
    new_player: bool
    death_count: int


def format_clock(moment: datetime) -> str:
    local = moment.astimezone(EASTERN)
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    if local.minute == 0:
        return f"{hour} {suffix}"
    return f"{hour}:{local.minute:02d} {suffix}"


def format_duration(seconds: int) -> str:
    """Compact playtime: ``17h 53m``, ``44m``, or ``30s`` under a minute."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    total_minutes = total // 60 + (1 if total % 60 >= 30 else 0)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


@dataclass(frozen=True)
class DayRecap:
    day: date
    pretty_date: str
    window_start: datetime
    window_end: datetime
    partial_day: bool
    players: list[PlayerSummary]
    slices: list[SessionSlice]
    deaths: list[Death]
    player_uuids: dict[str, str]
    peak_stretch: PeakStretch | None = None

    @property
    def nobody_played(self) -> bool:
        return not self.players

    @property
    def total_playtime_seconds(self) -> int:
        return sum(p.playtime_seconds for p in self.players)

    @property
    def total_sessions(self) -> int:
        return sum(p.sessions for p in self.players)

    @property
    def total_deaths(self) -> int:
        return len(self.deaths)

    def to_grok_payload(self) -> dict[str, Any]:
        """Build a clean factual payload for Grok."""
        ordered_deaths = sorted(self.deaths, key=lambda d: d.at)
        death_events: list[dict[str, Any]] = []
        for d in ordered_deaths[:MAX_DEATH_EVENTS_IN_PROMPT]:
            event: dict[str, Any] = {
                "time": format_clock(d.at),
                "player": d.player,
                "message": d.message,
            }
            if d.killer_player:
                event["pvp"] = True
                event["killer"] = d.killer_player
            death_events.append(event)

        summary: dict[str, Any] = {
            "player_count": len(self.players),
            "total_playtime": format_duration(self.total_playtime_seconds),
            "total_sessions": self.total_sessions,
            "total_deaths": self.total_deaths,
        }
        if self.peak_stretch is not None:
            summary["peak_stretch"] = {
                "start": self.peak_stretch.start_label,
                "end": self.peak_stretch.end_label,
                "peak_players": self.peak_stretch.peak_players,
                "players": list(self.peak_stretch.players),
            }

        payload: dict[str, Any] = {
            "date": self.pretty_date,
            "partial_day": self.partial_day,
            "summary": summary,
            "players": [
                {
                    "name": p.name,
                    "playtime": p.playtime,
                    "sessions": p.sessions,
                    "longest_session": p.longest_session,
                    "likely_afk": p.likely_afk,
                    "still_online": p.still_online,
                    "new_player": p.new_player,
                    "deaths": p.death_count,
                }
                for p in self.players
            ],
            "deaths": death_events,
        }

        if len(ordered_deaths) > MAX_DEATH_EVENTS_IN_PROMPT:
            counts = Counter((d.player, d.message) for d in ordered_deaths)
            payload["deaths_truncated"] = True
            payload["top_death_counts"] = [
                {"player": player, "message": msg, "count": count}
                for (player, msg), count in counts.most_common(15)
            ]

        return payload


@dataclass(frozen=True)
class PreparedPost:
    recap: DayRecap
    narrative: str
    source: str
    persisted: bool

    @property
    def nobody_played(self) -> bool:
        return self.recap.nobody_played
