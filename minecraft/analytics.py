"""Playday clipping, playtime aggregation, and peak stretches."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from config import EASTERN
from minecraft.camera import CameraSession, camera_overlap_seconds
from minecraft.day import day_window, pretty_date
from minecraft.models import (
    DayRecap,
    Death,
    PeakStretch,
    PlayerSummary,
    Session,
    SessionSlice,
    format_clock,
    format_duration,
)


def _in_window(moment: datetime, start: datetime, end: datetime) -> bool:
    return start <= moment < end


def _clip(
    start: datetime, end: datetime, window_start: datetime, window_end: datetime
) -> tuple[datetime, datetime] | None:
    lo = max(start, window_start)
    hi = min(end, window_end)
    if hi <= lo:
        return None
    return lo, hi


def _round_hour_span(start: datetime, end: datetime) -> tuple[str, str]:
    s = start.astimezone(EASTERN).replace(second=0, microsecond=0)
    e = end.astimezone(EASTERN).replace(second=0, microsecond=0)
    s = s.replace(minute=0) + (timedelta(hours=1) if s.minute >= 30 else timedelta(0))
    if e.minute == 0:
        labeled_end = e
    elif e.minute <= 30:
        labeled_end = e.replace(minute=0)
    else:
        labeled_end = e.replace(minute=0) + timedelta(hours=1)
    if labeled_end <= s:
        labeled_end = s + timedelta(hours=1)
    return format_clock(s), format_clock(labeled_end)


def _compute_peak_stretch(
    slices: list[SessionSlice], window_start: datetime, window_end: datetime
) -> PeakStretch | None:
    if not slices:
        return None
    step = timedelta(minutes=10)
    samples: list[tuple[datetime, frozenset[str]]] = []
    moment = window_start
    while moment < window_end:
        online = frozenset(
            sl.player_name
            for sl in slices
            if sl.slice_started_at <= moment < sl.slice_ended_at
        )
        samples.append((moment, online))
        moment += step

    peak = max((len(online) for _, online in samples), default=0)
    if peak < 2:
        return None

    threshold = max(2, peak - 1) if peak >= 3 else 2
    best: tuple[int, timedelta, datetime, datetime, set[str]] | None = None
    run_start: datetime | None = None
    run_peak = 0
    run_players: set[str] = set()

    for moment, online in samples + [(window_end, frozenset())]:
        if len(online) >= threshold:
            if run_start is None:
                run_start = moment
                run_peak = len(online)
                run_players = set(online)
            else:
                run_peak = max(run_peak, len(online))
                run_players.update(online)
        elif run_start is not None:
            span = moment - run_start
            if span >= timedelta(minutes=30):
                candidate = (run_peak, span, run_start, moment, set(run_players))
                if best is None or (candidate[0], candidate[1]) > (best[0], best[1]):
                    best = candidate
            run_start = None

    if best is None:
        return None

    peak_count, _span, start, end, players = best
    start_label, end_label = _round_hour_span(start, end)
    return PeakStretch(
        start=start,
        end=end,
        start_label=start_label,
        end_label=end_label,
        peak_players=peak_count,
        players=tuple(sorted(players, key=str.lower)),
    )


def summarize_day(
    sessions: list[Session],
    deaths: list[Death],
    day: date,
    *,
    as_of: datetime | None = None,
    prior_players: tuple[set[str], set[str]] | None = None,
    history_known: bool = True,
    cameras: list[CameraSession] | None = None,
) -> DayRecap:
    """Clip collector sessions/deaths into a typed :class:`DayRecap`."""
    window_start, window_end = day_window(day)
    window_start = window_start.astimezone(EASTERN)
    window_end = window_end.astimezone(EASTERN)
    partial = False
    if as_of is not None:
        partial = as_of.astimezone(EASTERN) < window_end

    camera_list = cameras or []
    slices: list[SessionSlice] = []
    for session in sessions:
        clipped = _clip(session.start, session.end, window_start, window_end)
        if clipped is None:
            continue
        slice_start, slice_end = clipped
        duration = int((slice_end - slice_start).total_seconds())
        if camera_list:
            duration -= camera_overlap_seconds(
                slice_start, slice_end, camera_list, session.player
            )
        if duration <= 0:
            continue
        full_seconds = session.duration_seconds
        slices.append(
            SessionSlice(
                player_name=session.player,
                player_uuid=session.uuid,
                started_at=session.start,
                ended_at=session.end,
                slice_started_at=slice_start,
                slice_ended_at=slice_end,
                duration_seconds=duration,
                full_seconds=full_seconds,
                end_reason=session.end_reason,
            )
        )

    window_deaths = [
        d for d in deaths if _in_window(d.at, window_start, window_end)
    ]

    earliest_by_player: dict[str, datetime] = {}
    for session in sessions:
        prev = earliest_by_player.get(session.player)
        if prev is None or session.start < prev:
            earliest_by_player[session.player] = session.start

    prior_names, prior_uuids = prior_players if prior_players is not None else (set(), set())
    prior_uuids_lower = {u.lower() for u in prior_uuids if u}

    slices_by_player: dict[str, list[SessionSlice]] = defaultdict(list)
    for sl in slices:
        slices_by_player[sl.player_name].append(sl)

    deaths_by_player = Counter(d.player for d in window_deaths)
    active_names: set[str] = set(slices_by_player) | set(deaths_by_player)

    uuids: dict[str, str] = {}
    for session in sessions:
        if session.uuid:
            uuids[session.player] = session.uuid

    players: list[PlayerSummary] = []
    for name in active_names:
        player_slices = slices_by_player.get(name, [])
        playtime = sum(sl.duration_seconds for sl in player_slices)
        longest = max((sl.full_seconds for sl in player_slices), default=0)
        still_online = any(
            sl.end_reason == "still_online" and sl.slice_ended_at == sl.ended_at
            for sl in player_slices
        )
        player_uuid = uuids.get(name) or next(
            (sl.player_uuid for sl in player_slices if sl.player_uuid), None
        )

        first_seen = earliest_by_player.get(name)
        seen_in_db = name in prior_names or (
            player_uuid is not None and player_uuid.lower() in prior_uuids_lower
        )
        is_new = (
            history_known
            and first_seen is not None
            and _in_window(first_seen, window_start, window_end)
            and not seen_in_db
        )

        players.append(
            PlayerSummary(
                name=name,
                uuid=player_uuid,
                playtime_seconds=playtime,
                playtime=format_duration(playtime),
                sessions=len(player_slices),
                longest_session_seconds=longest,
                longest_session=format_duration(longest) if longest else None,
                still_online=still_online,
                new_player=is_new,
                death_count=deaths_by_player.get(name, 0),
            )
        )

    players.sort(key=lambda p: (-p.playtime_seconds, p.name.lower()))

    return DayRecap(
        day=day,
        pretty_date=pretty_date(day),
        window_start=window_start,
        window_end=window_end,
        partial_day=partial,
        players=players,
        slices=slices,
        deaths=window_deaths,
        player_uuids=uuids,
        peak_stretch=_compute_peak_stretch(slices, window_start, window_end),
    )
