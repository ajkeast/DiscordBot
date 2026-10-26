"""Daily Minecraft recap: persist parsed stats and ask Grok for the post.

Stats are computed in :mod:`utils.minecraft_log`. Grok only phrases the JSON
it is given. If the API key is missing or the reply is unusable, a template
is used instead. Posting is idempotent via ``minecraft_daily_posts``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from xai_sdk import Client
from xai_sdk.chat import system, user

from utils.constants import MINECRAFT_CHANNEL_ID
from utils.db import db_ops
from utils.minecraft_log import (
    EASTERN,
    MinecraftLogError,
    apply_world_files,
    parse_data_dir,
    summarize_day,
)

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_MINECRAFT_MODEL = "grok-4.7"
DEFAULT_DATA_DIR = "/minecraft"
MAX_NARRATIVE_CHARS = 1800
GROK_ATTEMPTS = 2

# Additive only. Never ALTER or DROP tables that already exist.
SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS minecraft_sessions (
        id BIGSERIAL PRIMARY KEY,
        activity_date DATE NOT NULL,
        player_name TEXT NOT NULL,
        player_uuid TEXT,
        started_at TIMESTAMPTZ NOT NULL,
        ended_at TIMESTAMPTZ NOT NULL,
        slice_started_at TIMESTAMPTZ NOT NULL,
        slice_ended_at TIMESTAMPTZ NOT NULL,
        duration_seconds INTEGER NOT NULL CHECK (duration_seconds > 0),
        end_reason TEXT NOT NULL CHECK (
            end_reason IN ('leave', 'server_stop', 'rejoin', 'still_online')
        ),
        likely_afk BOOLEAN NOT NULL,
        UNIQUE (player_name, started_at, activity_date)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS minecraft_sessions_date_player_idx
        ON minecraft_sessions (activity_date, player_name)
    """,
    """
    CREATE TABLE IF NOT EXISTS minecraft_deaths (
        id BIGSERIAL PRIMARY KEY,
        activity_date DATE NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL,
        player_name TEXT NOT NULL,
        player_uuid TEXT,
        message TEXT NOT NULL,
        UNIQUE (player_name, occurred_at, message)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS minecraft_deaths_date_idx
        ON minecraft_deaths (activity_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS minecraft_advancements (
        id BIGSERIAL PRIMARY KEY,
        activity_date DATE NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL,
        player_name TEXT NOT NULL,
        player_uuid TEXT,
        kind TEXT NOT NULL CHECK (kind IN ('advancement', 'challenge', 'goal')),
        title TEXT NOT NULL,
        UNIQUE (player_name, occurred_at, kind, title)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS minecraft_advancements_date_idx
        ON minecraft_advancements (activity_date)
    """,
    """
    CREATE TABLE IF NOT EXISTS minecraft_daily_posts (
        activity_date DATE PRIMARY KEY,
        channel_id BIGINT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'posted')),
        claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        posted_at TIMESTAMPTZ,
        message_id BIGINT,
        nobody_played BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
)

SYSTEM_PROMPT = """You write the daily Minecraft recap for a small Discord server.
You are given JSON of highlights computed from the logs. Use only that JSON.
Write the way a friend would recap the night: a couple of sentences of context, then short sections. A list of every death or every title is not what anyone wants.

Sections, with a blank line between them:
1. Opening: how many players, the combined string, and the busiest stretch when busiest is set. Use its start_label and end_label. Say people were mostly in the Nether only when busiest.mostly_nether is true.
2. Time played — a heading, then one line per person. Use the playtime string and session count. Mention still online, the AFK stretch, or a welcome only when those fields say so.
3. Deaths (the total) — skip when the total is 0. When deaths.nether is true, open with the Nether trip going badly. Then one sentence for the cluster: who died how many times, between the two clock labels, mostly to the causes, in that order. Then one short sentence for deaths.others. A follow_up row is a later death by someone already in the cluster, not another count inside that window.
4. Advancements (the total) — spotlight one single-session run when a group has one_session and a span, from the first title to the last. Call it their first session when first_session is true. Spotlight one other unique title with "including" when notable is set. Lump every remaining group into a single sentence by count. Then one line per burst and per shared title. One line for anyone in advancements.none. Say someone missed a title only when advancements.missed lists it, as "Hey, Name, I saw you missed Title."
5. Server notes — the note text you were given. Omit the section when server_notes is empty.

