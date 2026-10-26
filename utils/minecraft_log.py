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
import json
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
# Played at least this long with zero advancements: the recap may nudge them.
NO_ADVANCEMENT_NUDGE_SECONDS = 20 * 60
# Lifetime play_time (from the world stats file) counts as "new" when it is
# within this slack of every session we could see in the logs. A first join
# in a short log file is not enough — that would greet the whole server.
NEW_PLAYER_UNDER_SECONDS = 10 * 60
NEW_PLAYER_OVER_SECONDS = 30 * 60
# How many confirmed misses to name. Early story titles come first.
MISSED_ADVANCEMENT_CAP = 2
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
COORD_RE = re.compile(
    r"\bx=(?P<x>-?\d+(?:\.\d+)?), y=-?\d+(?:\.\d+)?, z=(?P<z>-?\d+(?:\.\d+)?)"
)
LOGIN_AT_RE = re.compile(
    rf"^(?P<name>{PLAYER_NAME})\[/[^\]]+\] logged in with entity id \d+ at "
    r"\(\[(?P<dimension>[^\]]+)\]"
)
PRELOGIN_RE = re.compile(
    rf"^(?P<name>{PLAYER_NAME}) \([^)]*\) lost connection:"
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
    x: float | None = None
    z: float | None = None


@dataclass(frozen=True)
class DimensionLogin:
    player: str
    at: datetime
    dimension: str


@dataclass(frozen=True)
class Prelogin:
    name: str
    at: datetime


@dataclass
class ParsedLogs:
    sessions: list[Session] = field(default_factory=list)
    deaths: list[Death] = field(default_factory=list)
    advancements: list[Advancement] = field(default_factory=list)
    entity_deaths: list[EntityDeath] = field(default_factory=list)
    logins: list[DimensionLogin] = field(default_factory=list)
    prelogins: list[Prelogin] = field(default_factory=list)
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


# Vanilla title → advancement id. Order is the nudge priority: story first,
# then other titles that show up in a normal early world. A title is only
# called "missed" when this map, someone else's earn that day, and the
# player's own advancement file all agree.
VANILLA_ADVANCEMENTS: tuple[tuple[str, str], ...] = (
    ("Stone Age", "minecraft:story/mine_stone"),
    ("Getting an Upgrade", "minecraft:story/upgrade_tools"),
    ("Acquire Hardware", "minecraft:story/smelt_iron"),
    ("Suit Up", "minecraft:story/obtain_armor"),
    ("Hot Stuff", "minecraft:story/lava_bucket"),
    ("Isn't It Iron Pick?", "minecraft:story/iron_tools"),
    ("Not Today, Thank You", "minecraft:story/deflect_arrow"),
    ("Ice Bucket Challenge", "minecraft:story/form_obsidian"),
    ("Diamonds!", "minecraft:story/mine_diamond"),
    ("We Need to Go Deeper", "minecraft:story/enter_the_nether"),
    ("Cover Me with Diamonds", "minecraft:story/shiny_gear"),
    ("Enchanter", "minecraft:story/enchant_item"),
    ("Zombie Doctor", "minecraft:story/cure_zombie_villager"),
    ("Eye Spy", "minecraft:story/follow_ender_eye"),
    ("The End?", "minecraft:story/enter_the_end"),
    ("Sweet Dreams", "minecraft:adventure/sleep_in_bed"),
    ("Monster Hunter", "minecraft:adventure/kill_a_mob"),
    ("What a Deal!", "minecraft:adventure/trade"),
    ("Voluntary Exile", "minecraft:adventure/voluntary_exile"),
    ("Surge Protector", "minecraft:adventure/lightning_rod_with_villager_no_fire"),
    ("A Seedy Place", "minecraft:husbandry/plant_seed"),
    ("The Parrots and the Bats", "minecraft:husbandry/breed_an_animal"),
    ("A Terrible Fortress", "minecraft:nether/find_fortress"),
    ("Those Were the Days", "minecraft:nether/find_bastion"),
    ("Who is Cutting Onions?", "minecraft:nether/obtain_crying_obsidian"),
    ("This Boat Has Legs", "minecraft:nether/ride_strider"),
    ("Into Fire", "minecraft:nether/obtain_blaze_rod"),
    ("Hot Tourist Destinations", "minecraft:nether/explore_nether"),
)

VANILLA_ADVANCEMENT_IDS = {title: adv_id for title, adv_id in VANILLA_ADVANCEMENTS}


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
    logins: list[DimensionLogin] = []
    prelogins: list[Prelogin] = []
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

        login_match = LOGIN_AT_RE.match(msg)
        if login_match:
            logins.append(
                DimensionLogin(
                    player=login_match.group("name"),
                    at=line.dt,
                    dimension=login_match.group("dimension"),
                )
            )
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
                    message=_normalize_entity_message(entity, entity_match.group("message")),
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

    sessions = [session for session in sessions if session.duration_seconds > 0]
    return ParsedLogs(
        sessions=sessions,
        deaths=deaths,
        advancements=advancements,
        entity_deaths=entity_deaths,
        logins=logins,
        prelogins=prelogins,
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

    deaths_by_player: dict[str, dict[str, int]] = {}
    for item in deaths:
        counts = deaths_by_player.setdefault(item["player"], {})
        counts[item["message"]] = counts.get(item["message"], 0) + 1
    titles_by_player: dict[str, list[str]] = {}
    for item in advancements:
        titles = titles_by_player.setdefault(item["player"], [])
        if item["title"] not in titles:
            titles.append(item["title"])

    players: list[dict] = []
    for name in names:
        player_slices = [item for item in slices if item["player_name"] == name]
        playtime = sum(item["duration_seconds"] for item in player_slices)
        long_playtime = sum(
            item["duration_seconds"] for item in player_slices if item["likely_afk"]
        )
        longest = max((item["full_seconds"] for item in player_slices), default=0)
        player_deaths = sum(deaths_by_player.get(name, {}).values())
        player_titles = titles_by_player.get(name, [])
        likely_afk = any(item["likely_afk"] for item in player_slices)
        death_pairs = sorted(
            deaths_by_player.get(name, {}).items(),
            key=lambda pair: (-pair[1], pair[0]),
        )
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
                "death_lines": [
                    message if count == 1 else f"{message} ×{count}"
                    for message, count in death_pairs
                ],
                "advancements": len(player_titles),
                "advancement_titles": player_titles,
                "no_advancements": (
                    not player_titles and playtime >= NO_ADVANCEMENT_NUDGE_SECONDS
                ),
                "new_player": False,
                "missed_advancements": [],
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

    highlights = build_highlights(
        players=players,
        slices=slices,
        deaths=deaths,
        advancements=advancements,
        entity_deaths=[
            death
            for death in parsed.entity_deaths
            if _in_window(death.at, window_start, window_end)
        ],
        logins=[
            login
            for login in parsed.logins
            if _in_window(login.at, window_start, window_end)
        ],
        prelogins=[
            attempt
            for attempt in parsed.prelogins
            if _in_window(attempt.at, window_start, window_end)
        ],
        window_start=window_start,
        window_end=window_end,
    )

    return {
        "date": day.isoformat(),
        "weekday": day.strftime("%A"),
        "pretty_date": pretty_date(day),
        "timezone": "America/New_York",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "partial_day": partial_day,
        "nobody_played": not players,
        "new_players": [],
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
        "highlights": highlights,
        "slices": slices,
    }


def titles_earned_by_others(stats: dict, name: str) -> list[str]:
    """Titles someone else earned in this window, early story first."""
    earned: list[str] = []
    seen: set[str] = set()
    for item in stats.get("advancements", []):
        title = item["title"]
        if item["player"] == name or title in seen:
            continue
        seen.add(title)
        earned.append(title)
    priority = [title for title, _adv_id in VANILLA_ADVANCEMENTS if title in seen]
    return priority + [title for title in earned if title not in priority]


def play_time_looks_new(play_seconds: int, seen_seconds: int) -> bool:
    """True when world play_time is basically the sessions these logs contain."""
    if play_seconds <= 0:
        return False
    low = seen_seconds - NEW_PLAYER_UNDER_SECONDS
    high = seen_seconds + NEW_PLAYER_OVER_SECONDS
    return low <= play_seconds <= high


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _player_file_candidates(data_dir: Path, kind: str, uuid: str) -> list[Path]:
    filename = f"{uuid.lower()}.json"
    return [
        data_dir / "world" / "players" / kind / filename,
        data_dir / "world" / kind / filename,
    ]


def _first_file(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _play_time_seconds(payload) -> int | None:
    if not isinstance(payload, dict):
        return None
    custom = (payload.get("stats") or {}).get("minecraft:custom") or {}
    if not isinstance(custom, dict):
        return None
    ticks = custom.get("minecraft:play_time")
    if isinstance(ticks, bool) or not isinstance(ticks, (int, float)):
        return None
    if ticks < 0:
        return None
    return int(ticks) // 20


def _completed_advancement_ids(payload) -> set[str] | None:
    """Done advancement ids, or None when the file is not a usable object."""
    if not isinstance(payload, dict):
        return None
    done: set[str] = set()
    for key, value in payload.items():
        if key == "DataVersion" or not isinstance(value, dict):
            continue
        if value.get("done") is True:
            adv_id = key if key.startswith("minecraft:") else f"minecraft:{key}"
            done.add(adv_id)
    return done


def _lifetime_seconds(parsed: ParsedLogs) -> dict[str, int]:
    totals: dict[str, int] = {}
    for session in parsed.sessions:
        totals[session.player] = totals.get(session.player, 0) + session.duration_seconds
    return totals


def apply_world_files(stats: dict, parsed: ParsedLogs, data_dir: Path) -> dict:
    """Fill ``new_player`` and ``missed_advancements`` from the world save.

    New means the stats file's ``minecraft:play_time`` matches the sessions in
    these logs, within a few minutes. Missed means someone else earned that
    title today, we know its vanilla id, this player's advancement file
    exists, and that id is not done. No file, no claim.
    """
    for player in stats["players"]:
        player["new_player"] = False
        player["missed_advancements"] = []
    stats["new_players"] = []
    if not data_dir.is_dir():
        return stats

    lifetime = _lifetime_seconds(parsed)
    for player in stats["players"]:
        uuid = player.get("uuid")
        if not uuid:
            continue
        stats_path = _first_file(_player_file_candidates(data_dir, "stats", uuid))
        if stats_path is not None:
            play_seconds = _play_time_seconds(_read_json(stats_path))
            if play_seconds is not None and play_time_looks_new(
                play_seconds, lifetime.get(player["name"], 0)
            ):
                player["new_player"] = True

        adv_path = _first_file(_player_file_candidates(data_dir, "advancements", uuid))
        if adv_path is None:
            continue
        done = _completed_advancement_ids(_read_json(adv_path))
        if done is None:
            continue
        others = set(titles_earned_by_others(stats, player["name"]))
        own_titles = set(player.get("advancement_titles") or [])
        missed: list[str] = []
        for title, adv_id in VANILLA_ADVANCEMENTS:
            if title not in others or title in own_titles or adv_id in done:
                continue
            missed.append(title)
            if len(missed) >= MISSED_ADVANCEMENT_CAP:
                break
        player["missed_advancements"] = missed

    stats["new_players"] = [
        player["name"] for player in stats["players"] if player["new_player"]
    ]
    _sync_highlight_people(stats)
    return stats


_COUNT_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
}

_NETHER_CAUSES = {"lava", "Magma Cubes", "Ghasts", "Piglins", "Blazes"}
_NETHER_PROOF = {"Ghasts", "Piglins", "Blazes"}
_NETHER_TITLES = {
    "A Terrible Fortress",
    "Those Were the Days",
    "Who is Cutting Onions?",
    "This Boat Has Legs",
    "Into Fire",
    "Hot Tourist Destinations",
    "Return to Sender",
    "Spooky Scary Skeleton",
}


def _count_word(count: int) -> str:
    return _COUNT_WORDS.get(count, str(count))


def _clock_label(moment: datetime) -> str:
    local = moment.astimezone(EASTERN)
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    if local.minute == 0:
        return f"{hour} {suffix}"
    return f"{hour}:{local.minute:02d} {suffix}"


def _hour_span_label(start: datetime, end: datetime) -> tuple[str, str]:
    """Round a busy stretch onto the clock hours a person would name.

    ``end`` is the first quiet sample, so 11:20 stays 11 rather than becoming
    midnight. A start on the half hour rounds up.
    """
    start = start.astimezone(EASTERN).replace(second=0, microsecond=0)
    end = end.astimezone(EASTERN).replace(second=0, microsecond=0)
    if start.minute >= 30:
        start = start.replace(minute=0) + timedelta(hours=1)
    else:
        start = start.replace(minute=0)
    if end.minute == 0:
        labeled_end = end
    elif end.minute <= 30:
        labeled_end = end.replace(minute=0)
    else:
        labeled_end = end.replace(minute=0) + timedelta(hours=1)
    if labeled_end <= start:
        labeled_end = start + timedelta(hours=1)
    return _clock_label(start), _clock_label(labeled_end)


def _death_mentions(message: str) -> list[str]:
    """Every cause worth naming. Lava-while-escaping-a-mob counts as both."""
    text = message.lower()
    found: list[str] = []
    for needle, label in (
        ("lava", "lava"),
        ("magma", "Magma Cubes"),
        ("ghast", "Ghasts"),
        ("piglin", "Piglins"),
        ("blaze", "Blazes"),
        ("creeper", "a Creeper"),
        ("zombie", "a Zombie"),
        ("wither skeleton", "a Wither Skeleton"),
        ("drowned", "drowning"),
    ):
        if needle in text and label not in found:
            found.append(label)
    if any(token in text for token in ("fell", "doomed to fall", "high place")):
        found.append("a fall")
    mobs = {"Magma Cubes", "Ghasts", "Piglins", "Blazes", "a Creeper", "a Zombie", "a Wither Skeleton"}
    if ("blown up" in text or "explosion" in text) and not mobs.intersection(found):
        found.append("an explosion")
    return found or [message]


def _death_cause(message: str) -> str:
    mentions = _death_mentions(message)
    for label in mentions:
        if label not in {"lava", "a fall", "drowning", "an explosion"}:
            return label
    return mentions[0]


def _combined_label(seconds: int) -> str:
    hours = seconds / 3600
    rounded = round(hours)
    if rounded >= 1 and abs(hours - rounded) <= 0.35:
        unit = "hour" if rounded == 1 else "hours"
        return f"{rounded} {unit}"
    return format_duration(seconds)


def _date_label(window_start: datetime, window_end: datetime) -> str:
    start = window_start.astimezone(EASTERN)
    end = (window_end - timedelta(seconds=1)).astimezone(EASTERN)
    if start.date() == end.date():
        return f"{start.strftime('%b')} {start.day}"
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}–{end.day}"
    return f"{start.strftime('%b')} {start.day}–{end.strftime('%b')} {end.day}"


def _busiest_stretch(slices: list[dict], window_start: datetime, window_end: datetime):
    if not slices:
        return None
    step = timedelta(minutes=10)
    samples: list[tuple[datetime, int]] = []
    moment = window_start
    parsed_slices = [
        (
            datetime.fromisoformat(item["slice_started_at"]),
            datetime.fromisoformat(item["slice_ended_at"]),
            item["player_name"],
        )
        for item in slices
    ]
    while moment < window_end:
        online = {
            name
            for start, end, name in parsed_slices
            if start <= moment < end
        }
        samples.append((moment, len(online)))
        moment += step
    if not samples:
        return None
    peak = max(count for _, count in samples)
    if peak < 3:
        return None
    threshold = max(3, peak - 1)
    best: tuple[timedelta, datetime, datetime, int, int] | None = None
    run_start: datetime | None = None
    run_low = run_high = 0
    for moment, count in samples + [(window_end, 0)]:
        if count >= threshold:
            if run_start is None:
                run_start = moment
                run_low = run_high = count
            else:
                run_low = min(run_low, count)
                run_high = max(run_high, count)
        elif run_start is not None:
            span = moment - run_start
            if span >= timedelta(hours=1) and (best is None or span > best[0]):
                best = (span, run_start, moment, run_low, run_high)
            run_start = None
    if best is None:
        return None
    _span, start, end, low, high = best
    # Begin when the stretch actually hit its peak, if that still covers an hour.
    # A long warm-up at one fewer player would otherwise pull the label earlier.
    for moment, count in samples:
        if moment < start or moment >= end:
            continue
        if count == high:
            if end - moment >= timedelta(hours=1):
                start = moment
                counted = [value for when, value in samples if start <= when < end]
                low, high = min(counted), max(counted)
            break
    start_label, end_label = _hour_span_label(start, end)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "start_label": start_label,
        "end_label": end_label,
        "low": low,
        "high": high,
        "mostly_nether": False,
    }


