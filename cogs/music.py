"""YouTube voice playback via yt-dlp + FFmpeg (URL or search query)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional
from urllib.parse import urlparse

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

from utils.interactions import acknowledge

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 50
YDL_SOCKET_TIMEOUT = 20
# Writable copy — secrets mount is read-only and yt-dlp tries to refresh cookies.
_RUNTIME_COOKIE_PATH = "/tmp/youtube.cookies"

YOUTUBE_HOSTS = frozenset({
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
})

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "socket_timeout": YDL_SOCKET_TIMEOUT,
    "cachedir": False,
    # Allow Deno to fetch EJS challenge scripts when the bundled package is stale.
    "remote_components": {"ejs:npm"},
}


def _resolved_cookiefile() -> Optional[str]:
    """Return a writable cookie path, or None if cookies are not configured."""
    src = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    if not src:
        return None
    if not os.path.isfile(src):
        logger.warning("YOUTUBE_COOKIES_FILE set but file missing: %s", src)
        return None
    try:
        shutil.copy2(src, _RUNTIME_COOKIE_PATH)
    except OSError:
        logger.exception("Failed to copy YouTube cookies to %s", _RUNTIME_COOKIE_PATH)
        return None
    return _RUNTIME_COOKIE_PATH


def ydl_options() -> dict:
    """Build yt-dlp options, attaching Netscape cookies when configured."""
    opts = dict(YDL_OPTIONS)
    cookiefile = _resolved_cookiefile()
    if cookiefile:
        opts["cookiefile"] = cookiefile
    return opts


def _yt_dlp_user_message(exc: BaseException) -> str:
    text = str(exc).lower()
    if (
        "sign in to confirm" in text
        or "not a bot" in text
        or "login_required" in text
        or "cookies" in text
    ):
        return (
            "YouTube blocked this request (bot check). "
            "An admin needs to refresh the YouTube cookies on the server."
        )
    return "Couldn't find that on YouTube. Try a different search or paste a video URL."

FFMPEG_OPTIONS = {
    "before_options": (
        "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    ),
    "options": "-vn",
}

_YOUTUBE_HOST_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:music\.)?(?:m\.)?(?:youtube\.com|youtu\.be)/",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Track:
    title: str
    webpage_url: str
    duration: Optional[int]
    requester_id: int
    requester_name: str


def is_youtube_url(query: str) -> bool:
    """True only when the entire query is a YouTube URL (not search text)."""
    text = query.strip()
    if not text or any(ch.isspace() for ch in text):
        return False
    if not _YOUTUBE_HOST_RE.match(text):
        return False
    try:
        parsed = urlparse(text if "://" in text else f"https://{text}")
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return False
    # Require a video path or query so bare "youtube.com/" is not treated as a URL play.
    if host.endswith("youtu.be"):
        return bool(parsed.path.strip("/"))
    path = parsed.path or ""
    return bool(parsed.query) or path.startswith(
        ("/watch", "/shorts/", "/live/", "/embed/", "/v/")
    )


def to_ydl_query(query: str) -> str:
    """Map user input to a yt-dlp URL or ytsearch1 query."""
    text = query.strip()
    if is_youtube_url(text):
        if "://" not in text:
            return f"https://{text}"
        return text
    return f"ytsearch1:{text}"


def format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "?:??"
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def safe_title(title: str) -> str:
    """Neutralize markdown characters that break Discord message formatting."""
    return discord.utils.escape_markdown(title).replace("[", "").replace("]", "")


def _pick_entry(info: dict) -> dict:
    if "entries" in info:
        for entry in info["entries"]:
            if entry:
                return entry
        raise ValueError("No search results found.")
    return info


def _webpage_url_from_entry(entry: dict) -> str:
    """Prefer a stable watch URL; never fall back to expiring CDN stream URLs."""
    for key in ("webpage_url", "original_url"):
        value = entry.get(key)
        if value and is_youtube_url(value):
            return value if "://" in value else f"https://{value}"
    video_id = entry.get("id")
    if video_id and re.fullmatch(r"[\w-]{6,}", str(video_id)):
        return f"https://www.youtube.com/watch?v={video_id}"
    raise ValueError("Could not resolve a playable YouTube URL.")


def extract_track_info(query: str) -> dict:
    """Resolve a URL or search query to track metadata (no download)."""
    ydl_query = to_ydl_query(query)
    with yt_dlp.YoutubeDL(ydl_options()) as ydl:
        info = ydl.extract_info(ydl_query, download=False)
    if info is None:
        raise ValueError("No results.")
    entry = _pick_entry(info)
    return {
        "title": entry.get("title") or "Unknown title",
        "webpage_url": _webpage_url_from_entry(entry),
        "duration": entry.get("duration"),
    }


def extract_stream_url(webpage_url: str) -> str:
    """Fetch a fresh audio stream URL for an already-resolved video page."""
    if not is_youtube_url(webpage_url):
        raise ValueError("Refusing to stream a non-YouTube URL.")
    with yt_dlp.YoutubeDL(ydl_options()) as ydl:
        info = ydl.extract_info(webpage_url, download=False)
    if info is None:
        raise ValueError("Could not get an audio stream for that video.")
    entry = _pick_entry(info)
    stream_url = entry.get("url")
    if not stream_url:
        raise ValueError("Could not get an audio stream for that video.")
    return stream_url


class GuildPlayer:
    """In-memory queue + playback state for one guild."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue: Deque[Track] = deque()
        self.current: Optional[Track] = None
        self.lock = asyncio.Lock()
        # Bumped on stop/leave so in-flight yt-dlp extracts do not start audio.
        self.generation = 0