Do not invent players, times, places, dimensions, or causes. Do not say the Nether unless those flags are true. Discord markdown. No code fence. Stay under 1600 characters.
"""


@dataclass
class PreparedPost:
    stats: dict
    narrative: str
    source: str
    nobody_played: bool
    persisted: bool


def minecraft_model() -> str:
    raw = os.getenv("GROK_MINECRAFT_MODEL", "").strip()
    return raw or DEFAULT_MINECRAFT_MODEL


def minecraft_grok_enabled() -> bool:
    return bool(os.getenv("XAI_API_KEY", "").strip())


def minecraft_channel_id() -> int:
    raw = os.getenv("MINECRAFT_CHANNEL_ID", "").strip()
    if not raw:
        return MINECRAFT_CHANNEL_ID
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid MINECRAFT_CHANNEL_ID=%r; using %s",
            raw,
            MINECRAFT_CHANNEL_ID,
        )
        return MINECRAFT_CHANNEL_ID
    if value <= 0:
        logger.warning(
            "Invalid MINECRAFT_CHANNEL_ID=%s; using %s",
            value,
            MINECRAFT_CHANNEL_ID,
        )
        return MINECRAFT_CHANNEL_ID
    return value


def minecraft_data_dir() -> Path:
    raw = os.getenv("MINECRAFT_DATA_DIR", DEFAULT_DATA_DIR).strip()
    return Path(raw or DEFAULT_DATA_DIR)


def activity_date_due(now: datetime) -> date | None:
    """Yesterday's summary is due at and after 08:00 America/New_York.

    Returns None before 08:00 so a restart early in the morning waits for the
    scheduled run. After 08:00, the same calendar day's catch-up may post if
    the ledger does not already show a post.
    """
    local = now.astimezone(EASTERN)
    if (local.hour, local.minute) < (8, 0):
        return None
    return local.date() - timedelta(days=1)


def yesterday_et(now: datetime) -> date:
    return now.astimezone(EASTERN).date() - timedelta(days=1)


def ensure_minecraft_tables() -> None:
    for statement in SCHEMA_STATEMENTS:
        db_ops.db.execute(statement)


def _dedupe(rows: list[tuple], key_indexes: tuple[int, ...]) -> list[tuple]:
    seen: set[tuple] = set()
    unique: list[tuple] = []
    for row in rows:
        key = tuple(row[index] for index in key_indexes)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def persist_activity(stats: dict) -> None:
    """Replace one calendar day's rows. Safe to run again for the same date."""
    ensure_minecraft_tables()
    day = date.fromisoformat(stats["date"])
    session_rows = _dedupe(
        [
            (
                day,
                item["player_name"],
                item.get("player_uuid"),
                datetime.fromisoformat(item["started_at"]),
                datetime.fromisoformat(item["ended_at"]),
                datetime.fromisoformat(item["slice_started_at"]),
                datetime.fromisoformat(item["slice_ended_at"]),
                int(item["duration_seconds"]),
                item["end_reason"],
                bool(item["likely_afk"]),
            )
            for item in stats["slices"]
        ],
        (0, 1, 3),
    )
    death_rows = _dedupe(
        [
            (
                day,
                datetime.fromisoformat(item["at"]),
                item["player"],
                item.get("uuid"),
                item["message"],
            )
            for item in stats["deaths"]
        ],
        (0, 2, 1, 4),
    )
    advancement_rows = _dedupe(
        [
            (
                day,
                datetime.fromisoformat(item["at"]),
                item["player"],
                item.get("uuid"),
                item["kind"],
                item["title"],
            )
            for item in stats["advancements"]
        ],
        (0, 2, 1, 4, 5),
    )

    with db_ops.db.cursor() as cursor:
        cursor.execute("DELETE FROM minecraft_sessions WHERE activity_date = %s", (day,))
        cursor.execute("DELETE FROM minecraft_deaths WHERE activity_date = %s", (day,))
        cursor.execute("DELETE FROM minecraft_advancements WHERE activity_date = %s", (day,))
        if session_rows:
            cursor.executemany(
                """
                INSERT INTO minecraft_sessions (
                    activity_date, player_name, player_uuid, started_at, ended_at,
                    slice_started_at, slice_ended_at, duration_seconds, end_reason, likely_afk
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::boolean)
                """,
                session_rows,
            )
        if death_rows:
            cursor.executemany(
                """
                INSERT INTO minecraft_deaths (
                    activity_date, occurred_at, player_name, player_uuid, message
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                death_rows,
            )
        if advancement_rows:
            cursor.executemany(
                """
                INSERT INTO minecraft_advancements (
                    activity_date, occurred_at, player_name, player_uuid, kind, title
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                advancement_rows,
            )


def claim_daily_post(activity_date: date, channel_id: int) -> str:
    """Claim the day's post.

    Returns ``claimed`` when this run should post, or ``already_posted`` when
    a previous run finished. A leftover ``pending`` row (crash after the claim)
    is reclaimed so a restart can still deliver the message.
    """
    ensure_minecraft_tables()
    with db_ops.db.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM minecraft_daily_posts WHERE activity_date = %s",
            (activity_date,),
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                """
                INSERT INTO minecraft_daily_posts (
                    activity_date, channel_id, status, claimed_at, nobody_played
                ) VALUES (%s, %s, 'pending', NOW(), FALSE)
                """,
                (activity_date, channel_id),
            )
            return "claimed"
        if row[0] == "posted":
            return "already_posted"
        cursor.execute(
            """
            UPDATE minecraft_daily_posts
            SET status = 'pending',
                channel_id = %s,
                claimed_at = NOW(),
                posted_at = NULL,
                message_id = NULL
            WHERE activity_date = %s AND status = 'pending'
            """,
            (channel_id, activity_date),
        )
        if cursor.rowcount == 0:
            return "already_posted"
        return "claimed"


