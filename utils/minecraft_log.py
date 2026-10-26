"""Parse itzg/Paper Minecraft logs into sessions, deaths, and advancements.

Log lines carry only a wall-clock ``[HH:MM:SS]`` in America/New_York. The date
comes from rolled filenames (``YYYY-MM-DD-N.log`` / ``.gz``). ``latest.log``
starts on the day of its first line: the day after the newest rolled event
when its clock time goes backwards, otherwise that same day. A file that
itself rolls past midnight (clock jumps backwards by more than an hour) continues
onto the next calendar day. Small backwards jitter does not.

Sessions come from ``X joined the game`` / ``X left the game``. A server stop,
crash, or the next startup closes anyone still online. Playtime for a calendar
day is clipped to midnight–midnight Eastern.
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

# Unbroken sessions longer than this are flagged as likely AFK.
AFK_SESSION_SECONDS = 6 * 60 * 60
# Repeated non-player deaths (villagers, etc.) only surface above this count.
ENTITY_NOTE_MIN_COUNT = 5
# If the newest log line is this recent, an open session is still live.
LIVE_GRACE = timedelta(minutes=15)
# Clock going backwards by more than this means the file crossed midnight.
# One hour ignores thread reordering and the DST fall-back hour.
MIDNIGHT_ROLLBACK = timedelta(hours=1)

PLAYER_NAME = r"[A-Za-z0-9_]{1,16}"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SECTION_RE = re.compile(r"§[0-9A-FK-ORa-fk-or]")
LINE_RE = re.compile(
    r"^\[(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})(?:\.\d+)?\] \[[^\]]+\]: (?P<msg>.*)$"
)
DATED_LOG_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})-(?P<idx>\d+)\.log(?:\.gz)?$"
)
UUID_RE = re.compile(
    rf"UUID of player (?P<name>{PLAYER_NAME}) is "
    r"(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
JOIN_RE = re.compile(rf"^(?P<name>{PLAYER_NAME}) joined the game$")
LEAVE_RE = re.compile(rf"^(?P<name>{PLAYER_NAME}) left the game$")
ADV_RE = re.compile(
    rf"^(?P<name>{PLAYER_NAME}) has "
    r"(?P<verb>made the advancement|completed the challenge|reached the goal) "
    r"\[(?P<title>.+)\]$"
)
ENTITY_DEATH_RE = re.compile(
    r"^(?P<entity>[A-Za-z]+) .+ died, message: '(?P<message>.*)'$"
)
DONE_RE = re.compile(r'^Done \([0-9.]+s\)! For help, type "help"')

ADV_KINDS = {
    "made the advancement": "advancement",
    "completed the challenge": "challenge",
    "reached the goal": "goal",
}

# Vanilla death.attack / death.fell phrasing, longest first so a prefix check
# still matches the specific form ("tried to swim in lava to escape …").
DEATH_STEMS: tuple[str, ...] = tuple(
    sorted(
        (
            "was obliterated by a sonically-charged shriek",
            "didn't want to live in the same world as",
            "was poked to death by a sweet berry bush",
            "tried to swim in lava to escape",
            "tried to swim in lava",
            "discovered the floor was lava",
            "walked into danger zone due to",
            "experienced kinetic energy",
            "hit the ground too hard",
            "fell from a high place",
            "fell too far and was finished by",
            "was doomed to fall by",
            "was doomed to fall",
            "fell out of the world",
            "fell while climbing",
            "fell off a ladder",
            "fell off some twisting vines",
            "fell off some weeping vines",
            "fell off some vines",
            "fell off scaffolding",
            "was impaled on a stalagmite",
            "was impaled by",
            "was squashed by a falling anvil",
            "was squashed by a falling block",
            "was skewered by a falling stalactite",
            "was pricked to death",
            "walked into a cactus",
            "went up in flames",
            "walked into fire whilst fighting",
            "walked into fire while fighting",
            "was burned to a crisp whilst fighting",
            "was burned to a crisp while fighting",
            "burned to death",
            "went off with a bang",
            "was slain by",
            "was shot by a skull from",
            "was shot by",
            "was blown up by",
            "was fireballed by",
            "was stung to death by",
            "was stung to death",
            "was killed by even more magic",
            "was killed by magic",
            "was killed by",
            "was killed trying to hurt",
            "was killed",
            "was speared by",
            "was smashed by",
            "starved to death",
            "suffocated in a wall",
            "was squished too much",
            "was squished by",
            "withered away",
            "was pummeled by",
            "was frozen to death by",
            "was frozen to death",
            "froze to death",
            "was struck by lightning",
            "was roasted in dragon breath by",
            "was roasted in dragon breath",
            "died from dehydration",
            "died because of",
            "left the confines of this world",
            "drowned whilst trying to escape",
            "drowned while trying to escape",
            "drowned",
            "blew up",
            "died",
        ),
        key=len,
        reverse=True,
    )
)


class MinecraftLogError(RuntimeError):
    """Logs directory is missing or has no server logs to read."""


@dataclass(frozen=True)
class LogLine:
    dt: datetime
    msg: str


@dataclass(frozen=True)
class Session:
    player: str
    start: datetime
    end: datetime
    end_reason: str

    @property
    def duration_seconds(self) -> int:
        return int((self.end - self.start).total_seconds())


@dataclass(frozen=True)
class Death:
    player: str
    at: datetime
    message: str


@dataclass(frozen=True)
class Advancement:
    player: str
    at: datetime
    kind: str
    title: str


@dataclass(frozen=True)
class EntityDeath:
    entity: str
    at: datetime
    message: str


@dataclass
class ParsedLogs:
    sessions: list[Session] = field(default_factory=list)
    deaths: list[Death] = field(default_factory=list)
    advancements: list[Advancement] = field(default_factory=list)
    entity_deaths: list[EntityDeath] = field(default_factory=list)
    uuids: dict[str, str] = field(default_factory=dict)
    files_found: int = 0
    last_event_at: datetime | None = None


def strip_codes(text: str) -> str:
    """Remove ANSI CSI color codes and legacy section-sign formatting."""
    text = ANSI_RE.sub("", text)
    text = SECTION_RE.sub("", text)
    return text


def format_duration(seconds: int) -> str:
    """Compact playtime: ``17h 53m``, ``44m``, or ``30s`` under a minute."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    total_minutes = seconds // 60
    if seconds % 60 >= 30:
        total_minutes += 1
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def pretty_date(day: date) -> str:
    return f"{day.strftime('%A, %B')} {day.day}, {day.year}"


