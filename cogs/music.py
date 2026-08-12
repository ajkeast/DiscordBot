"""SoundCloud voice playback via Lavalink + Wavelink (URL or search query)."""

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
MAX_PLAY_CANDIDATES = 5

SOUNDCLOUD_HOSTS = frozenset({
    "soundcloud.com",
    "www.soundcloud.com",
    "m.soundcloud.com",
    "on.soundcloud.com",
})

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


def to_search_query(query: str) -> str:
    """Normalize user input for Wavelink SoundCloud search (URL or bare search text)."""
    text = query.strip()
    if is_soundcloud_url(text):
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


def rank_playable_tracks(tracks) -> list[wavelink.Playable]:
    """Order candidates: full streams first, then previews (search order preserved)."""
    if isinstance(tracks, wavelink.Playlist):
        candidates = list(tracks.tracks)
    else:
        candidates = list(tracks)
    full = [t for t in candidates if not is_preview_track(t)]
    previews = [t for t in candidates if is_preview_track(t)]
    return full + previews


def pick_playable_track(tracks) -> Optional[wavelink.Playable]:
    """Prefer a full stream over a SoundCloud preview clip."""
    ranked = rank_playable_tracks(tracks)
    return ranked[0] if ranked else None


def _preview_note(track: wavelink.Playable) -> str:
    if not is_preview_track(track):
        return ""
    return (
        "\n⚠️ SoundCloud only allows a short preview of this track "
        "(full stream needs Go+)."
    )


def _track_url(track: wavelink.Playable) -> str:
    uri = getattr(track, "uri", None)
    if uri:
        text = str(uri)
        if "://" not in text and is_soundcloud_url(text):
            return f"https://{text}"
        return text
    return "https://soundcloud.com"


def _lavalink_user_message(exc: BaseException) -> str:
    text = str(exc).lower()
    if "nodisconnected" in text.replace(" ", "") or "no nodes" in text or (
        "node" in text and "connect" in text
    ):
        return "Music backend is offline. Try again in a moment."
    return "Couldn't find that on SoundCloud. Try a different search or paste a track URL."


class Music(commands.Cog):
    """Play SoundCloud audio in a voice channel via Lavalink (URL or search)."""

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

    @commands.Cog.listener()
    async def on_wavelink_track_exception(
        self, payload: wavelink.TrackExceptionEventPayload
    ) -> None:
        """Try the next search candidate; only complain if nothing else will play."""
        exc = payload.exception or {}
        message = str(exc.get("message") or exc.get("cause") or exc)
        logger.error("Track exception for %r: %s", getattr(payload.track, "title", None), message)
        player = payload.player
        if player is None:
            return

        text_channel = getattr(player, "music_text_channel", None)
        requester = getattr(player, "music_requester", None) or "someone"
        fallbacks: list[wavelink.Playable] = list(
            getattr(player, "music_fallbacks", None) or []
        )

        while fallbacks:
            next_track = fallbacks.pop(0)
            player.music_fallbacks = fallbacks  # type: ignore[attr-defined]
            try:
                await player.play(next_track)
            except Exception:
                logger.exception(
                    "Failed starting fallback track %r",
                    getattr(next_track, "title", None),
                )
                continue
            if text_channel is not None:
                note = _preview_note(next_track)
                try:
                    await text_channel.send(
                        self._track_line(
                            next_track,
                            requester,
                            prefix="▶️ **Playing instead:** ",
                        )
                        + note
                    )
                except discord.HTTPException:
                    logger.debug("Failed to notify channel of fallback play", exc_info=True)
            return

        player.music_fallbacks = []  # type: ignore[attr-defined]
        if text_channel is None:
            return
        try:
            await text_channel.send(
                "Couldn't play that track. Try another SoundCloud search or URL."
            )
        except discord.HTTPException:
            logger.debug("Failed to notify channel of track exception", exc_info=True)

    def _track_line(self, track: wavelink.Playable, requester: str, *, prefix: str = "") -> str:
        title = safe_title(getattr(track, "title", None) or "Unknown title")
        duration = format_duration(_track_duration_seconds(track))
        # Angle brackets keep the link clickable but suppress Discord's rich embed.
        return (
            f"{prefix}**{title}** ({duration})\n"
            f"<{_track_url(track)}> — {requester}"
        )

    def _arm_play_candidates(
        self,
        player: wavelink.Player,
        candidates: list[wavelink.Playable],
        requester: str,
    ) -> wavelink.Playable:
        """Play/queue the best candidate and keep the rest for exception fallback."""
        track = candidates[0]
        player.music_fallbacks = candidates[1:]  # type: ignore[attr-defined]
        player.music_requester = requester  # type: ignore[attr-defined]
        return track

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
        player.autoplay = wavelink.AutoPlayMode.partial
        if ctx.channel is not None:
            player.music_text_channel = ctx.channel  # type: ignore[attr-defined]
        return player

    @commands.hybrid_command(brief="Play a SoundCloud URL or search query in voice")
    @app_commands.describe(query="SoundCloud URL or search text (e.g. lofi hip hop)")
    async def play(self, ctx: commands.Context, *, query: str):
        """Play audio from a SoundCloud URL or the top search result."""
        if ctx.guild is None:
            await ctx.send("This command only works in a server.")
            return

        query = query.strip()
        if not query:
            await ctx.send("Give me a SoundCloud URL or something to search for.")
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
                    source=wavelink.TrackSource.SoundCloud,
                )
            except Exception as exc:
                logger.exception("Lavalink SoundCloud search failed for query %r", query)
                await ctx.send(_lavalink_user_message(exc))
                return

            if not tracks:
                await ctx.send(
                    "Couldn't find that on SoundCloud. Try a different search or paste a track URL."
                )
                return

            candidates = rank_playable_tracks(tracks)[:MAX_PLAY_CANDIDATES]
            if not candidates:
                await ctx.send(
                    "Couldn't find that on SoundCloud. Try a different search or paste a track URL."
                )
                return

            requester = ctx.author.display_name

            if player.current is not None or len(player.queue) > 0:
                if len(player.queue) >= MAX_QUEUE_SIZE:
                    await ctx.send(f"Queue is full ({MAX_QUEUE_SIZE} tracks).")
                    return
                # Queue only the best hit — fallbacks are armed when a track starts.
                track = candidates[0]
                await player.queue.put_wait(track)
                position = len(player.queue)
                await ctx.send(
                    self._track_line(
                        track,
                        requester,
                        prefix=f"Queued **#{position}:** ",
                    )
                    + _preview_note(track)
                )
                return

            track = self._arm_play_candidates(player, candidates, requester)
            await player.play(track)
            await ctx.send(
                self._track_line(track, requester, prefix="▶️ **Now playing:** ")
                + _preview_note(track)
            )

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