def release_claim(activity_date: date) -> None:
    """Drop a pending claim so a failed send can be retried."""
    db_ops.db.execute(
        """
        DELETE FROM minecraft_daily_posts
        WHERE activity_date = %s AND status = 'pending'
        """,
        (activity_date,),
    )


def mark_posted(
    activity_date: date,
    channel_id: int,
    message_id: int | None,
    nobody_played: bool,
) -> None:
    db_ops.db.execute(
        """
        UPDATE minecraft_daily_posts
        SET status = 'posted',
            channel_id = %s,
            posted_at = NOW(),
            message_id = %s,
            nobody_played = %s::boolean
        WHERE activity_date = %s
        """,
        (channel_id, message_id, nobody_played, activity_date),
    )


def build_day_stats(
    day: date,
    *,
    data_dir: Path | None = None,
    as_of: datetime | None = None,
) -> dict:
    root = data_dir if data_dir is not None else minecraft_data_dir()
    if not root.exists():
        raise MinecraftLogError(f"Minecraft data directory does not exist: {root}")
    parsed = parse_data_dir(root, as_of=as_of)
    stats = summarize_day(parsed, day, as_of=as_of)
    apply_world_files(stats, parsed, root)
    logger.info(
        "Minecraft %s: %s players, %s sessions, %s deaths, %s advancements",
        day.isoformat(),
        stats["totals"]["players"],
        stats["totals"]["sessions"],
        stats["totals"]["deaths"],
        stats["totals"]["advancements"],
    )
    return stats


def grok_payload(stats: dict) -> dict:
    """Highlights the model may phrase. No raw slices or per-death logs."""
    highlights = stats.get("highlights") or _minimal_highlights(stats)
    return {
        "date": stats.get("pretty_date"),
        "partial_day": bool(stats.get("partial_day")),
        "highlights": highlights,
    }


def nobody_line(stats: dict) -> str:
    return f"Nobody played Minecraft on {stats['pretty_date']}."