def is_death_rest(rest: str) -> bool:
    """True when the text after a player name is a vanilla death message."""
    for stem in DEATH_STEMS:
        if rest == stem or rest.startswith(stem + " "):
            return True
    return False


def is_server_stop(msg: str) -> bool:
    if msg.startswith("Stopping server") or msg.startswith("Stopping the server"):
        return True
    if msg.startswith("Closing Server"):
        return True
    return "This crash report has been saved" in msg


def is_server_start(msg: str) -> bool:
    if msg.startswith("Starting minecraft server") or msg.startswith("Starting Minecraft server"):
        return True
    return DONE_RE.match(msg) is not None


def _clock_seconds(clock: time) -> int:
    return clock.hour * 3600 + clock.minute * 60 + clock.second


def _went_backwards(prev: time, current: time) -> bool:
    return _clock_seconds(prev) - _clock_seconds(current) > int(MIDNIGHT_ROLLBACK.total_seconds())


def _at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock.replace(tzinfo=None), tzinfo=EASTERN)


def _read_text(path: Path) -> list[str]:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        return handle.read().splitlines()


def _parse_clock_line(raw: str) -> tuple[time, str] | None:
    match = LINE_RE.match(raw.rstrip("\r"))
    if not match:
        return None
    clock = time(int(match.group("h")), int(match.group("m")), int(match.group("s")))
    msg = strip_codes(match.group("msg")).strip()
    if not msg:
        return None
    return clock, msg


def _stamp_lines(raw_lines: list[str], start: date) -> list[LogLine]:
    current = start
    prev: time | None = None
    stamped: list[LogLine] = []
    for raw in raw_lines:
        parsed = _parse_clock_line(raw)
        if parsed is None:
            continue
        clock, msg = parsed
        if prev is not None and _went_backwards(prev, clock):
            current += timedelta(days=1)
        prev = clock
        stamped.append(LogLine(dt=_at(current, clock), msg=msg))
    return stamped


def _first_clock(raw_lines: list[str]) -> time | None:
    for raw in raw_lines:
        parsed = _parse_clock_line(raw)
        if parsed is not None:
            return parsed[0]
    return None


def infer_latest_start(
    first_clock: time,
    last_dated: datetime | None,
    *,
    as_of: datetime | None,
) -> date:
    """Calendar date of ``latest.log``'s first line.

    Rolled files anchor the timeline. When there are none, ``as_of`` (or now)
    is the day the live file belongs to, unless its first clock time is still
    in the future — then the file started yesterday.
    """
    if last_dated is not None:
        last_local = last_dated.astimezone(EASTERN)
        same_day = _at(last_local.date(), first_clock)
        if same_day >= last_local:
            return last_local.date()
        return last_local.date() + timedelta(days=1)
    ref = (as_of or datetime.now(EASTERN)).astimezone(EASTERN)
    candidate = _at(ref.date(), first_clock)
    if candidate - ref > timedelta(hours=1):
        return ref.date() - timedelta(days=1)
    return ref.date()


