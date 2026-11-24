"""Grok-driven Minecraft daily recap narrative + compact fallback."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter

from xai_sdk import Client
from xai_sdk.chat import system, user

from config import minecraft_model, xai_api_key
from minecraft.models import DayRecap, format_duration
from services.xai import create_xai_client

logger = logging.getLogger(__name__)

MAX_NARRATIVE_CHARS = 1800
GROK_ATTEMPTS = 2

SYSTEM_PROMPT = """You write the daily Minecraft recap for a small friends' Discord server.
You are given factual JSON from collector telemetry for one play day. Use only that data.
Write like an observant, witty outside third-party commentator: concise, natural, and human.
Address players by name, or use "you" when speaking to them. Never use "we", "us", or "our".

Use a consistent, scannable layout with blank lines between sections:
1. Opening: 1–2 crisp sentences covering how many people played, combined playtime, and the peak stretch if notable (mention if `partial_day` is true).
2. **Time played**: one compact bullet per player with playtime and session count. Mention still online, likely AFK, or welcome a new player only when those fields are true.
3. **Deaths**: omit this section entirely if `total_deaths` is 0. Otherwise, read the chronological death log and concisely summarize the story — group chaotic stretches, raids, or repeat causes naturally instead of listing every death line by line. When a death has `"pvp": true`, call that fight PVP. Never invent a location, mob, or event that the death messages do not show.

Rules:
- Stick strictly to the JSON data; never invent players, times, causes, or places unsupported by the logs.
- Do not mention advancements.
- Use clean Discord markdown, no code fences, and keep it concise (under 1,500 characters).
"""


def minecraft_grok_enabled() -> bool:
    return bool(xai_api_key())


def nobody_line(recap: DayRecap) -> str:
    return f"Nobody played Minecraft on {recap.pretty_date}."


def clean_model_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:\w+)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def is_valid_narrative(text: str, recap: DayRecap) -> bool:
    """Minimal structural validation — no hardcoded keywords or mob lists."""
    cleaned = text.strip()
    if not cleaned or len(cleaned) > MAX_NARRATIVE_CHARS:
        return False
    lowered = cleaned.lower()
    for player in recap.players:
        if player.name.lower() not in lowered:
            return False
    return True


def _md_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("`", "\\`")
    )


def format_fallback(recap: DayRecap) -> str:
    """Compact factual recap used only when Grok is unavailable."""
    if recap.nobody_played:
        return nobody_line(recap)

    sections: list[str] = []
    player_word = "player" if len(recap.players) == 1 else "players"
    opening = (
        f"**{len(recap.players)} {player_word}** played for a combined "
        f"**{format_duration(recap.total_playtime_seconds)}**."
    )
    if recap.peak_stretch is not None:
        opening += (
            f" Peak activity was {recap.peak_stretch.start_label}–"
            f"{recap.peak_stretch.end_label} ({recap.peak_stretch.peak_players} online)."
        )
    if recap.partial_day:
        opening += " The day is still in progress."
    sections.append(opening)

    play_lines = ["**Time played**"]
    for p in recap.players:
        session_word = "session" if p.sessions == 1 else "sessions"
        notes: list[str] = []
        if p.still_online:
            notes.append("still online")
        if p.likely_afk:
            notes.append("likely AFK")
        if p.new_player:
            notes.append("welcome, first time on the server")
        suffix = f" ({', '.join(notes)})" if notes else ""
        play_lines.append(
            f"- **{_md_escape(p.name)}**: {p.playtime} ({p.sessions} {session_word}){suffix}"
        )
    sections.append("\n".join(play_lines))

    if recap.deaths:
        by_player = Counter(d.player for d in recap.deaths)
        top_summary = ", ".join(
            f"{_md_escape(name)} ({count})" for name, count in by_player.most_common(6)
        )
        sections.append(f"**Deaths ({recap.total_deaths})**\n{top_summary}")

    text = "\n\n".join(sections)
    if len(text) > MAX_NARRATIVE_CHARS:
        return text[: MAX_NARRATIVE_CHARS - 1].rstrip() + "…"
    return text


def ask_grok(
    recap: DayRecap,
    *,
    client: Client | None = None,
    model: str | None = None,
) -> str:
    """Request Grok's daily commentary for ``recap``. Raises on failure."""
    xai = client or create_xai_client(timeout=90)
    model_name = model or minecraft_model()
    payload = json.dumps(recap.to_grok_payload(), ensure_ascii=False)
    user_prompt = f"Write the daily Minecraft recap from this JSON:\n\n{payload}"

    last_error: Exception | None = None
    for attempt in range(GROK_ATTEMPTS):
        try:
            chat = xai.chat.create(
                model=model_name,
                temperature=0.4,
                reasoning_effort="low",
                store_messages=False,
            )
            chat.append(system(SYSTEM_PROMPT))
            chat.append(user(user_prompt))
            response = chat.sample()
            text = clean_model_text(response.content or "")
            if not is_valid_narrative(text, recap):
                raise ValueError("Grok narrative was empty, too long, or omitted a player")
            return text
        except Exception as exc:
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


def generate_narrative(
    recap: DayRecap,
    *,
    client: Client | None = None,
    model: str | None = None,
) -> tuple[str, str]:
    """Return ``(narrative_text, source)`` where source is ``grok`` or ``template``."""
    if recap.nobody_played:
        return nobody_line(recap), "template"
    if minecraft_grok_enabled() or client is not None:
        try:
            return ask_grok(recap, client=client, model=model), "grok"
        except Exception:
            logger.exception("Minecraft Grok narrative failed; using fallback template")
    return format_fallback(recap), "template"