# Words that show the reply actually used a death message, not just a count.
_DEATH_HINTS = (
    "lava",
    "slain",
    "fell",
    "blown",
    "creeper",
    "zombie",
    "ghast",
    "piglin",
    "magma",
    "doomed",
    "drowned",
    "burned",
    "burnt",
    "shot",
    "fireball",
    "void",
    "cactus",
    "anvil",
    "suffocat",
    "starv",
    "pricked",
    "frozen",
    "lightning",
    "wither",
    "explosion",
    "crammed",
    "kinetic",
    "skewered",
    "impaled",
    "sting",
    "arrow",
    "trident",
    "shriek",
    "berry",
)

_NUDGE_MARKERS = (
    "no advancement",
    "didn't earn",
    "did not earn",
    "without an advancement",
    "zero advancement",
    "missed",
    "place to start",
)


def _death_fact_present(text: str, stats: dict) -> bool:
    blob = " ".join(item["message"].lower() for item in stats.get("deaths") or [])
    if not blob:
        return True
    lowered = text.lower()
    hints = [hint for hint in _DEATH_HINTS if hint in blob]
    if any(hint in lowered for hint in hints):
        return True
    words = re.findall(r"[a-z]{5,}", blob)
    return any(word in lowered for word in words)


def narrative_ok(text: str, stats: dict) -> bool:
    """Accept a reply that names everyone and uses the deaths and titles."""
    cleaned = text.strip()
    if not cleaned or len(cleaned) > MAX_NARRATIVE_CHARS:
        return False
    for player in stats["players"]:
        if player["name"] not in cleaned:
            return False
    if stats["totals"]["deaths"] and not _death_fact_present(cleaned, stats):
        return False
    titles = [item["title"] for item in stats.get("advancements") or [] if item.get("title")]
    if titles and not any(title.lower() in cleaned.lower() for title in titles):
        return False
    lowered = cleaned.lower()
    if stats["players"] and "time played" not in lowered:
        return False
    if (stats.get("highlights") or {}).get("deaths", {}).get("nether") and "nether" not in lowered:
        return False
    if any(player.get("no_advancements") for player in stats["players"]):
        if not any(marker in lowered for marker in _NUDGE_MARKERS):
            return False
    if any(player.get("new_player") for player in stats["players"]):
        if "welcome" not in lowered:
            return False
    return True


def clean_model_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:\w+)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _md(text: str) -> str:
    """Keep player names and log phrases from breaking Discord markdown."""
    return (
        text.replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("`", "\\`")
    )


