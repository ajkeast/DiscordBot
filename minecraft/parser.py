"""Date-windowed itzg/Paper Minecraft log parser and camera-session filter."""

from __future__ import annotations

import gzip
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from config import EASTERN
from minecraft.models import (
    Advancement,
    CameraSession,
    Death,
    EntityDeath,
    LogLine,
    MinecraftLogError,
    ParsedLogs,
    Prelogin,
    Session,
)

# A play day runs 05:00 to 05:00 America/New_York (outside the DST gap).
PLAY_DAY_START = time(5, 0)
LIVE_GRACE = timedelta(minutes=15)
MIDNIGHT_ROLLBACK = timedelta(hours=1)
CAMERA_GRACE = timedelta(seconds=5)
_CAMERA_OPEN = datetime(9999, 1, 1, tzinfo=timezone.utc)

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
COORD_RE = re.compile(
    r"\bx=(?P<x>-?\d+(?:\.\d+)?), y=-?\d+(?:\.\d+)?, z=(?P<z>-?\d+(?:\.\d+)?)"
)
PRELOGIN_RE = re.compile(rf"^(?P<name>{PLAYER_NAME}) \([^)]*\) lost connection:")
DONE_RE = re.compile(r'^Done \([0-9.]+s\)! For help, type "help"')

ADV_KINDS = {
    "made the advancement": "advancement",
    "completed the challenge": "challenge",
    "reached the goal": "goal",
}

# Vanilla Minecraft death message stems used strictly to distinguish player
# death broadcasts from non-death system chat lines during log parsing.
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


def strip_codes(text: str) -> str:
    return SECTION_RE.sub("", ANSI_RE.sub("", text))


def pretty_date(day: date) -> str:
    return f"{day.strftime('%A, %B')} {day.day}, {day.year}"


def _at(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock.replace(tzinfo=None), tzinfo=EASTERN)


def day_window(day: date) -> tuple[datetime, datetime]:
    """``[05:00, 05:00)`` America/New_York, named by the morning it starts."""
    return _at(day, PLAY_DAY_START), _at(day + timedelta(days=1), PLAY_DAY_START)


def playday_date(moment: datetime) -> date:
    """The play day containing ``moment`` (04:59 belongs to the previous day)."""
    local = moment.astimezone(EASTERN)
    opened = _at(local.date(), PLAY_DAY_START)
    if local < opened:
        return local.date() - timedelta(days=1)
    return local.date()


def is_death_rest(rest: str) -> bool:
    for stem in DEATH_STEMS:
        if rest == stem or rest.startswith(stem + " "):
            return True
    return False


def is_server_stop(msg: str) -> bool:
    return (
        msg.startswith("Stopping server")
        or msg.startswith("Stopping the server")
        or msg.startswith("Closing Server")
        or "This crash report has been saved" in msg
    )


def is_server_start(msg: str) -> bool:
    return (
        msg.startswith("Starting minecraft server")
        or msg.startswith("Starting Minecraft server")
        or DONE_RE.match(msg) is not None
    )


def _clock_seconds(clock: time) -> int:
    return clock.hour * 3600 + clock.minute * 60 + clock.second


def _went_backwards(prev: time, current: time) -> bool:
    return _clock_seconds(prev) - _clock_seconds(current) > int(
        MIDNIGHT_ROLLBACK.total_seconds()
    )


def _read_lines(path: Path) -> list[str]:
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


def discover_log_files(
    logs_dir: Path,
) -> tuple[list[tuple[date, int, Path]], Path | None]:
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
            dated.append(
                (date.fromisoformat(match.group("date")), int(match.group("idx")), path)
            )
    dated.sort(key=lambda item: (item[0], item[1], item[2].name))
    return dated, latest


def resolve_logs_dir(data_dir: Path) -> Path:
    nested = data_dir / "logs"
    return nested if nested.is_dir() else data_dir


def _system_body(msg: str) -> str | None:
    prefix = "System chat:"
    if not msg.startswith(prefix):
        return None
    return msg[len(prefix) :].strip()


def _collect_players(lines: list[LogLine]) -> dict[str, str | None]:
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


