"""Playday clipping, playtime/AFK aggregation, peak stretches, and server events."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from config import EASTERN
from minecraft.models import (
    DayRecap,
    EntityDeath,
    ParsedLogs,
    PeakStretch,
    PlayerSummary,
    Prelogin,
    SessionSlice,
    format_clock,
    format_duration,
)
from minecraft.parser import camera_overlap_seconds, day_window, pretty_date

AFK_SESSION_SECONDS = 6 * 60 * 60
ENTITY_NOTE_MIN_COUNT = 5
SCANNER_MIN_ATTEMPTS = 5


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


def _compute_server_events(
    entity_deaths: list[EntityDeath], prelogins: list[Prelogin]
) -> list[str]:
    notes: list[str] = []

    by_entity_msg: dict[tuple[str, str], list[EntityDeath]] = defaultdict(list)
    for ed in entity_deaths:
        by_entity_msg[(ed.entity, ed.message)].append(ed)

    top_entity_groups = sorted(
        (
            (entity, message, group)
            for (entity, message), group in by_entity_msg.items()
            if len(group) >= ENTITY_NOTE_MIN_COUNT
        ),
        key=lambda item: (-len(item[2]), item[0].lower()),
    )[:3]

    for entity, message, group in top_entity_groups:
        placed = [d for d in group if d.x is not None and d.z is not None]
        where = ""
        if len(placed) >= ENTITY_NOTE_MIN_COUNT:
            xs = sorted(d.x for d in placed if d.x is not None)
            zs = sorted(d.z for d in placed if d.z is not None)
            where = f" near x {round(xs[len(xs) // 2])}, z {round(zs[len(zs) // 2])}"
        notes.append(f"{entity} {message} ({len(group)} times{where})")

    prelogin_counts = Counter(p.name for p in prelogins)
    scanners = [
        (name, count)
        for name, count in prelogin_counts.most_common(3)
        if count >= SCANNER_MIN_ATTEMPTS
    ]
    if len(scanners) == 1:
        name, count = scanners[0]
        notes.append(
            f"{name} tried to connect {count} times without joining (likely scanner)"
        )
    elif scanners:
        names = ", ".join(f"{name} ({count}x)" for name, count in scanners)
        notes.append(
            f"Repeated failed login attempts without joining: {names} (likely scanners)"
        )

    return notes


def summarize_window(
    parsed: ParsedLogs,
    window_start: datetime,
    window_end: datetime,
    *,
    partial_day: bool = False,
    prior_players: tuple[set[str], set[str]] | None = None,
    history_known: bool = True,
) -> DayRecap:
    """Clip parsed events into a typed :class:`DayRecap` for ``[window_start, window_end)``."""
    window_start = window_start.astimezone(EASTERN)
    window_end = window_end.astimezone(EASTERN)
    day = window_start.date()

    slices: list[SessionSlice] = []
    for session in parsed.sessions:
        clipped = _clip(session.start, session.end, window_start, window_end)
        if clipped is None:
            continue
        slice_start, slice_end = clipped
        duration = int((slice_end - slice_start).total_seconds())
        if parsed.camera_sessions:
            duration -= camera_overlap_seconds(
                slice_start, slice_end, parsed.camera_sessions, session.player
            )
        if duration <= 0:
            continue
        full_seconds = session.duration_seconds
        slices.append(
            SessionSlice(
                player_name=session.player,
                player_uuid=parsed.uuids.get(session.player),
                started_at=session.start,
                ended_at=session.end,
                slice_started_at=slice_start,
                slice_ended_at=slice_end,
                duration_seconds=duration,
                full_seconds=full_seconds,
                end_reason=session.end_reason,
                likely_afk=full_seconds > AFK_SESSION_SECONDS,
            )
        )

    deaths = [
        d for d in parsed.deaths if _in_window(d.at, window_start, window_end)
    ]
    advancements = [
        a for a in parsed.advancements if _in_window(a.at, window_start, window_end)
    ]
    window_entity_deaths = [
        ed for ed in parsed.entity_deaths if _in_window(ed.at, window_start, window_end)
    ]
    window_prelogins = [
        p for p in parsed.prelogins if _in_window(p.at, window_start, window_end)
    ]

    # Earliest session per player across all parsed logs to catch pre-window veterans.
    earliest_session_by_player: dict[str, datetime] = {}
    for session in parsed.sessions:
        prev = earliest_session_by_player.get(session.player)
        if prev is None or session.start < prev:
            earliest_session_by_player[session.player] = session.start

    prior_names, prior_uuids = prior_players if prior_players is not None else (set(), set())
    prior_uuids_lower = {u.lower() for u in prior_uuids if u}

    slices_by_player: dict[str, list[SessionSlice]] = defaultdict(list)
    for sl in slices:
        slices_by_player[sl.player_name].append(sl)

    deaths_by_player = Counter(d.player for d in deaths)

    active_names: set[str] = set(slices_by_player) | set(deaths_by_player)
    active_names.update(a.player for a in advancements)

    players: list[PlayerSummary] = []
    for name in active_names:
        player_slices = slices_by_player.get(name, [])
        playtime = sum(sl.duration_seconds for sl in player_slices)
        longest = max((sl.full_seconds for sl in player_slices), default=0)
        likely_afk = any(sl.likely_afk for sl in player_slices)
        still_online = any(
            sl.end_reason == "still_online" and sl.slice_ended_at == sl.ended_at
            for sl in player_slices
        )
        player_uuid = parsed.uuids.get(name)

        first_seen = earliest_session_by_player.get(name)
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
                likely_afk=likely_afk,
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
        partial_day=partial_day,
        players=players,
        slices=slices,
        deaths=deaths,
        advancements=advancements,
        notable_server_events=_compute_server_events(
            window_entity_deaths, window_prelogins
        ),
        player_uuids=dict(parsed.uuids),
        peak_stretch=_compute_peak_stretch(slices, window_start, window_end),
    )


def summarize_day(
    parsed: ParsedLogs,
    day: date,
    *,
    as_of: datetime | None = None,
    prior_players: tuple[set[str], set[str]] | None = None,
    history_known: bool = True,
) -> DayRecap:
    window_start, window_end = day_window(day)
    partial = False
    if as_of is not None:
        partial = as_of.astimezone(EASTERN) < window_end
    return summarize_window(
        parsed,
        window_start,
        window_end,
        partial_day=partial,
        prior_players=prior_players,
        history_known=history_known,
    )