def _and_join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _people_phrase(low: int, high: int) -> str:
    words = {
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

    def word(count: int) -> str:
        return words.get(count, str(count))

    if low == high:
        return word(low)
    if high - low == 1:
        return f"{word(low)} or {word(high)}"
    return f"as many as {word(high)}"


def _minimal_highlights(stats: dict) -> dict:
    """Section facts for a hand-built day that never went through the parser."""
    players = stats.get("players") or []
    deaths = stats.get("deaths") or []
    advancements = stats.get("advancements") or []
    by_player: dict[str, list[str]] = {}
    for item in deaths:
        by_player.setdefault(item["player"], []).append(item["message"])
    groups = []
    for player in players:
        titles = list(player.get("advancement_titles") or [])
        if not titles:
            titles = [
                item["title"]
                for item in advancements
                if item.get("player") == player["name"]
            ]
        if titles:
            groups.append(
                {
                    "names": [player["name"]],
                    "count": player.get("advancements") or len(titles),
                    "span": [titles[0], titles[-1]] if len(titles) >= 2 else [],
                    "one_session": False,
                    "notable": None,
                }
            )
    notes = []
    for note in stats.get("server_notes") or []:
        if note.get("text"):
            notes.append(note)
        elif note.get("entity"):
            notes.append(
                {
                    "kind": "entity",
                    "text": f"{note['entity']} {note['message']} ×{note['count']}.",
                }
            )
    return {
        "player_count": len(players),
        "combined": None,
        "date_label": stats.get("pretty_date") or "",
        "busiest": None,
        "time_played": [
            {
                "name": player["name"],
                "playtime": player["playtime"],
                "sessions": player["sessions"],
                "still_online": False,
                "afk": (
                    {"duration": player.get("longest_session"), "since": None}
                    if player.get("likely_afk")
                    else None
                ),
                "new_player": bool(player.get("new_player")),
            }
            for player in players
        ],
        "deaths": {
            "total": (stats.get("totals") or {}).get("deaths", len(deaths)),
            "nether": False,
            "cluster": None,
            "others": [
                {"name": name, "count": len(messages), "details": messages[:3]}
                for name, messages in by_player.items()
            ],
        },
        "advancements": {
            "total": (stats.get("totals") or {}).get("advancements", len(advancements)),
            "groups": groups,
            "bursts": [],
            "shared": [],
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
        },
        "server_notes": notes,
    }


def _pretty_duration(text: str) -> str:
    """Turn ``17h 53m`` into ``17 h 53 m`` so a line can be read out loud."""
    return re.sub(r"(\d+)([hms])", r"\1 \2", text)


def _stretch_phrase(start_label: str, end_label: str) -> str:
    """``8 PM`` to ``11 PM`` reads better as ``8 to 11 PM``."""
    for suffix in (" AM", " PM"):
        if start_label.endswith(suffix) and end_label.endswith(suffix):
            return f"{start_label[: -len(suffix)]} to {end_label}"
    return f"{start_label} to {end_label}"


def _time_line(row: dict) -> str:
    name = f"**{_md(row['name'])}**"
    noun = "session" if row["sessions"] == 1 else "sessions"
    bridge = "in" if row["sessions"] == 1 else "over"
    played = _pretty_duration(row["playtime"])
    line = f"{name}, {played} {bridge} {row['sessions']} {noun}"
    if row.get("still_online"):
        line += ", and still online"
    line += "."
    afk = row.get("afk")
    if afk and afk.get("duration"):
        since = f" since {afk['since']}" if afk.get("since") else ""
        duration = _pretty_duration(afk["duration"])
        line += f" That includes {duration} straight{since}, so it's likely AFK."
    if row.get("new_player"):
        line += " Welcome, first time on the server."
    return line


def _cluster_sentence(cluster: dict) -> str:
    bits = []
    for index, row in enumerate(cluster["players"]):
        if index == 0:
            bits.append(f"{_md(row['name'])} died {row['count']} times")
        else:
            bits.append(f"{_md(row['name'])} {row['count']} times")
    who = _and_join(bits)
    causes = [_md(cause) for cause in cluster.get("causes") or []]
    cause_bit = f", mostly to {_and_join(causes)}" if causes else ""
    return (
        f"{who} between {cluster['start_label']} and {cluster['end_label']}{cause_bit}."
    )


def _other_bit(row: dict) -> str:
    name = _md(row["name"])
    count = row["count"]
    details = [_md(item) for item in row.get("details") or []]
    if row.get("follow_up") and count == 1 and details == ["a fall"]:
        return f"{name} fell once more later"
    if row.get("follow_up") and count == 1 and details:
        return f"{name} also died once later ({details[0]})"
    if row.get("follow_up") and count == 1:
        return f"{name} died once more later"
    if count == 1 and details == ["a Creeper"]:
        return f"{name} was blown up by a Creeper once"
    if count == 1 and details:
        return f"{name} died once ({details[0]})"
    if count == 2 and len(details) >= 2:
        if details[0].startswith(("a ", "an ")):
            return f"{name} died twice (to {details[0]}, then {details[1]})"
        return f"{name} died twice ({details[0]}, then {details[1]})"
    if details:
        return f"{name} died {count} times ({_and_join(details)})"
    return f"{name} died {count} times"


def _lumped_groups(groups: list[dict]) -> str:
    """One sentence for everyone who doesn't get their own story line."""
    buckets: list[tuple[int, list[str]]] = []
    for group in groups:
        names = list(group["names"])
        if buckets and buckets[-1][0] == group["count"]:
            buckets[-1][1].extend(names)
        else:
            buckets.append((group["count"], names))
    parts = []
    for count, names in buckets:
        shown = [_md(name) for name in names]
        if len(shown) == 1:
            parts.append(f"{shown[0]} got {count}")
        else:
            parts.append(f"{_and_join(shown)} got {count} each")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0] + "."
    return ", ".join(parts[:-1]) + ", and " + parts[-1] + "."