class Music(commands.Cog):
    """Play YouTube audio in a voice channel (URL or search)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._players: Dict[int, GuildPlayer] = {}
        cookiefile = _resolved_cookiefile()
        if cookiefile:
            logger.info("YouTube cookies enabled (%s)", cookiefile)
        else:
            logger.warning(
                "YouTube cookies not loaded — /play may hit bot checks. "
                "Set YOUTUBE_COOKIES_FILE to a Netscape cookies.txt path."
            )

    def _player(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self._players:
            self._players[guild_id] = GuildPlayer(guild_id)
        return self._players[guild_id]

    async def _ensure_voice(self, ctx: commands.Context) -> discord.VoiceClient:
        if ctx.guild is None:
            raise commands.CommandError("This command only works in a server.")

        author_voice = getattr(ctx.author, "voice", None)
        channel = author_voice.channel if author_voice else None
        if channel is None:
            raise commands.CommandError("Join a voice channel first, then use `/play`.")

        me = ctx.guild.me
        if me is not None:
            perms = channel.permissions_for(me)
            if not perms.connect or not perms.speak:
                raise commands.CommandError(
                    "I need **Connect** and **Speak** permissions in that voice channel."
                )

        voice = ctx.voice_client
        try:
            if voice is None:
                return await channel.connect()
            if voice.channel != channel:
                await voice.move_to(channel)
            return voice
        except asyncio.TimeoutError as exc:
            raise commands.CommandError("Timed out joining the voice channel.") from exc
        except discord.ClientException as exc:
            raise commands.CommandError(f"Could not join voice: {exc}") from exc

    def _track_line(self, track: Track, *, prefix: str = "") -> str:
        title = safe_title(track.title)
        return (
            f"{prefix}**{title}** ({format_duration(track.duration)})\n"
            f"{track.webpage_url} — {track.requester_name}"
        )

    async def _play_next(self, guild_id: int) -> None:
        player = self._player(guild_id)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            async with player.lock:
                player.current = None
                player.queue.clear()
            return

        async with player.lock:
            voice = guild.voice_client
            if voice is None or not voice.is_connected():
                player.current = None
                player.queue.clear()
                return
            # Another starter already owns playback (or pause).
            if voice.is_playing() or voice.is_paused():
                return
            player.current = None
            if not player.queue:
                return
            track = player.queue.popleft()
            player.current = track
            generation = player.generation

        try:
            stream_url = await asyncio.to_thread(extract_stream_url, track.webpage_url)
        except Exception:
            logger.exception("Failed to resolve stream for %s", track.webpage_url)
            async with player.lock:
                if player.generation != generation:
                    return
                if player.current is track:
                    player.current = None
            await self._play_next(guild_id)
            return

        async with player.lock:
            if player.generation != generation:
                if player.current is track:
                    player.current = None
                return
            voice = guild.voice_client
            if voice is None or not voice.is_connected():
                player.current = None
                player.queue.clear()
                return
            if voice.is_playing() or voice.is_paused():
                # Lost the race; put the track back at the front.
                if player.current is track:
                    player.current = None
                player.queue.appendleft(track)
                return
            try:
                source = discord.FFmpegOpusAudio(stream_url, **FFMPEG_OPTIONS)
            except Exception:
                logger.exception("FFmpeg failed for %s", track.webpage_url)
                if player.current is track:
                    player.current = None
                self.bot.loop.create_task(self._play_next(guild_id))
                return

            def _after(error: Optional[Exception]) -> None:
                if error:
                    logger.error("Playback error in guild %s: %s", guild_id, error)
                asyncio.run_coroutine_threadsafe(self._play_next(guild_id), self.bot.loop)

            try:
                voice.play(source, after=_after)
            except discord.ClientException:
                logger.exception("voice.play failed in guild %s", guild_id)
                if player.current is track:
                    player.current = None
                player.queue.appendleft(track)

    @commands.hybrid_command(brief="Play a YouTube URL or search query in voice")
    @app_commands.describe(query="YouTube URL or search text (e.g. tubthumping)")
    async def play(self, ctx: commands.Context, *, query: str):
        """Play audio from a YouTube URL or the top search result."""
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return

        query = query.strip()
        if not query:
            await ctx.send("Give me a YouTube URL or something to search for.")
            return

        player = self._player(ctx.guild.id)

        async with acknowledge(ctx):
            try:
                await self._ensure_voice(ctx)
            except commands.CommandError as exc:
                await ctx.send(str(exc))
                return

            try:
                info = await asyncio.to_thread(extract_track_info, query)
            except Exception as exc:
                logger.exception("yt-dlp failed for query %r", query)
                await ctx.send(_yt_dlp_user_message(exc))
                return

            track = Track(
                title=info["title"],
                webpage_url=info["webpage_url"],
                duration=info.get("duration"),
                requester_id=ctx.author.id,
                requester_name=ctx.author.display_name,
            )

            async with player.lock:
                voice = ctx.voice_client
                playing = voice is not None and (
                    voice.is_playing() or voice.is_paused()
                )
                active = player.current is not None or playing
                if active and len(player.queue) >= MAX_QUEUE_SIZE:
                    await ctx.send(f"Queue is full ({MAX_QUEUE_SIZE} tracks).")
                    return
                player.queue.append(track)
                position = len(player.queue)
                # Start when nothing is playing/extracting. A non-empty idle
                # queue (e.g. after a dropped after-callback) should recover.
                should_start = player.current is None and not playing

            if should_start:
                await self._play_next(ctx.guild.id)
                # If we recovered an older queued item first, still acknowledge
                # this request as queued when it was not alone in line.
                if position == 1:
                    await ctx.send(self._track_line(track, prefix="▶️ **Now playing:** "))
                else:
                    await ctx.send(
                        self._track_line(track, prefix=f"➕ **Queued #{position}:** ")
                    )
            else:
                await ctx.send(
                    self._track_line(track, prefix=f"➕ **Queued #{position}:** ")
                )

    @commands.hybrid_command(brief="Skip the current track")
    async def skip(self, ctx: commands.Context):
        """Skip the track that is playing now."""
        if ctx.guild is None or ctx.voice_client is None:
            await ctx.send("I'm not playing anything.")
            return
        voice = ctx.voice_client
        if not voice.is_playing() and not voice.is_paused():
            await ctx.send("I'm not playing anything.")
            return
        voice.stop()
        await ctx.send("⏭️ Skipped.")

    @commands.hybrid_command(brief="Stop playback and clear the queue")
    async def stop(self, ctx: commands.Context):
        """Stop playback and clear the queue (stays in the voice channel)."""
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = self._player(ctx.guild.id)
        async with player.lock:
            player.generation += 1
            player.queue.clear()
            player.current = None
        voice = ctx.voice_client
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()
        await ctx.send("⏹️ Stopped and cleared the queue.")

    @commands.hybrid_command(brief="Show the upcoming queue")
    async def queue(self, ctx: commands.Context):
        """Show the current track and upcoming queue."""
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = self._player(ctx.guild.id)
        async with player.lock:
            current = player.current
            upcoming = list(player.queue)

        if current is None and not upcoming:
            await ctx.send("Queue is empty.")
            return

        lines = []
        if current is not None:
            lines.append(
                f"**Now playing:** **{safe_title(current.title)}** "
                f"({format_duration(current.duration)})\n{current.webpage_url}"
            )
        if upcoming:
            lines.append("**Up next:**")
            for i, track in enumerate(upcoming[:15], start=1):
                lines.append(
                    f"`{i}.` **{safe_title(track.title)}** "
                    f"({format_duration(track.duration)}) — {track.requester_name}"
                )
            remaining = len(upcoming) - 15
            if remaining > 0:
                lines.append(f"_…and {remaining} more_")
        await ctx.send("\n".join(lines))

    @commands.hybrid_command(name="np", brief="Show the track playing now")
    async def now_playing(self, ctx: commands.Context):
        """Show the track that is playing now."""
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = self._player(ctx.guild.id)
        async with player.lock:
            track = player.current
        if track is None:
            await ctx.send("Nothing is playing right now.")
            return
        await ctx.send(self._track_line(track, prefix="🎵 **Now playing:** "))

    @commands.hybrid_command(brief="Pause the current track")
    async def pause(self, ctx: commands.Context):
        """Pause playback."""
        if ctx.voice_client is None or not ctx.voice_client.is_playing():
            await ctx.send("Nothing is playing.")
            return
        ctx.voice_client.pause()
        await ctx.send("⏸️ Paused.")

    @commands.hybrid_command(brief="Resume paused playback")
    async def resume(self, ctx: commands.Context):
        """Resume paused playback."""
        if ctx.voice_client is None or not ctx.voice_client.is_paused():
            await ctx.send("Nothing is paused.")
            return
        ctx.voice_client.resume()
        await ctx.send("▶️ Resumed.")

    @commands.hybrid_command(brief="Leave the voice channel")
    async def leave(self, ctx: commands.Context):
        """Disconnect from voice and clear the queue."""
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = self._player(ctx.guild.id)
        async with player.lock:
            player.generation += 1
            player.queue.clear()
            player.current = None
        if ctx.voice_client is not None:
            await ctx.voice_client.disconnect()
            await ctx.send("👋 Left the voice channel.")
        else:
            await ctx.send("I'm not in a voice channel.")

    async def _cleanup_guild_voice(self, guild_id: int) -> None:
        player = self._players.get(guild_id)
        if player is None:
            return
        async with player.lock:
            player.generation += 1
            player.queue.clear()
            player.current = None

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Clear state on bot disconnect; leave when alone in the channel."""
        bot_user = self.bot.user
        if bot_user is None:
            return

        # Bot was disconnected / moved out of a channel.
        if member.id == bot_user.id:
            if before.channel is not None and after.channel is None:
                await self._cleanup_guild_voice(before.channel.guild.id)
            return

        # If a human left/moved and the bot is alone, disconnect.
        channel = before.channel
        if channel is None:
            return
        if after.channel == before.channel:
            return
        guild = channel.guild
        voice = guild.voice_client
        if voice is None or voice.channel != channel:
            return
        humans = [m for m in channel.members if not m.bot]
        if humans:
            return
        await self._cleanup_guild_voice(guild.id)
        try:
            await voice.disconnect()
        except Exception:
            logger.exception("Failed to auto-disconnect in guild %s", guild.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
