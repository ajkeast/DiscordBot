"""SoundCloud URL normalization, async shortlink expansion, and track helpers."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse, urlunparse

import aiohttp
import discord
import wavelink

logger = logging.getLogger(__name__)

SOUNDCLOUD_HOSTS = frozenset(
    {
        "soundcloud.com",
        "www.soundcloud.com",
        "m.soundcloud.com",
        "on.soundcloud.com",
    }
)

_SOUNDCLOUD_HOST_RE = re.compile(
    r"^(?:https?://)?(?:www\.|m\.|on\.)?soundcloud\.com/",
    re.IGNORECASE,
)


def is_soundcloud_url(query: str) -> bool:
    """True only when the entire query is a SoundCloud URL (not search text)."""
    text = query.strip()
    if not text or any(ch.isspace() for ch in text):
        return False
    if not _SOUNDCLOUD_HOST_RE.match(text):
        return False
    try:
        parsed = urlparse(text if "://" in text else f"https://{text}")
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host not in SOUNDCLOUD_HOSTS:
        return False
    return bool(parsed.path.strip("/"))


def _prefer_soundcloud_host(url: str) -> str:
    """Rewrite m.soundcloud.com so the Soundy source manager accepts the URL."""
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() != "m.soundcloud.com":
        return url
    return urlunparse(parsed._replace(netloc="soundcloud.com"))


def to_search_query(query: str) -> str:
    """Normalize user input for Wavelink SoundCloud search."""
    text = query.strip()
    if is_soundcloud_url(text):
        if "://" not in text:
            text = f"https://{text}"
        return _prefer_soundcloud_host(text)
    return text


async def expand_soundcloud_short_link(
    url: str,
    session: aiohttp.ClientSession,
    *,
    timeout_seconds: float = 8.0,
) -> str:
    """Follow ``on.soundcloud.com`` redirects asynchronously."""
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() != "on.soundcloud.com":
        return url
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with session.get(
            url,
            headers={"User-Agent": "DiscordBot music"},
            allow_redirects=True,
            timeout=timeout,
        ) as response:
            final = str(response.url)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        logger.warning("Could not expand SoundCloud short link %s", url, exc_info=True)
        return url
    if not is_soundcloud_url(final):
        return url
    return to_search_query(final)


def format_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "?:??"
    total = max(0, int(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def safe_title(title: str) -> str:
    """Neutralize markdown characters that break Discord message formatting."""
    return discord.utils.escape_markdown(title).replace("[", "").replace("]", "")


def track_duration_seconds(track: wavelink.Playable) -> int | None:
    length = getattr(track, "length", None)
    if length is None:
        return None
    try:
        return max(0, int(length) // 1000)
    except (TypeError, ValueError):
        return None


def is_preview_track(track: wavelink.Playable) -> bool:
    """True when SoundCloud only exposes a short Go+ preview stream (~30s)."""
    for attr in ("identifier", "uri"):
        value = getattr(track, attr, None)
        if value and "/preview/" in str(value).lower():
            return True
    return False


def full_stream_tracks(tracks) -> list[wavelink.Playable]:
    """Return only fully streamable uploads (no Go+ preview clips)."""
    candidates = list(tracks.tracks) if isinstance(tracks, wavelink.Playlist) else list(tracks)
    return [t for t in candidates if not is_preview_track(t)]


def pick_playable_track(tracks) -> wavelink.Playable | None:
    full = full_stream_tracks(tracks)
    return full[0] if full else None


def track_url(track: wavelink.Playable) -> str:
    uri = getattr(track, "uri", None)
    if uri:
        text = str(uri)
        if "://" not in text and is_soundcloud_url(text):
            return f"https://{text}"
        return text
    return "https://soundcloud.com"


def track_line(track: wavelink.Playable, *, prefix: str = "") -> str:
    title = safe_title(getattr(track, "title", None) or "Unknown title")
    duration = format_duration(track_duration_seconds(track))
    return f"{prefix}**{title}** ({duration})\n<{track_url(track)}>"


def lavalink_user_message(exc: BaseException) -> str:
    text = str(exc).lower()
    if "nodisconnected" in text.replace(" ", "") or "no nodes" in text or (
        "node" in text and "connect" in text
    ):
        return "Music backend is offline. Try again in a moment."
    return "Couldn't find that on SoundCloud. Try a different search or paste a track URL."
