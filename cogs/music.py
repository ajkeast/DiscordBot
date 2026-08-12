"""YouTube voice playback via Lavalink + Wavelink (URL or search query)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

import discord
import wavelink
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

_YOUTUBE_HOST_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:music\.)?(?:m\.)?(?:youtube\.com|youtu\.be)/",
    re.IGNORECASE,
)


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
    if host.endswith("youtu.be"):
        return bool(parsed.path.strip("/"))
    path = parsed.path or ""
    return bool(parsed.query) or path.startswith(
        ("/watch", "/shorts/", "/live/", "/embed/", "/v/")
    )


def to_search_query(query: str) -> str:
    """Normalize user input for Wavelink search (URL or bare search text)."""
    text = query.strip()
    if is_youtube_url(text):
        if "://" not in text:
            return f"https://{text}"
        return text
    return text


def format_duration(seconds: Optional[int | float]) -> str:
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


def _track_duration_seconds(track: wavelink.Playable) -> Optional[int]:
    length = getattr(track, "length", None)
    if length is None:
        return None
    # Wavelink lengths are milliseconds.
    try:
        return max(0, int(length) // 1000)
    except (TypeError, ValueError):
        return None


def _track_url(track: wavelink.Playable) -> str:
    uri = getattr(track, "uri", None)
    if uri and is_youtube_url(str(uri)):
        return str(uri) if "://" in str(uri) else f"https://{uri}"
    identifier = getattr(track, "identifier", None)
    if identifier and re.fullmatch(r"[\w-]{6,}", str(identifier)):
        return f"https://www.youtube.com/watch?v={identifier}"
    return str(uri or "https://www.youtube.com")


def _lavalink_user_message(exc: BaseException) -> str:
    text = str(exc).lower()
    if "oauth" in text or "login" in text or "sign in" in text or "not a bot" in text:
        return (
            "YouTube needs Lavalink OAuth. An admin should check "
            "`discord-lavalink` logs for the device login URL, then set "
            "`YOUTUBE_OAUTH_REFRESH_TOKEN`."
        )
    if "nodisconnected" in text.replace(" ", "") or "no nodes" in text or "node" in text and "connect" in text:
        return "Music backend is offline. Try again in a moment."
    return "Couldn't find that on YouTube. Try a different search or paste a video URL."


class Music(commands.Cog):
    """Play YouTube audio in a voice channel via Lavalink (URL or search)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        uri = os.getenv("LAVALINK_URI", "http://127.0.0.1:2333").strip()
        password = os.getenv("LAVALINK_PASSWORD", "").strip()
        if not password:
            logger.warning(
                "LAVALINK_PASSWORD is empty — set it in .env to match Lavalink."
            )
        nodes = [wavelink.Node(identifier="main", uri=uri, password=password or "youshallnotpass")]
        try:
            await wavelink.Pool.connect(nodes=nodes, client=self.bot)
            logger.info("Connected to Lavalink at %s", uri)
        except Exception:
            logger.exception("Failed to connect to Lavalink at %s", uri)

    async def cog_unload(self) -> None:
        try:
            await wavelink.Pool.close()
        except Exception:
            logger.debug("Lavalink pool close failed", exc_info=True)

    def _track_line(self, track: wavelink.Playable, requester: str, *, prefix: str = "") -> str:
        title = safe_title(getattr(track, "title", None) or "Unknown title")
        duration = format_duration(_track_duration_seconds(track))
        return (
            f"{prefix}**{title}** ({duration})\n"
            f"{_track_url(track)} — {requester}"
        )

    async def _ensure_player(self, ctx: commands.Context) -> wavelink.Player:
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

        player = ctx.voice_client
        try:
            if player is None:
                player = await channel.connect(cls=wavelink.Player, self_deaf=True)
            elif not isinstance(player, wavelink.Player):
                await player.disconnect()
                player = await channel.connect(cls=wavelink.Player, self_deaf=True)
            elif player.channel != channel:
                await player.move_to(channel)
        except asyncio.TimeoutError as exc:
            raise commands.CommandError("Timed out joining the voice channel.") from exc
        except discord.ClientException as exc:
            raise commands.CommandError(f"Could not join voice: {exc}") from exc

        assert isinstance(player, wavelink.Player)
        # Advance the queue when a track ends (no AutoPlay recommendations).
        player.autoplay = wavelink.AutoPlayMode.partial
        return player

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

        async with acknowledge(ctx):
            try:
                player = await self._ensure_player(ctx)
            except commands.CommandError as exc:
                await ctx.send(str(exc))
                return

            try:
                tracks = await wavelink.Playable.search(
                    to_search_query(query),
                    source=wavelink.TrackSource.YouTube,
                )
            except Exception as exc:
                logger.exception("Lavalink search failed for query %r", query)
                await ctx.send(_lavalink_user_message(exc))
                return

            if not tracks:
                await ctx.send(
                    "Couldn't find that on YouTube. Try a different search or paste a video URL."
                )
                return

            track = tracks[0] if not isinstance(tracks, wavelink.Playlist) else tracks.tracks[0]
            requester = ctx.author.display_name

            if player.current is not None or len(player.queue) > 0:
                if len(player.queue) >= MAX_QUEUE_SIZE:
                    await ctx.send(f"Queue is full ({MAX_QUEUE_SIZE} tracks).")
                    return
                await player.queue.put_wait(track)
                position = len(player.queue)
                await ctx.send(
                    self._track_line(
                        track,
                        requester,
                        prefix=f"Queued **#{position}:** ",
                    )
                )
                return

            await player.play(track)
            await ctx.send(self._track_line(track, requester, prefix="▶️ **Now playing:** "))

    @commands.hybrid_command(brief="Skip the current track")
    async def skip(self, ctx: commands.Context):
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = ctx.voice_client
        if not isinstance(player, wavelink.Player) or not player.playing:
            await ctx.send("Nothing is playing.")
            return
        await player.skip(force=True)
        await ctx.send("Skipped.")

    @commands.hybrid_command(brief="Stop playback and clear the queue")
    async def stop(self, ctx: commands.Context):
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = ctx.voice_client
        if not isinstance(player, wavelink.Player):
            await ctx.send("I'm not in a voice channel.")
            return
        player.queue.clear()
        await player.stop()
        await ctx.send("Stopped and cleared the queue.")

    @commands.hybrid_command(brief="Show the queue")
    async def queue(self, ctx: commands.Context):
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = ctx.voice_client
        if not isinstance(player, wavelink.Player):
            await ctx.send("Queue is empty.")
            return
        if player.current is None and not player.queue:
            await ctx.send("Queue is empty.")
            return

        lines: list[str] = []
        if player.current is not None:
            lines.append(
                self._track_line(player.current, "now", prefix="▶️ **Now playing:** ")
            )
        for index, track in enumerate(list(player.queue)[:20], start=1):
            title = safe_title(getattr(track, "title", None) or "Unknown title")
            duration = format_duration(_track_duration_seconds(track))
            lines.append(f"**{index}.** {title} ({duration})")
        remaining = max(0, len(player.queue) - 20)
        if remaining:
            lines.append(f"…and {remaining} more")
        await ctx.send("\n".join(lines))

    @commands.hybrid_command(name="np", brief="Show the track playing now")
    async def now_playing(self, ctx: commands.Context):
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = ctx.voice_client
        if not isinstance(player, wavelink.Player) or player.current is None:
            await ctx.send("Nothing is playing.")
            return
        await ctx.send(self._track_line(player.current, "now", prefix="▶️ **Now playing:** "))

    @commands.hybrid_command(brief="Pause playback")
    async def pause(self, ctx: commands.Context):
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = ctx.voice_client
        if not isinstance(player, wavelink.Player) or not player.playing:
            await ctx.send("Nothing is playing.")
            return
        if player.paused:
            await ctx.send("Already paused.")
            return
        await player.pause(True)
        await ctx.send("Paused.")

    @commands.hybrid_command(brief="Resume playback")
    async def resume(self, ctx: commands.Context):
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = ctx.voice_client
        if not isinstance(player, wavelink.Player):
            await ctx.send("Nothing is paused.")
            return
        if not player.paused:
            await ctx.send("Nothing is paused.")
            return
        await player.pause(False)
        await ctx.send("Resumed.")

    @commands.hybrid_command(brief="Leave the voice channel")
    async def leave(self, ctx: commands.Context):
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return
        player = ctx.voice_client
        if not isinstance(player, wavelink.Player):
            await ctx.send("I'm not in a voice channel.")
            return
        player.queue.clear()
        await player.disconnect()
        await ctx.send("Left the voice channel.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