def _death_cluster(deaths: list[dict]) -> dict | None:
    ordered = sorted(deaths, key=lambda item: item["at"])
    if len(ordered) < 5:
        return None
    best: tuple[int, int, int] | None = None
    for left, first in enumerate(ordered):
        first_at = datetime.fromisoformat(first["at"])
        for right in range(left, len(ordered)):
            last_at = datetime.fromisoformat(ordered[right]["at"])
            if last_at - first_at > timedelta(minutes=45):
                break
            count = right - left + 1
            if best is None or count > best[0]:
                best = (count, left, right)
    if best is None or best[0] < 5 or best[0] * 2 < len(ordered):
        return None
    _count, left, right = best
    group = ordered[left : right + 1]
    mention_counts: dict[str, int] = {}
    mention_order: list[str] = []
    for item in group:
        for cause in _death_mentions(item["message"]):
            if cause not in mention_counts:
                mention_order.append(cause)
            mention_counts[cause] = mention_counts.get(cause, 0) + 1
    causes = sorted(
        mention_order,
        key=lambda cause: (-mention_counts[cause], mention_order.index(cause)),
    )[:3]
    by_player: dict[str, int] = {}
    for item in group:
        by_player[item["player"]] = by_player.get(item["player"], 0) + 1
    players = [
        {"name": name, "count": count}
        for name, count in sorted(by_player.items(), key=lambda pair: (-pair[1], pair[0].lower()))
        if count >= 3
    ]
    if not players:
        players = [
            {"name": name, "count": count}
            for name, count in sorted(by_player.items(), key=lambda pair: (-pair[1], pair[0].lower()))
        ][:2]
    start = datetime.fromisoformat(group[0]["at"])
    end = datetime.fromisoformat(group[-1]["at"])
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "start_label": _clock_label(start),
        "end_label": _clock_label(end),
        "players": players,
        "causes": causes[:3],
        "indexes": {id(item) for item in group},
    }