def _group_line(group: dict) -> str:
    names = [_md(name) for name in group["names"]]
    count = group["count"]
    if len(names) == 1:
        who = f"{names[0]} got {count}"
    elif len(names) == 2:
        who = f"{names[0]} and {names[1]} got {count} each"
    else:
        who = f"{_and_join(names)} got {count} each"
    if group.get("first_session"):
        who += " in their first session"
    elif group.get("one_session"):
        who += " in one session"
    span = group.get("span") or []
    if len(span) == 2:
        who += f", from {_md(span[0])} to {_md(span[1])}"
    elif group.get("notable"):
        who += f", including {_md(group['notable'])}"
    if who.endswith(("?", "!", ".")):
        return who
    return who + "."


def _burst_line(burst: dict) -> str:
    names = [_md(name) for name in burst["players"]]
    title = _md(burst["title"])
    when = burst["at_label"]
    if burst.get("seconds_apart", 999) <= 15:
        pace = "within seconds of each other"
    else:
        pace = "within a couple of minutes"
    kind = "challenge" if burst.get("kind") == "challenge" else "advancement"
    return f"{_and_join(names)} all finished the {title} {kind} {pace} at {when}."


def _render_highlights(stats: dict, highlights: dict) -> str:
    sections: list[str] = []
    overview = ""
    if highlights.get("combined"):
        overview = (
            f"**{highlights['player_count']} players** were on for a combined "
            f"{highlights['combined']}."
        )
    busiest = highlights.get("busiest")
    if busiest:
        people = _people_phrase(busiest["low"], busiest["high"])
        where = ", mostly in the Nether" if busiest.get("mostly_nether") else ""
        sentence = (
            f"The busiest stretch was {_stretch_phrase(busiest['start_label'], busiest['end_label'])}, "
            f"when {people} people were on together{where}."
        )
        overview = f"{overview} {sentence}".strip()
    if stats.get("partial_day"):
        overview = (overview + " " if overview else "") + "The day is still in progress."
    if overview:
        sections.append(overview.strip())

    played = "\n".join(_time_line(row) for row in highlights.get("time_played") or [])
    if played:
        sections.append("**Time played**\n" + played)

    deaths = highlights.get("deaths") or {}
    if deaths.get("total"):
        lines = [f"**Deaths ({deaths['total']})**"]
        story: list[str] = []
        if deaths.get("nether") and deaths.get("cluster"):
            story.append("The Nether trip went badly. " + _cluster_sentence(deaths["cluster"]))
        elif deaths.get("cluster"):
            story.append(_cluster_sentence(deaths["cluster"]))
        others = deaths.get("others") or []
        if others:
            story.append(_and_join([_other_bit(row) for row in others]) + ".")
        if story:
            lines.append(" ".join(story))
        sections.append("\n".join(lines))

    advancements = highlights.get("advancements") or {}
    if advancements.get("total") or advancements.get("none") or advancements.get("missed"):
        lines = [f"**Advancements ({advancements.get('total', 0)})**"]
        groups = list(advancements.get("groups") or [])
        arc = next(
            (
                group
                for group in groups
                if group.get("one_session") and len(group.get("span") or []) == 2
            ),
            None,
        )
        remaining = [group for group in groups if group is not arc]
        standout = next((group for group in remaining if group.get("notable")), None)
        remaining = [group for group in remaining if group is not standout]
        if arc is not None:
            lines.append(_group_line(arc))
        if standout is not None:
            lines.append(
                _group_line(
                    {
                        **standout,
                        "span": [],
                        "one_session": False,
                        "first_session": False,
                    }
                )
            )
        lumped = _lumped_groups(remaining)
        if lumped:
            lines.append(lumped)
        for burst in advancements.get("bursts") or []:
            lines.append(_burst_line(burst))
        for shared in advancements.get("shared") or []:
            people = _people_phrase(shared["count"], shared["count"])
            lines.append(f"{people.capitalize()} people reached {_md(shared['title'])}.")
        for missed in advancements.get("missed") or []:
            titles = [_md(title) for title in missed["titles"][:2]]
            lines.append(f"Hey, {_md(missed['name'])}, I saw you missed {_and_join(titles)}.")
        for name in advancements.get("none") or []:
            lines.append(f"{_md(name)} didn't earn an advancement.")
        sections.append("\n".join(lines))

    notes = highlights.get("server_notes") or []
    if notes:
        sections.append(
            "**Server notes**\n" + "\n".join(note["text"] for note in notes)
        )
    return "\n\n".join(section for section in sections if section)


