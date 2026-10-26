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
    titles_earned_by_others,
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
You are given JSON computed from server logs and, when the world save was readable, from player stats and advancement files. Use only that JSON.
Write like a friend in the channel, briefly. One line per player. Tease a death, name an advancement, nudge someone who came up empty.

Rules:
- Do not invent players, deaths, advancements, places, times, or events.
- Mention every player. Use each playtime string and session count exactly as given.
- If likely_afk is true, say that long session looks AFK. Say "mostly" only when mostly_afk is true.
- Name at most two death_lines per player. Skip the death sentence when death_lines is empty.
- Name at most two advancement_titles per player. You may say there were more.
- If missed_advancements is non-empty, say you noticed they missed those exact titles. Those are the only titles you may call missed.
- If no_advancements is true and missed_advancements is empty, say they earned no advancement and point at one title someone else actually earned, as a place to start. Do not say they missed that title.
- If new_player is true, welcome them in a few words. Welcome nobody else.
- If server_notes is non-empty, add one short sentence. If it is empty, do not mention entities.
- If partial_day is true, say the day is still in progress.
- Close with the exact integers totals.deaths and totals.advancements.
- Discord markdown. No title line, no code fence, no preamble.
- Stay under 1000 characters.
"""


@dataclass
class PreparedPost:
    stats: dict
    narrative: str
    source: str
    nobody_played: bool
    persisted: bool


def minecraft_summary_enabled() -> bool:
    """On unless ``MINECRAFT_SUMMARY_ENABLED`` is false, 0, no, or off."""
    raw = os.getenv("MINECRAFT_SUMMARY_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


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
    """Facts the model is allowed to mention. No raw slices or timestamps."""
    return {
        "date": stats["pretty_date"],
        "timezone": stats["timezone"],
        "partial_day": stats["partial_day"],
        "new_players": list(stats.get("new_players") or []),
        "players": [
            {
                "name": player["name"],
                "playtime": player["playtime"],
                "sessions": player["sessions"],
                "longest_session": player["longest_session"],
                "likely_afk": player["likely_afk"],
                "mostly_afk": player["mostly_afk"],
                "deaths": player["deaths"],
                "death_lines": list(player.get("death_lines") or []),
                "advancements": player["advancements"],
                "advancement_titles": list(player.get("advancement_titles") or []),
                "no_advancements": bool(player.get("no_advancements")),
                "new_player": bool(player.get("new_player")),
                "missed_advancements": list(player.get("missed_advancements") or []),
            }
            for player in stats["players"]
        ],
        "totals": {
            "players": stats["totals"]["players"],
            "sessions": stats["totals"]["sessions"],
            "deaths": stats["totals"]["deaths"],
            "advancements": stats["totals"]["advancements"],
        },
        "death_counts": stats["death_counts"][:40],
        "advancements": [
            {"player": item["player"], "kind": item["kind"], "title": item["title"]}
            for item in stats["advancements"][:60]
        ],
        "server_notes": stats["server_notes"],
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


def _death_lines(player: dict, stats: dict) -> list[str]:
    lines = player.get("death_lines")
    if lines:
        return list(lines)
    built: list[str] = []
    for item in stats.get("death_counts") or []:
        if item["player"] != player["name"]:
            continue
        count = int(item["count"])
        message = item["message"]
        built.append(message if count == 1 else f"{message} ×{count}")
    return built


def _advancement_titles(player: dict, stats: dict) -> list[str]:
    if "advancement_titles" in player:
        return list(player["advancement_titles"])
    seen: list[str] = []
    for item in stats.get("advancements") or []:
        if item["player"] == player["name"] and item["title"] not in seen:
            seen.append(item["title"])
    return seen


def _player_needs_nudge(player: dict) -> bool:
    if "no_advancements" in player:
        return bool(player["no_advancements"])
    return (
        player.get("advancements", 0) == 0
        and player.get("playtime_seconds", 0) >= 20 * 60
    )


def _compose_template(stats: dict, *, title_cap: int, death_cap: int) -> str:
    lines: list[str] = []
    if stats.get("partial_day"):
        lines.append("Still in progress.")

    for player in stats["players"]:
        name = f"**{_md(player['name'])}**"
        noun = "session" if player["sessions"] == 1 else "sessions"
        longest = player.get("longest_session")
        head = f"{name} — {player['playtime']}, {player['sessions']} {noun}"
        if player.get("mostly_afk") and longest:
            head += f", mostly one {longest} session, likely AFK"
        elif player.get("likely_afk") and longest:
            # The long session can span midnight, so its length may exceed this day's total.
            head += f", likely AFK ({longest} session)"
        sentences = [head + "."]
        if player.get("new_player"):
            sentences.append("Welcome, first time on the server.")

        death_lines = _death_lines(player, stats)
        death_count = int(player.get("deaths") or 0)
        if death_lines:
            shown = [_md(line) for line in death_lines[:death_cap]]
            if death_count == 1:
                sentences.append(f"Died once: {_and_join(shown)}.")
            else:
                sentences.append(f"Died {death_count} times: {_and_join(shown)}.")

        titles = _advancement_titles(player, stats)
        missed = [_md(title) for title in (player.get("missed_advancements") or [])[:2]]
        if titles:
            shown = [_md(title) for title in titles[:title_cap]]
            extra = len(titles) - len(shown)
            more = f", plus {extra} more" if extra else ""
            sentences.append(f"{_and_join(shown)}{more}.")
        elif missed:
            sentences.append(f"Hey, I saw you missed {_and_join(missed)}.")
        elif _player_needs_nudge(player):
            suggestion = titles_earned_by_others(stats, player["name"])
            if suggestion:
                sentences.append(f"No advancement. {_md(suggestion[0])} is a place to start.")
            else:
                sentences.append("No advancement.")
        lines.append(" ".join(sentences))

    for note in (stats.get("server_notes") or [])[:1]:
        lines.append(f"{_md(note['entity'])} {_md(note['message'])} ×{note['count']}.")

    totals = stats["totals"]
    death_word = "death" if totals["deaths"] == 1 else "deaths"
    adv_word = "advancement" if totals["advancements"] == 1 else "advancements"
    lines.append(f"{totals['deaths']} {death_word}, {totals['advancements']} {adv_word}.")
    return "\n".join(lines)


def render_template(stats: dict) -> str:
    """Prose recap used when Grok is unavailable. Stays under Discord's limit."""
    if stats["nobody_played"]:
        return nobody_line(stats)

    text = _compose_template(stats, title_cap=2, death_cap=2)
    if len(text) > 1900:
        text = _compose_template(stats, title_cap=2, death_cap=2)
    if len(text) > 1900:
        text = _compose_template(stats, title_cap=1, death_cap=1)
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