def _overlaps(start_iso: str, end_iso: str, other_start_iso: str, other_end_iso: str) -> bool:
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso)
    other_start = datetime.fromisoformat(other_start_iso)
    other_end = datetime.fromisoformat(other_end_iso)
    return start < other_end and other_start < end


def _cluster_is_nether(cluster: dict, deaths: list[dict], logins, advancements: list[dict]) -> bool:
    if any(cause in _NETHER_PROOF for cause in cluster["causes"]):
        return True
    start = datetime.fromisoformat(cluster["start"]) - timedelta(minutes=30)
    end = datetime.fromisoformat(cluster["end"]) + timedelta(minutes=30)
    if any(start <= login.at <= end and login.dimension.endswith("the_nether") for login in logins):
        return True
    for item in advancements:
        moment = datetime.fromisoformat(item["at"])
        if start <= moment <= end and item["title"] in _NETHER_TITLES:
            return True
    return any(
        _death_cause(item["message"]) in _NETHER_CAUSES and id(item) in cluster["indexes"]
        for item in deaths
    ) and any(cause in _NETHER_PROOF for cause in cluster["causes"])


def _advancement_highlights(players: list[dict], advancements: list[dict], slices: list[dict]) -> dict:
    by_player: dict[str, list[dict]] = {}
    for item in advancements:
        by_player.setdefault(item["player"], []).append(item)
    records = []
    for player in players:
        items = by_player.get(player["name"], [])
        if not items:
            continue
        items = sorted(items, key=lambda item: item["at"])
        titles = []
        for item in items:
            if item["title"] not in titles:
                titles.append(item["title"])
        player_slices = [item for item in slices if item["player_name"] == player["name"]]
        one_session = False
        first_session = False
        if player_slices:
            def _covers(sl: dict) -> bool:
                return all(
                    datetime.fromisoformat(sl["slice_started_at"])
                    <= datetime.fromisoformat(adv["at"])
                    < datetime.fromisoformat(sl["slice_ended_at"])
                    for adv in items
                )

            one_session = any(_covers(sl) for sl in player_slices)
            earliest = min(player_slices, key=lambda sl: sl["slice_started_at"])
            first_session = _covers(earliest)
        notable = None
        for title in reversed(titles):
            others = [
                name
                for name, owned in by_player.items()
                if name != player["name"] and any(adv["title"] == title for adv in owned)
            ]
            if not others:
                notable = title
                break
        records.append(
            {
                "names": [player["name"]],
                "count": len(items),
                "span": [titles[0], titles[-1]] if len(titles) >= 4 and titles[0] != titles[-1] else [],
                "one_session": one_session and len(items) >= 4,
                "first_session": first_session and len(items) >= 4,
                "notable": notable if len(items) >= 4 else None,
            }
        )
    records.sort(key=lambda item: (-item["count"], item["names"][0].lower()))

    bursts = []
    by_title: dict[str, list[dict]] = {}
    for item in advancements:
        by_title.setdefault(item["title"], []).append(item)
    for title, items in by_title.items():
        items = sorted(items, key=lambda item: item["at"])
        names: list[str] = []
        first = datetime.fromisoformat(items[0]["at"])
        last = first
        kind = items[0]["kind"]
        for item in items:
            moment = datetime.fromisoformat(item["at"])
            if names and moment - first > timedelta(minutes=2):
                break
            if item["player"] not in names:
                names.append(item["player"])
            last = moment
            kind = item["kind"]
        close_together = int((last - first).total_seconds()) <= 20
        if len(names) >= 3 or (len(names) >= 2 and close_together and kind == "challenge"):
            bursts.append(
                {
                    "title": title,
                    "kind": kind,
                    "players": names,
                    "at_label": _clock_label(first),
                    "seconds_apart": int((last - first).total_seconds()),
                }
            )
    bursts.sort(key=lambda item: (-len(item["players"]), item["title"]))

    shared = []
    burst_covered = set()
    for burst in bursts:
        everyone = {
            item["player"]
            for item in by_title.get(burst["title"], [])
        }
        if set(burst["players"]) >= everyone and len(burst["players"]) >= 3:
            burst_covered.add(burst["title"])
    for title, items in by_title.items():
        names = []
        for item in items:
            if item["player"] not in names:
                names.append(item["player"])
        if len(names) >= 3 and title not in burst_covered:
            shared.append({"title": title, "count": len(names)})
    shared.sort(key=lambda item: (-item["count"], item["title"]))

    return {
        "total": len(advancements),
        "groups": records,
        "bursts": bursts[:2],
        "shared": shared[:2],
        "none": [
            player["name"]
            for player in players
            if player.get("no_advancements") and not player.get("missed_advancements")
        ],
        "missed": [
            {"name": player["name"], "titles": list(player.get("missed_advancements") or [])}
            for player in players
            if player.get("missed_advancements")
        ],
    }