def discover_log_files(logs_dir: Path) -> tuple[list[tuple[date, int, Path]], Path | None]:
    dated: list[tuple[date, int, Path]] = []
    latest: Path | None = None
    if not logs_dir.is_dir():
        return dated, latest
    for path in logs_dir.iterdir():
        if not path.is_file():
            continue
        if path.name == "latest.log":
            latest = path
            continue
        match = DATED_LOG_RE.match(path.name)
        if match:
            dated.append((date.fromisoformat(match.group("date")), int(match.group("idx")), path))
    dated.sort(key=lambda item: (item[0], item[1], item[2].name))
    return dated, latest


def resolve_logs_dir(data_dir: Path) -> Path:
    """``MINECRAFT_DATA_DIR`` is the server data root; logs live in ``logs/``.

    A directory that already contains ``latest.log`` is accepted too, so tests
    can point straight at a fixture folder.
    """
    nested = data_dir / "logs"
    if nested.is_dir():
        return nested
    return data_dir


def _collect_players(lines: list[LogLine]) -> dict[str, str | None]:
    """Player names from joins, UUID lines, and advancement lines.

    Deaths are matched against this set so scanner disconnects and villager
    death lines cannot become player deaths.
    """
    uuids: dict[str, str | None] = {}
    for line in lines:
        uuid_match = UUID_RE.search(line.msg)
        if uuid_match:
            uuids[uuid_match.group("name")] = uuid_match.group("uuid")
            continue
        body = _system_body(line.msg)
        if body is None or body.startswith("[Rcon"):
            continue
        join_match = JOIN_RE.match(body)
        if join_match:
            uuids.setdefault(join_match.group("name"), None)
            continue
        adv_match = ADV_RE.match(body)
        if adv_match:
            uuids.setdefault(adv_match.group("name"), None)
    return uuids


def _system_body(msg: str) -> str | None:
    prefix = "System chat:"
    if not msg.startswith(prefix):
        return None
    return msg[len(prefix):].strip()


def _split_known_player(body: str, names: set[str]) -> tuple[str, str] | None:
    best: str | None = None
    for name in names:
        prefix = name + " "
        if body.startswith(prefix) and (best is None or len(name) > len(best)):
            best = name
    if best is None:
        return None
    return best, body[len(best) + 1:]


def _normalize_entity_message(entity: str, message: str) -> str:
    prefix = entity + " "
    if message.startswith(prefix):
        return message[len(prefix):]
    return message


def interpret_lines(
    lines: list[LogLine],
    *,
    as_of: datetime | None = None,
    files_found: int = 0,
) -> ParsedLogs:
    """Turn timestamped log lines into sessions and events.

    Lines must already be in log order. Open sessions at the end of the file
    run until the last log line, or until ``as_of`` when that is only a few
    minutes later (the server is still up and simply quiet).
    """
    known_uuids = _collect_players(lines)
    known = set(known_uuids)
    uuids = {name: uuid for name, uuid in known_uuids.items() if uuid}
    online: dict[str, datetime] = {}
    sessions: list[Session] = []
    deaths: list[Death] = []
    advancements: list[Advancement] = []
    entity_deaths: list[EntityDeath] = []
    previous_dt: datetime | None = None

    def close_player(name: str, at: datetime, reason: str) -> None:
        start = online.pop(name, None)
        if start is None:
            return
        if at < start:
            at = start
        sessions.append(Session(player=name, start=start, end=at, end_reason=reason))

    def close_all(at: datetime, reason: str) -> None:
        for name in list(online):
            close_player(name, at, reason)

    for line in lines:
        msg = line.msg
        if is_server_stop(msg):
            close_all(line.dt, "server_stop")
            previous_dt = line.dt
            continue
        if is_server_start(msg):
            # Downtime belongs to nobody. End open sessions at the last line
            # before the new process, not at the boot timestamp.
            close_all(previous_dt or line.dt, "server_stop")
            previous_dt = line.dt
            continue

        uuid_match = UUID_RE.search(msg)
        if uuid_match:
            previous_dt = line.dt
            continue

        body = _system_body(msg)
        if body is not None:
            if not body.startswith("[Rcon"):
                _consume_system(
                    body,
                    line.dt,
                    known=known,
                    online=online,
                    sessions_close=close_player,
                    deaths=deaths,
                    advancements=advancements,
                )
            previous_dt = line.dt
            continue

        entity_match = ENTITY_DEATH_RE.match(msg)
        if entity_match:
            entity = entity_match.group("entity")
            entity_deaths.append(
                EntityDeath(
                    entity=entity,
                    at=line.dt,
                    message=_normalize_entity_message(entity, entity_match.group("message")),
                )
            )
        previous_dt = line.dt

    if online and previous_dt is not None:
        close_at = previous_dt
        if as_of is not None:
            as_of_local = as_of.astimezone(EASTERN)
            gap = as_of_local - previous_dt
            if timedelta(0) <= gap <= LIVE_GRACE:
                close_at = as_of_local
        close_all(close_at, "still_online")

    sessions = [session for session in sessions if session.duration_seconds > 0]
    return ParsedLogs(
        sessions=sessions,
        deaths=deaths,
        advancements=advancements,
        entity_deaths=entity_deaths,
        uuids=uuids,
        files_found=files_found,
        last_event_at=previous_dt,
    )


