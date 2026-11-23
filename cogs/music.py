"""SoundCloud voice playback via Lavalink (Soundy AAC HLS) + Wavelink."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import aiohttp
import discord
import wavelink
from discord import app_commands
from discord.ext import commands

from config import lavalink_password, lavalink_uri, music_inactive_timeout
from services.soundcloud import (
    expand_soundcloud_short_link,
    format_duration,
    full_stream_tracks,
    is_preview_track,
    is_soundcloud_url,
    lavalink_user_message,
    safe_title,
    to_search_query,
    track_duration_seconds,
    track_line,
)
from utils.discord_helpers import acknowledge, hybrid_command

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 50
MAX_PLAY_CANDIDATES = 8
ALONE_TIMEOUT = 90


class DinkPlayer(wavelink.Player):
    """Typed Wavelink player carrying SoundCloud search fallback and announce state."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fallbacks: list[wavelink.Playable] = []
        self.pending_ctx: commands.Context | None = None
        self.announce_fallback: bool = False
        self.status_message: discord.Message | None = None
        self.text_channel: discord.abc.Messageable | None = None

    def discard_fallbacks(self) -> None:
        self.fallbacks = []
        self.announce_fallback = False

    def arm_candidates(
        self, candidates: list[wavelink.Playable], ctx: commands.Context
    ) -> wavelink.Playable:
        self.fallbacks = candidates[1:]
        self.pending_ctx = ctx
        self.announce_fallback = False
        self.status_message = None
        return candidates[0]