def render_template(stats: dict) -> str:
    """Sectioned recap used when Grok is unavailable. Stays under Discord's limit."""
    if stats["nobody_played"]:
        return nobody_line(stats)
    highlights = stats.get("highlights") or _minimal_highlights(stats)
    text = _render_highlights(stats, highlights)
    if len(text) > 1900:
        text = text[:1890].rstrip() + "…"
    return text


def ask_grok(stats: dict, *, client: Client | None = None, model: str | None = None) -> str:
    """Ask Grok for a recap. Raises if the reply is missing or unusable."""
    api_key = os.getenv("XAI_API_KEY", "").strip()
    if not api_key and client is None:
        raise RuntimeError("XAI_API_KEY is required for the Minecraft narrative")
    if client is None:
        client = Client(api_key=api_key, timeout=90)
    model_name = model or minecraft_model()
    payload = json.dumps(grok_payload(stats), ensure_ascii=False)
    user_prompt = (
        "Write the Discord recap from this JSON only.\n\n"
        f"{payload}"
    )
    last_error: Exception | None = None
    for attempt in range(GROK_ATTEMPTS):
        try:
            # grok-4.7 reasons by default. "low" keeps this short recap cheap;
            # "none" is not in that model's documented efforts.
            chat = client.chat.create(
                model=model_name,
                temperature=0.3,
                reasoning_effort="low",
                store_messages=False,
            )
            chat.append(system(SYSTEM_PROMPT))
            chat.append(user(user_prompt))
            response = chat.sample()
            text = clean_model_text(response.content or "")
            if not narrative_ok(text, stats):
                raise ValueError("Grok narrative missing required facts or too long")
            return text
        except Exception as exc:  # noqa: BLE001 — retry, then the template takes over
            last_error = exc
            logger.warning(
                "Minecraft narrative attempt %s/%s failed: %s",
                attempt + 1,
                GROK_ATTEMPTS,
                exc,
            )
            if attempt + 1 < GROK_ATTEMPTS:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Minecraft narrative failed: {last_error}")


def render_narrative(stats: dict, *, client: Client | None = None) -> tuple[str, str]:
    """Return ``(text, source)`` where source is ``grok`` or ``template``."""
    if stats["nobody_played"]:
        return nobody_line(stats), "template"
    if minecraft_grok_enabled() or client is not None:
        try:
            return ask_grok(stats, client=client), "grok"
        except Exception:
            logger.exception("Minecraft Grok narrative failed; using the template")
    return render_template(stats), "template"


def prepare_post(
    day: date,
    *,
    as_of: datetime | None = None,
    data_dir: Path | None = None,
    client: Client | None = None,
    persist: str = "required",
) -> PreparedPost:
    """Parse one calendar day, optionally save it, and build the message.

    ``persist`` is ``required`` (daily job: a database error aborts the post)
    or ``best_effort`` (admin preview still returns the recap).
    """
    stats = build_day_stats(day, data_dir=data_dir, as_of=as_of)
    persisted = False
    try:
        persist_activity(stats)
        persisted = True
    except Exception:
        logger.exception("Failed to persist Minecraft activity for %s", day.isoformat())
        if persist == "required":
            raise
    narrative, source = render_narrative(stats, client=client)
    return PreparedPost(
        stats=stats,
        narrative=narrative,
        source=source,
        nobody_played=stats["nobody_played"],
        persisted=persisted,
    )


def embed_footer(stats: dict) -> str:
    footer = "Midnight to midnight America/New_York. Sessions over 6 hours are likely AFK."
    if stats.get("partial_day"):
        footer += " This day is still in progress."
    return footer


def summary_title(stats: dict) -> str:
    return f"Minecraft — {stats['pretty_date']}"