def _consume_system(
    body: str,
    at: datetime,
    *,
    known: set[str],
    online: dict[str, datetime],
    sessions_close,
    deaths: list[Death],
    advancements: list[Advancement],
) -> None:
    join_match = JOIN_RE.match(body)
    if join_match:
        name = join_match.group("name")
        known.add(name)
        if name in online:
            sessions_close(name, at, "rejoin")
        online[name] = at
        return

    leave_match = LEAVE_RE.match(body)
    if leave_match:
        sessions_close(leave_match.group("name"), at, "leave")
        return

    adv_match = ADV_RE.match(body)
    if adv_match:
        name = adv_match.group("name")
        advancements.append(
            Advancement(
                player=name,
                at=at,
                kind=ADV_KINDS[adv_match.group("verb")],
                title=adv_match.group("title").strip(),
            )
        )
        return

    split = _split_known_player(body, known)
    if split is None:
        return
    name, rest = split
    if is_death_rest(rest):
        deaths.append(Death(player=name, at=at, message=rest))


def parse_log_directory(logs_dir: Path, *, as_of: datetime | None = None) -> ParsedLogs:
    """Parse every rolled log and ``latest.log`` under ``logs_dir``."""
    if not logs_dir.is_dir():
        raise MinecraftLogError(f"Minecraft logs directory does not exist: {logs_dir}")
    dated, latest = discover_log_files(logs_dir)
    if not dated and latest is None:
        raise MinecraftLogError(f"No Minecraft log files in {logs_dir}")

    lines: list[LogLine] = []
    last_dated: datetime | None = None
    for start, _idx, path in dated:
        stamped = _stamp_lines(_read_text(path), start)
        lines.extend(stamped)
        if stamped:
            last_dated = stamped[-1].dt if last_dated is None else max(last_dated, stamped[-1].dt)

    files_found = len(dated)
    if latest is not None:
        raw = _read_text(latest)
        first = _first_clock(raw)
        if first is not None:
            start = infer_latest_start(first, last_dated, as_of=as_of)
            lines.extend(_stamp_lines(raw, start))
        files_found += 1

    return interpret_lines(lines, as_of=as_of, files_found=files_found)


def parse_data_dir(data_dir: Path, *, as_of: datetime | None = None) -> ParsedLogs:
    return parse_log_directory(resolve_logs_dir(data_dir), as_of=as_of)


def _in_window(moment: datetime, start: datetime, end: datetime) -> bool:
    return start <= moment < end


def _clip(start: datetime, end: datetime, window_start: datetime, window_end: datetime):
    clipped_start = max(start, window_start)
    clipped_end = min(end, window_end)
    if clipped_end <= clipped_start:
        return None
    return clipped_start, clipped_end