class Music(commands.Cog):
    """Play SoundCloud audio in a voice channel via Lavalink (URL or search)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._alone_tasks: dict[int, asyncio.Task[None]] = {}

    async def cog_load(self) -> None:
        uri = lavalink_uri()
        password = lavalink_password()
        if not password:
            logger.warning(
                "LAVALINK_PASSWORD is empty — set it in .env to match Lavalink."
            )
        nodes = [
            wavelink.Node(
                identifier="main",
                uri=uri,
                password=password or "youshallnotpass",
                inactive_player_timeout=music_inactive_timeout(),
            )
        ]
        try:
            await wavelink.Pool.connect(nodes=nodes, client=self.bot)
            logger.info("Connected to Lavalink at %s", uri)
        except Exception:
            logger.exception("Failed to connect to Lavalink at %s", uri)

    async def cog_unload(self) -> None:
        for guild_id in list(self._alone_tasks):
            self._cancel_alone(guild_id)
        try:
            await wavelink.Pool.close()
        except Exception:
            logger.debug("Lavalink pool close failed", exc_info=True)

    def _cancel_alone(self, guild_id: int) -> None:
        task = self._alone_tasks.pop(guild_id, None)
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def _arm_alone(self, guild_id: int) -> None:
        current = self._alone_tasks.get(guild_id)
        if current is not None and not current.done():
            return
        self._alone_tasks[guild_id] = asyncio.create_task(
            self._leave_if_still_alone(guild_id),
            name=f"music-alone-{guild_id}",
        )

    @staticmethod
    def _channel_has_human(channel: object) -> bool:
        members = getattr(channel, "members", None) or ()
        return any(not getattr(member, "bot", False) for member in members)

    async def _leave_if_still_alone(self, guild_id: int) -> None:
        await asyncio.sleep(ALONE_TIMEOUT)
        self._alone_tasks.pop(guild_id, None)
        guild = self.bot.get_guild(guild_id)
        player = getattr(guild, "voice_client", None) if guild is not None else None
        if not isinstance(player, DinkPlayer) or player.channel is None:
            return
        if self._channel_has_human(player.channel):
            return
        await self._disconnect_player(player)

    async def _disconnect_player(self, player: DinkPlayer) -> None:
        guild_id = getattr(getattr(player, "guild", None), "id", None)
        if isinstance(guild_id, int):
            self._cancel_alone(guild_id)
        player.discard_fallbacks()
        player.pending_ctx = None
        player.queue.clear()
        if not player.connected:
            return
        await player.disconnect()

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(self, player: wavelink.Player) -> None:
        if not isinstance(player, DinkPlayer) or player.playing:
            return
        await self._disconnect_player(player)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        _before: discord.VoiceState,
        _after: discord.VoiceState,
    ) -> None:
        guild = getattr(member, "guild", None)
        if guild is None:
            return
        player = guild.voice_client
        if not isinstance(player, DinkPlayer) or player.channel is None:
            self._cancel_alone(guild.id)
            return
        if self._channel_has_human(player.channel):
            self._cancel_alone(guild.id)
            return
        self._arm_alone(guild.id)

    async def _clear_music_announce(self, player: DinkPlayer) -> None:
        status = player.status_message
        player.status_message = None
        if status is None:
            return
        try:
            await status.delete()
        except discord.HTTPException:
            logger.debug("Failed to delete failed-track announce", exc_info=True)

    async def _send_music_message(self, player: DinkPlayer, content: str) -> None:
        ctx = player.pending_ctx
        if ctx is not None:
            player.pending_ctx = None
            try:
                player.status_message = await ctx.send(content)
                return
            except discord.HTTPException:
                logger.debug("Failed to send music message via play context", exc_info=True)

        if player.text_channel is None:
            return
        try:
            player.status_message = await player.text_channel.send(content)
        except discord.HTTPException:
            logger.debug("Failed to send music message to channel", exc_info=True)

    async def _play_next_from_queue(self, player: DinkPlayer) -> bool:
        if not player.queue:
            return False
        try:
            track = player.queue.get()
        except wavelink.QueueEmpty:
            return False
        await player.play(track)
        return True

    @commands.Cog.listener()
    async def on_wavelink_track_start(
        self, payload: wavelink.TrackStartEventPayload
    ) -> None:
        player = payload.player
        if not isinstance(player, DinkPlayer):
            return
        if player.pending_ctx is None and not player.announce_fallback:
            return

        player.announce_fallback = False
        await self._send_music_message(
            player,
            track_line(payload.track, prefix="▶️ **Now playing:** "),
        )

    @commands.Cog.listener()
    async def on_wavelink_track_exception(
        self, payload: wavelink.TrackExceptionEventPayload
    ) -> None:
        exc = payload.exception or {}
        message = str(exc.get("message") or exc.get("cause") or exc)
        logger.error(
            "Track exception for %r: %s",
            getattr(payload.track, "title", None),
            message,
        )
        player = payload.player
        if not isinstance(player, DinkPlayer):
            return

        await self._clear_music_announce(player)

        while player.fallbacks:
            next_track = player.fallbacks.pop(0)
            if is_preview_track(next_track):
                continue
            player.announce_fallback = True
            try:
                await player.play(next_track)
            except Exception:
                logger.exception(
                    "Failed starting fallback track %r",
                    getattr(next_track, "title", None),
                )
                continue
            return

        player.discard_fallbacks()
        await self._send_music_message(
            player,
            "Couldn't play a full version of that. Try another SoundCloud search or URL.",
        )

    async def _ensure_player(self, ctx: commands.Context) -> DinkPlayer:
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
                player = await channel.connect(cls=DinkPlayer, self_deaf=True)
            elif not isinstance(player, DinkPlayer):
                await player.disconnect(force=True)
                player = await channel.connect(cls=DinkPlayer, self_deaf=True)
            elif player.channel != channel:
                await player.move_to(channel)
        except asyncio.TimeoutError as exc:
            raise commands.CommandError("Timed out joining the voice channel.") from exc
        except discord.ClientException as exc:
            raise commands.CommandError(f"Could not join voice: {exc}") from exc

        assert isinstance(player, DinkPlayer)
        player.autoplay = wavelink.AutoPlayMode.partial
        player.inactive_timeout = music_inactive_timeout()
        if ctx.channel is not None:
            player.text_channel = ctx.channel
        return player

    async def _expand_short_link(self, url: str) -> str:
        bot_session: aiohttp.ClientSession | None = getattr(
            self.bot, "http_session", None
        )
        owns_session = bot_session is None or bot_session.closed
        active_session = aiohttp.ClientSession() if owns_session else bot_session
        assert active_session is not None
        try:
            return await expand_soundcloud_short_link(url, active_session)
        finally:
            if owns_session:
                await active_session.close()

    @hybrid_command(brief="Play a SoundCloud URL or search query in voice")
    @commands.guild_only()
    @app_commands.describe(query="SoundCloud URL or search text (e.g. lofi hip hop)")
    async def play(self, ctx: commands.Context, *, query: str) -> None:
        """Play audio from a SoundCloud URL or the top search result."""
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

            prepared = to_search_query(query)
            if is_soundcloud_url(prepared):
                host = (urlparse(prepared).hostname or "").lower()
                if host == "on.soundcloud.com":
                    prepared = await self._expand_short_link(prepared)

            try:
                tracks = await wavelink.Playable.search(
                    prepared, source=wavelink.TrackSource.SoundCloud
                )
            except Exception as exc:
                logger.exception("Lavalink SoundCloud search failed for query %r", query)
                await ctx.send(lavalink_user_message(exc))
                return

            if not tracks:
                await ctx.send(
                    "Couldn't find that on SoundCloud. Try a different search or paste a track URL."
                )
                return

            candidates = full_stream_tracks(tracks)[:MAX_PLAY_CANDIDATES]
            if not candidates:
                await ctx.send(
                    "Couldn't find a full streamable version on SoundCloud "
                    "(that result is preview-only). Try another search or URL."
                )
                return

            if player.current is not None or len(player.queue) > 0:
                if len(player.queue) >= MAX_QUEUE_SIZE:
                    await ctx.send(f"Queue is full ({MAX_QUEUE_SIZE} tracks).")
                    return
                track = candidates[0]
                await player.queue.put_wait(track)
                await ctx.send(
                    track_line(track, prefix=f"Queued **#{len(player.queue)}:** ")
                )
                return

            track = player.arm_candidates(candidates, ctx)
            try:
                await player.play(track)
            except Exception as exc:
                player.pending_ctx = None
                logger.exception("Failed to start track for query %r", query)
                await ctx.send(lavalink_user_message(exc))
                return

    @hybrid_command(brief="Skip the current track")
    @commands.guild_only()
    async def skip(self, ctx: commands.Context) -> None:
        player = ctx.voice_client
        if not isinstance(player, DinkPlayer) or not player.playing:
            await ctx.send("Nothing is playing.")
            return
        player.discard_fallbacks()
        await player.skip(force=True)
        await self._play_next_from_queue(player)
        await ctx.send("Skipped.")

    @hybrid_command(brief="Stop playback and clear the queue")
    @commands.guild_only()
    async def stop(self, ctx: commands.Context) -> None:
        player = ctx.voice_client
        if not isinstance(player, DinkPlayer):
            await ctx.send("I'm not in a voice channel.")
            return
        player.discard_fallbacks()
        player.queue.clear()
        await player.stop()
        await ctx.send("Stopped and cleared the queue.")

    @hybrid_command(brief="Show the queue")
    @commands.guild_only()
    async def queue(self, ctx: commands.Context) -> None:
        player = ctx.voice_client
        if not isinstance(player, DinkPlayer) or (
            player.current is None and not player.queue
        ):
            await ctx.send("Queue is empty.")
            return

        lines: list[str] = []
        if player.current is not None:
            lines.append(track_line(player.current, prefix="▶️ **Now playing:** "))
        for index, track in enumerate(list(player.queue)[:20], start=1):
            title = safe_title(getattr(track, "title", None) or "Unknown title")
            duration = format_duration(track_duration_seconds(track))
            lines.append(f"**{index}.** {title} ({duration})")
        remaining = max(0, len(player.queue) - 20)
        if remaining:
            lines.append(f"…and {remaining} more")
        await ctx.send("\n".join(lines))

    @hybrid_command(name="np", brief="Show the track playing now")
    @commands.guild_only()
    async def now_playing(self, ctx: commands.Context) -> None:
        player = ctx.voice_client
        if not isinstance(player, DinkPlayer) or player.current is None:
            await ctx.send("Nothing is playing.")
            return
        await ctx.send(track_line(player.current, prefix="▶️ **Now playing:** "))

    @hybrid_command(brief="Pause playback")
    @commands.guild_only()
    async def pause(self, ctx: commands.Context) -> None:
        player = ctx.voice_client
        if not isinstance(player, DinkPlayer) or not player.playing:
            await ctx.send("Nothing is playing.")
            return
        if player.paused:
            await ctx.send("Already paused.")
            return
        await player.pause(True)
        await ctx.send("Paused.")

    @hybrid_command(brief="Resume playback")
    @commands.guild_only()
    async def resume(self, ctx: commands.Context) -> None:
        player = ctx.voice_client
        if not isinstance(player, DinkPlayer) or not player.paused:
            await ctx.send("Nothing is paused.")
            return
        await player.pause(False)
        await ctx.send("Resumed.")

    @hybrid_command(brief="Leave the voice channel")
    @commands.guild_only()
    async def leave(self, ctx: commands.Context) -> None:
        player = ctx.voice_client
        if not isinstance(player, DinkPlayer):
            await ctx.send("I'm not in a voice channel.")
            return
        await self._disconnect_player(player)
        await ctx.send("Left the voice channel.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