def _split_known_player(body: str, names: set[str]) -> tuple[str, str] | None:
    best: str | None = None
    for name in names:
        if body.startswith(name + " ") and (best is None or len(name) > len(best)):
            best = name
    if best is None:
        return None
    return best, body[len(best) + 1 :]


def _normalize_entity_message(entity: str, message: str) -> str:
    prefix = entity + " "
    return message[len(prefix) :] if message.startswith(prefix) else message


def interpret_lines(
    lines: list[LogLine],
    *,
    as_of: datetime | None = None,
    files_found: int = 0,
) -> ParsedLogs:
    """Turn timestamped log lines into sessions, deaths, advancements, and notes."""
    known_uuids = _collect_players(lines)
    known = set(known_uuids)
    uuids = {name: uid for name, uid in known_uuids.items() if uid}

    online: dict[str, datetime] = {}
    sessions: list[Session] = []
    deaths: list[Death] = []
    advancements: list[Advancement] = []
    entity_deaths: list[EntityDeath] = []
    prelogins: list[Prelogin] = []
    previous_dt: datetime | None = None

    def close_player(name: str, at: datetime, reason: str) -> None:
        start = online.pop(name, None)
        if start is None:
            return
        end = start if at < start else at
        sessions.append(Session(player=name, start=start, end=end, end_reason=reason))

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
            close_all(previous_dt or line.dt, "server_stop")
            previous_dt = line.dt
            continue
        if UUID_RE.search(msg):
            previous_dt = line.dt
            continue

        body = _system_body(msg)
        if body is not None:
            if not body.startswith("[Rcon"):
                join_match = JOIN_RE.match(body)
                if join_match:
                    name = join_match.group("name")
                    known.add(name)
                    if name in online:
                        close_player(name, line.dt, "rejoin")
                    online[name] = line.dt
                elif leave_match := LEAVE_RE.match(body):
                    close_player(leave_match.group("name"), line.dt, "leave")
                elif adv_match := ADV_RE.match(body):
                    advancements.append(
                        Advancement(
                            player=adv_match.group("name"),
                            at=line.dt,
                            kind=ADV_KINDS[adv_match.group("verb")],
                            title=adv_match.group("title").strip(),
                        )
                    )
                else:
                    split = _split_known_player(body, known)
                    if split is not None:
                        name, rest = split
                        if is_death_rest(rest):
                            deaths.append(Death(player=name, at=line.dt, message=rest))
            previous_dt = line.dt
            continue

        prelogin_match = PRELOGIN_RE.match(msg)
        if prelogin_match:
            name = prelogin_match.group("name")
            if name not in known:
                prelogins.append(Prelogin(name=name, at=line.dt))
            previous_dt = line.dt
            continue

        entity_match = ENTITY_DEATH_RE.match(msg)
        if entity_match:
            entity = entity_match.group("entity")
            coord = COORD_RE.search(msg)
            entity_deaths.append(
                EntityDeath(
                    entity=entity,
                    at=line.dt,
                    message=_normalize_entity_message(
                        entity, entity_match.group("message")
                    ),
                    x=float(coord.group("x")) if coord else None,
                    z=float(coord.group("z")) if coord else None,
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

    sessions = [s for s in sessions if s.duration_seconds > 0]
    return ParsedLogs(
        sessions=sessions,
        deaths=deaths,
        advancements=advancements,
        entity_deaths=entity_deaths,
        prelogins=prelogins,
        uuids=uuids,
        files_found=files_found,
        last_event_at=previous_dt,
    )


def parse_log_directory(
    logs_dir: Path,
    *,
    day: date | None = None,
    as_of: datetime | None = None,
    lookback_days: int = 2,
    lookahead_days: int = 2,
) -> ParsedLogs:
    """Parse rolled logs and ``latest.log`` bounded around ``day`` when provided.

    When ``day`` is specified, rolled files outside
    ``[day - lookback_days, day + lookahead_days]`` are skipped without
    decompressing, keeping I/O and memory bounded regardless of server age.
    """
    if not logs_dir.is_dir():
        raise MinecraftLogError(f"Minecraft logs directory does not exist: {logs_dir}")
    dated, latest = discover_log_files(logs_dir)
    if not dated and latest is None:
        raise MinecraftLogError(f"No Minecraft log files in {logs_dir}")

    newest_dated_day = dated[-1][0] if dated else None
    if day is not None:
        lo_day = day - timedelta(days=lookback_days)
        hi_day = day + timedelta(days=lookahead_days)
        selected_dated = [item for item in dated if lo_day <= item[0] <= hi_day]
        include_latest = latest is not None and (
            newest_dated_day is None or newest_dated_day <= hi_day
        )
    else:
        selected_dated = dated
        include_latest = latest is not None

    lines: list[LogLine] = []
    last_dated: datetime | None = None
    for start, _idx, path in selected_dated:
        stamped = _stamp_lines(_read_lines(path), start)
        lines.extend(stamped)
        if stamped:
            last_dated = (
                stamped[-1].dt
                if last_dated is None
                else max(last_dated, stamped[-1].dt)
            )

    files_found = len(selected_dated)
    if include_latest and latest is not None:
        raw = _read_lines(latest)
        first = _first_clock(raw)
        if first is not None:
            start = infer_latest_start(first, last_dated, as_of=as_of)
            lines.extend(_stamp_lines(raw, start))
        files_found += 1

    return interpret_lines(lines, as_of=as_of, files_found=files_found)


def parse_data_dir(
    data_dir: Path,
    *,
    day: date | None = None,
    as_of: datetime | None = None,
) -> ParsedLogs:
    return parse_log_directory(resolve_logs_dir(data_dir), day=day, as_of=as_of)


def _parse_instant(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_camera_sessions(data_dir: Path) -> list[CameraSession]:
    """Load camera-mode intervals from ``plugins/Camera/sessions.jsonl``."""
    path = data_dir / "plugins" / "Camera" / "sessions.jsonl"
    if not path.is_file():
        return []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    sessions: list[CameraSession] = []
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        player = row.get("player")
        start = _parse_instant(row.get("start"))
        if not isinstance(player, str) or not player.strip() or start is None:
            continue
        end = _parse_instant(row.get("end")) if row.get("end") else None
        if end is not None and end < start:
            continue
        sessions.append(CameraSession(player=player.strip(), start=start, end=end))
    return sessions


def _camera_end(camera: CameraSession) -> datetime:
    return camera.end if camera.end is not None else _CAMERA_OPEN


def _session_covered(session: Session, cameras: list[CameraSession]) -> bool:
    own = [c for c in cameras if c.player == session.player]
    if not own:
        return False
    for cam in own:
        end = _camera_end(cam)
        if session.start >= cam.start - CAMERA_GRACE and session.end <= end + CAMERA_GRACE:
            return True
    spans = sorted(
        (max(c.start, session.start), min(_camera_end(c), session.end))
        for c in own
        if _camera_end(c) > session.start and c.start < session.end
    )
    cursor = session.start
    for start, end in spans:
        if start > cursor:
            return False
        if end > cursor:
            cursor = end
    return cursor >= session.end


def camera_overlap_seconds(
    start: datetime,
    end: datetime,
    cameras: list[CameraSession],
    player: str,
) -> int:
    spans = sorted(
        (max(start, c.start), min(end, _camera_end(c)))
        for c in cameras
        if c.player == player and min(end, _camera_end(c)) > max(start, c.start)
    )
    if not spans:
        return 0
    total = 0.0
    cur_lo, cur_hi = spans[0]
    for lo, hi in spans[1:]:
        if lo <= cur_hi:
            if hi > cur_hi:
                cur_hi = hi
        else:
            total += (cur_hi - cur_lo).total_seconds()
            cur_lo, cur_hi = lo, hi
    total += (cur_hi - cur_lo).total_seconds()
    return int(round(total))


def exclude_camera_sessions(
    parsed: ParsedLogs, cameras: list[CameraSession]
) -> ParsedLogs:
    if not cameras:
        return parsed
    parsed.camera_sessions = list(cameras)
    parsed.sessions = [
        session for session in parsed.sessions if not _session_covered(session, cameras)
    ]
    return parsed
