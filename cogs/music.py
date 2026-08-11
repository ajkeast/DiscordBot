"""YouTube voice playback via yt-dlp + FFmpeg (URL or search query)."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Deque, Dict, Optional
from collections import deque
from urllib.parse import urlparse

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

from utils.interactions import acknowledge

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 50

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
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

_YOUTUBE_URL_RE = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/",
    re.IGNORECASE,
)


@dataclass
class Track:
    title: str
    webpage_url: str
    duration: Optional[int]
    requester_id: int
    requester_name: str


def is_youtube_url(query: str) -> bool:
    """Return True if query looks like a YouTube watch/share URL."""
    text = query.strip()
    if not _YOUTUBE_URL_RE.search(text):
        return False
    # Reject bare hostnames without a path/query that aren't youtu.be short links
    try:
        parsed = urlparse(text if "://" in text else f"https://{text}")
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return host in YOUTUBE_HOSTS


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


def _pick_entry(info: dict) -> dict:
    if "entries" in info:
        for entry in info["entries"]:
            if entry:
                return entry
        raise ValueError("No search results found.")
    return info


def extract_track_info(query: str) -> dict:
    """Resolve a URL or search query to track metadata (no download)."""
    ydl_query = to_ydl_query(query)
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(ydl_query, download=False)
    entry = _pick_entry(info)
    webpage_url = entry.get("webpage_url") or entry.get("url")
    title = entry.get("title") or "Unknown title"
    duration = entry.get("duration")
    if not webpage_url:
        raise ValueError("Could not resolve a playable YouTube URL.")
    return {
        "title": title,
        "webpage_url": webpage_url,
        "duration": duration,
    }


def extract_stream_url(webpage_url: str) -> str:
    """Fetch a fresh audio stream URL for an already-resolved video page."""
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(webpage_url, download=False)
    entry = _pick_entry(info)
    stream_url = entry.get("url")
    if not stream_url:
        raise ValueError("Could not get an audio stream for that video.")
    return stream_url


class GuildPlayer:
    """In-memory queue + playback state for one guild."""

    def __init__(self, cog: "Music", guild_id: int):
        self.cog = cog
        self.guild_id = guild_id
        self.queue: Deque[Track] = deque()
        self.current: Optional[Track] = None
        self._lock = asyncio.Lock()

    def is_idle(self) -> bool:
        return self.current is None and not self.queue


class Music(commands.Cog):
    """Play YouTube audio in a voice channel (URL or search)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._players: Dict[int, GuildPlayer] = {}

    def _player(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self._players:
            self._players[guild_id] = GuildPlayer(self, guild_id)
        return self._players[guild_id]

    async def _ensure_voice(self, ctx: commands.Context) -> discord.VoiceClient:
        if ctx.guild is None:
            raise commands.CommandError("This command only works in a server.")

        author_voice = getattr(ctx.author, "voice", None)
        channel = author_voice.channel if author_voice else None
        if channel is None:
            raise commands.CommandError("Join a voice channel first, then use `/play`.")

        voice = ctx.voice_client
        if voice is None:
            return await channel.connect()
        if voice.channel != channel:
            await voice.move_to(channel)
        return voice

    async def _play_next(self, guild_id: int) -> None:
        player = self._player(guild_id)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            player.current = None
            player.queue.clear()
            return

        voice = guild.voice_client
        async with player._lock:
            if voice is None or not voice.is_connected():
                player.current = None
                player.queue.clear()
                return

            if not player.queue:
                player.current = None
                return

            track = player.queue.popleft()
            player.current = track

            try:
                stream_url = await asyncio.to_thread(extract_stream_url, track.webpage_url)
                source = discord.FFmpegOpusAudio(stream_url, **FFMPEG_OPTIONS)
            except Exception:
                logger.exception("Failed to start track %s", track.webpage_url)
                player.current = None
                # Skip broken track and continue
                self.bot.loop.create_task(self._play_next(guild_id))
                return

            def _after(error: Optional[Exception]) -> None:
                if error:
                    logger.error("Playback error in guild %s: %s", guild_id, error)
                asyncio.run_coroutine_threadsafe(self._play_next(guild_id), self.bot.loop)

            voice.play(source, after=_after)

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
                await ctx.send(f"Couldn't find that on YouTube: {exc}")
                return

            track = Track(
                title=info["title"],
                webpage_url=info["webpage_url"],
                duration=info.get("duration"),
                requester_id=ctx.author.id,
                requester_name=ctx.author.display_name,
            )

            voice = ctx.voice_client
            should_start = (
                voice is not None
                and not voice.is_playing()
                and not voice.is_paused()
                and player.current is None
            )

            if should_start:
                player.queue.appendleft(track)
                await self._play_next(ctx.guild.id)
                await ctx.send(
                    f"▶️ **Now playing:** [{track.title}]({track.webpage_url}) "
                    f"(`{format_duration(track.duration)}`) — requested by {track.requester_name}"
                )
            else:
                if len(player.queue) >= MAX_QUEUE_SIZE:
                    await ctx.send(f"Queue is full ({MAX_QUEUE_SIZE} tracks).")
                    return
                player.queue.append(track)
                position = len(player.queue)
                await ctx.send(
                    f"➕ **Queued #{position}:** [{track.title}]({track.webpage_url}) "
                    f"(`{format_duration(track.duration)}`) — requested by {track.requester_name}"
                )

    @commands.hybrid_command(brief="Skip the current track")
    async def skip(self, ctx: commands.Context):
        """Skip the track that is playing now."""
        if ctx.guild is None or ctx.voice_client is None:
            await ctx.send("I'm not playing anything.")
            return
        if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
            await ctx.send("I'm not playing anything.")
            return
        ctx.voice_client.stop()
        await ctx.send("⏭️ Skipped.")

    @commands.hybrid_command(brief="Stop playback and clear the queue")
    async def stop(self, ctx: commands.Context):
        """Stop playback and clear the queue (stays in the voice channel)."""
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = self._player(ctx.guild.id)
        player.queue.clear()
        player.current = None
        if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
            ctx.voice_client.stop()
        await ctx.send("⏹️ Stopped and cleared the queue.")

    @commands.hybrid_command(brief="Show the upcoming queue")
    async def queue(self, ctx: commands.Context):
        """Show the current track and upcoming queue."""
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = self._player(ctx.guild.id)
        if player.current is None and not player.queue:
            await ctx.send("Queue is empty.")
            return

        lines = []
        if player.current is not None:
            lines.append(
                f"**Now playing:** [{player.current.title}]({player.current.webpage_url}) "
                f"(`{format_duration(player.current.duration)}`)"
            )
        if player.queue:
            lines.append("**Up next:**")
            for i, track in enumerate(list(player.queue)[:15], start=1):
                lines.append(
                    f"`{i}.` [{track.title}]({track.webpage_url}) "
                    f"(`{format_duration(track.duration)}`) — {track.requester_name}"
                )
            remaining = len(player.queue) - 15
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
        track = player.current
        if track is None:
            await ctx.send("Nothing is playing right now.")
            return
        await ctx.send(
            f"🎵 **Now playing:** [{track.title}]({track.webpage_url}) "
            f"(`{format_duration(track.duration)}`) — requested by {track.requester_name}"
        )

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
        player.queue.clear()
        player.current = None
        if ctx.voice_client is not None:
            await ctx.voice_client.disconnect()
            await ctx.send("👋 Left the voice channel.")
        else:
            await ctx.send("I'm not in a voice channel.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Clear state if the bot is disconnected from voice."""
        if member.id != self.bot.user.id:
            return
        if before.channel is not None and after.channel is None:
            guild_id = before.channel.guild.id
            player = self._players.get(guild_id)
            if player is not None:
                player.queue.clear()
                player.current = None


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