def _server_highlights(entity_deaths: list[EntityDeath], prelogins: list[Prelogin]) -> list[dict]:
    notes: list[dict] = []
    squished = [
        death
        for death in entity_deaths
        if "squish" in death.message.lower() or "cram" in death.message.lower()
    ]
    if len(squished) >= ENTITY_NOTE_MIN_COUNT:
        placed = [death for death in squished if death.x is not None and death.z is not None]
        where = ""
        if len(placed) >= ENTITY_NOTE_MIN_COUNT:
            xs = sorted(death.x for death in placed if death.x is not None)
            zs = sorted(death.z for death in placed if death.z is not None)
            mid_x = round(xs[len(xs) // 2])
            mid_z = round(zs[len(zs) // 2])
            where = f" near x {mid_x}, z {mid_z}"
        if where:
            text = (
                f"Villagers kept dying from overcrowding{where} — "
                f"squished {len(squished)} times."
            )
        else:
            text = f"Villagers were squished {len(squished)} times."
        notes.append({"kind": "villagers", "text": text})
    attempts: dict[str, int] = {}
    for attempt in prelogins:
        attempts[attempt.name] = attempts.get(attempt.name, 0) + 1
    scanners = sorted(
        ((name, count) for name, count in attempts.items() if count >= 5),
        key=lambda pair: (-pair[1], pair[0].lower()),
    )
    if scanners:
        shown = scanners[:3]
        if len(shown) == 1:
            name, count = shown[0]
            notes.append(
                {
                    "kind": "scanner",
                    "text": (
                        f"{name} tried to connect {count} times and never got in. "
                        "Looks like a scanner."
                    ),
                }
            )
        else:
            names = ", ".join(name for name, _count in shown)
            notes.append(
                {
                    "kind": "scanner",
                    "text": (
                        f"A few unknown names, like {names}, tried to connect "
                        "and never got in. They look like scanners."
                    ),
                }
            )
    return notes


def _sync_highlight_people(stats: dict) -> None:
    highlights = stats.get("highlights")
    if not highlights:
        return
    by_name = {player["name"]: player for player in stats["players"]}
    for row in highlights.get("time_played") or []:
        player = by_name.get(row["name"])
        if player:
            row["new_player"] = bool(player.get("new_player"))
    advancements = highlights.setdefault("advancements", {})
    advancements["missed"] = [
        {"name": player["name"], "titles": list(player.get("missed_advancements") or [])}
        for player in stats["players"]
        if player.get("missed_advancements")
    ]
    advancements["none"] = [
        player["name"]
        for player in stats["players"]
        if player.get("no_advancements") and not player.get("missed_advancements")
    ]


def build_highlights(
    *,
    players: list[dict],
    slices: list[dict],
    deaths: list[dict],
    advancements: list[dict],
    entity_deaths: list[EntityDeath],
    logins: list[DimensionLogin],
    prelogins: list[Prelogin],
    window_start: datetime,
    window_end: datetime,
) -> dict:
    """The few facts worth saying: totals, the busy hour, the death story."""
    play_seconds = sum(player["playtime_seconds"] for player in players)
    busiest = _busiest_stretch(slices, window_start, window_end)
    cluster = _death_cluster(deaths)
    nether = False
    if cluster is not None:
        nether = _cluster_is_nether(cluster, deaths, logins, advancements)
        if busiest is not None and _overlaps(
            busiest["start"], busiest["end"], cluster["start"], cluster["end"]
        ):
            busiest["mostly_nether"] = nether
        cluster = {key: value for key, value in cluster.items() if key != "indexes"}

    in_cluster: set[int] = set()
    cluster_names = {row["name"] for row in (cluster or {}).get("players", [])}
    if cluster is not None:
        start = datetime.fromisoformat(cluster["start"])
        end = datetime.fromisoformat(cluster["end"])
        in_cluster = {
            id(item)
            for item in deaths
            if start <= datetime.fromisoformat(item["at"]) <= end
        }

    others_map: dict[str, list[str]] = {}
    for item in deaths:
        if id(item) in in_cluster and item["player"] in cluster_names:
            continue
        others_map.setdefault(item["player"], []).append(_death_cause(item["message"]))
    others = [
        {
            "name": name,
            "count": len(causes),
            "details": causes[:3],
            "follow_up": name in cluster_names,
        }
        for name, causes in others_map.items()
    ]
    others.sort(key=lambda item: (-item["count"], item["name"].lower()))

    time_played = []
    for player in players:
        player_slices = [item for item in slices if item["player_name"] == player["name"]]
        # A session clipped by midnight is not "still online" in yesterday's recap.
        still_online = any(
            item["end_reason"] == "still_online" and item["slice_ended_at"] == item["ended_at"]
            for item in player_slices
        )
        afk = None
        long_slices = [item for item in player_slices if item["likely_afk"]]
        if long_slices and player.get("mostly_afk"):
            longest = max(long_slices, key=lambda item: item["full_seconds"])
            started = datetime.fromisoformat(longest["started_at"])
            afk = {
                "duration": format_duration(longest["full_seconds"]),
                "since": _clock_label(started),
            }
        time_played.append(
            {
                "name": player["name"],
                "playtime": player["playtime"],
                "sessions": player["sessions"],
                "still_online": still_online,
                "afk": afk,
                "new_player": bool(player.get("new_player")),
            }
        )

    return {
        "player_count": len(players),
        "combined": _combined_label(play_seconds),
        "date_label": _date_label(window_start, window_end),
        "busiest": busiest,
        "time_played": time_played,
        "deaths": {
            "total": len(deaths),
            "nether": nether,
            "cluster": cluster,
            "others": others,
        },
        "advancements": _advancement_highlights(players, advancements, slices),
        "server_notes": _server_highlights(entity_deaths, prelogins),
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