def summarize_window(
    parsed: ParsedLogs,
    window_start: datetime,
    window_end: datetime,
    *,
    partial_day: bool = False,
) -> dict:
    """Clip parsed sessions and events to ``[window_start, window_end)``."""
    window_start = window_start.astimezone(EASTERN)
    window_end = window_end.astimezone(EASTERN)
    day = window_start.date()

    slices: list[dict] = []
    for session in parsed.sessions:
        clipped = _clip(session.start, session.end, window_start, window_end)
        if clipped is None:
            continue
        slice_start, slice_end = clipped
        duration = int((slice_end - slice_start).total_seconds())
        if duration <= 0:
            continue
        full_seconds = session.duration_seconds
        slices.append(
            {
                "player_name": session.player,
                "player_uuid": parsed.uuids.get(session.player),
                "started_at": session.start.isoformat(),
                "ended_at": session.end.isoformat(),
                "slice_started_at": slice_start.isoformat(),
                "slice_ended_at": slice_end.isoformat(),
                "duration_seconds": duration,
                "full_seconds": full_seconds,
                "end_reason": session.end_reason,
                "likely_afk": full_seconds > AFK_SESSION_SECONDS,
            }
        )

    deaths = [
        {
            "player": death.player,
            "uuid": parsed.uuids.get(death.player),
            "at": death.at.isoformat(),
            "time": death.at.astimezone(EASTERN).strftime("%H:%M:%S"),
            "message": death.message,
        }
        for death in parsed.deaths
        if _in_window(death.at, window_start, window_end)
    ]
    advancements = [
        {
            "player": adv.player,
            "uuid": parsed.uuids.get(adv.player),
            "at": adv.at.isoformat(),
            "time": adv.at.astimezone(EASTERN).strftime("%H:%M:%S"),
            "kind": adv.kind,
            "title": adv.title,
        }
        for adv in parsed.advancements
        if _in_window(adv.at, window_start, window_end)
    ]

    names: set[str] = set()
    names.update(item["player_name"] for item in slices)
    names.update(item["player"] for item in deaths)
    names.update(item["player"] for item in advancements)

    players: list[dict] = []
    for name in names:
        player_slices = [item for item in slices if item["player_name"] == name]
        playtime = sum(item["duration_seconds"] for item in player_slices)
        long_playtime = sum(
            item["duration_seconds"] for item in player_slices if item["likely_afk"]
        )
        longest = max((item["full_seconds"] for item in player_slices), default=0)
        player_deaths = sum(1 for item in deaths if item["player"] == name)
        player_advs = sum(1 for item in advancements if item["player"] == name)
        likely_afk = any(item["likely_afk"] for item in player_slices)
        players.append(
            {
                "name": name,
                "uuid": parsed.uuids.get(name),
                "playtime_seconds": playtime,
                "playtime": format_duration(playtime),
                "sessions": len(player_slices),
                "longest_session_seconds": longest,
                "longest_session": format_duration(longest) if longest else None,
                "likely_afk": likely_afk,
                "mostly_afk": bool(playtime and long_playtime * 2 >= playtime),
                "deaths": player_deaths,
                "advancements": player_advs,
            }
        )
    players.sort(key=lambda item: (-item["playtime_seconds"], item["name"].lower()))

    death_counts_map: dict[tuple[str, str], int] = {}
    for item in deaths:
        key = (item["player"], item["message"])
        death_counts_map[key] = death_counts_map.get(key, 0) + 1
    death_counts = [
        {"player": player, "message": message, "count": count}
        for (player, message), count in death_counts_map.items()
    ]
    death_counts.sort(key=lambda item: (-item["count"], item["player"].lower(), item["message"]))

    note_map: dict[tuple[str, str], int] = {}
    for entity_death in parsed.entity_deaths:
        if not _in_window(entity_death.at, window_start, window_end):
            continue
        key = (entity_death.entity, entity_death.message)
        note_map[key] = note_map.get(key, 0) + 1
    server_notes = [
        {"entity": entity, "message": message, "count": count}
        for (entity, message), count in note_map.items()
        if count >= ENTITY_NOTE_MIN_COUNT
    ]
    server_notes.sort(key=lambda item: (-item["count"], item["entity"]))
    server_notes = server_notes[:3]

    return {
        "date": day.isoformat(),
        "weekday": day.strftime("%A"),
        "pretty_date": pretty_date(day),
        "timezone": "America/New_York",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "partial_day": partial_day,
        "nobody_played": not players,
        "players": players,
        "totals": {
            "players": len(players),
            "playtime_seconds": sum(item["playtime_seconds"] for item in players),
            "sessions": sum(item["sessions"] for item in players),
            "deaths": len(deaths),
            "advancements": len(advancements),
        },
        "deaths": deaths,
        "death_counts": death_counts,
        "advancements": advancements,
        "server_notes": server_notes,
        "slices": slices,
    }


def summarize_day(
    parsed: ParsedLogs,
    day: date,
    *,
    as_of: datetime | None = None,
) -> dict:
    """Previous-calendar-day window: midnight to midnight Eastern."""
    window_start = _at(day, time(0, 0))
    window_end = window_start + timedelta(days=1)
    partial = False
    if as_of is not None:
        partial = as_of.astimezone(EASTERN) < window_end
    return summarize_window(parsed, window_start, window_end, partial_day=partial)


def day_window(day: date) -> tuple[datetime, datetime]:
    start = _at(day, time(0, 0))
    return start, start + timedelta(days=1)
