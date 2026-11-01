"""Unit tests for music helpers and command edge cases (Lavalink/Wavelink SoundCloud)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import wavelink

from cogs.music import (
    ALONE_TIMEOUT,
    INACTIVE_TIMEOUT,
    MAX_QUEUE_SIZE,
    Music,
    _inactive_timeout_seconds,
    _lavalink_user_message,
    expand_soundcloud_short_link,
    format_duration,
    full_stream_tracks,
    is_preview_track,
    is_soundcloud_url,
    pick_playable_track,
    safe_title,
    to_search_query,
)
from tests.reporting import SECTION_COMMANDS


def test_is_soundcloud_url_accepts_common_forms(report):
    cases = [
        ("https://soundcloud.com/artist/track-name", True),
        ("https://www.soundcloud.com/artist/track-name", True),
        ("https://m.soundcloud.com/artist/track-name", True),
        ("https://on.soundcloud.com/abc123", True),
        ("soundcloud.com/artist/track-name", True),
        ("not a url", False),
        ("https://example.com/track", False),
        ("https://soundcloud.com/", False),
        ("lofi hip hop", False),
        ("soundcloud.com/artist/track and more", False),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", False),
    ]
    for value, expected in cases:
        actual = is_soundcloud_url(value)
        report.record(f"is_soundcloud_url({value!r})", expected, actual, section=SECTION_COMMANDS)
        assert actual is expected


def test_to_search_query(report):
    assert to_search_query("lofi hip hop") == "lofi hip hop"
    report.record("search text", "lofi hip hop", to_search_query("lofi hip hop"), section=SECTION_COMMANDS)
    bare = to_search_query("soundcloud.com/artist/track-name")
    report.record("bare url gets scheme", True, bare.startswith("https://"), section=SECTION_COMMANDS)
    assert bare.startswith("https://")
    mobile = to_search_query("https://m.soundcloud.com/artist/track-name?si=abc")
    report.record(
        "mobile host rewritten",
        "https://soundcloud.com/artist/track-name?si=abc",
        mobile,
        section=SECTION_COMMANDS,
    )
    assert mobile == "https://soundcloud.com/artist/track-name?si=abc"
    assert to_search_query("https://www.soundcloud.com/artist/track-name").startswith(
        "https://www.soundcloud.com/"
    )


def test_expand_soundcloud_short_link(report):
    permalink = "https://soundcloud.com/artist/track-name"
    response = MagicMock()
    response.geturl.return_value = permalink
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    with patch("cogs.music.urlopen", return_value=response) as urlopen:
        actual = expand_soundcloud_short_link("https://on.soundcloud.com/abc123")
    report.record("short link expands", permalink, actual, section=SECTION_COMMANDS)
    assert actual == permalink
    urlopen.assert_called_once()
    assert expand_soundcloud_short_link(permalink) == permalink


def test_format_duration(report):
    assert format_duration(None) == "?:??"
    assert format_duration(65) == "1:05"
    assert format_duration(3661) == "1:01:01"
    report.record("format_duration(65)", "1:05", format_duration(65), section=SECTION_COMMANDS)


def test_safe_title(report):
    actual = safe_title("Song [Live] *wow*")
    report.record("safe_title strips brackets", True, "[" not in actual and "]" not in actual, section=SECTION_COMMANDS)
    assert "[" not in actual and "]" not in actual


def test_is_preview_track(report):
    preview = MagicMock()
    preview.identifier = (
        "https://api-v2.soundcloud.com/media/soundcloud:tracks:1/abc/preview/hls"
    )
    preview.uri = "https://soundcloud.com/artist/song"
    full = MagicMock()
    full.identifier = (
        "https://api-v2.soundcloud.com/media/soundcloud:tracks:1/abc/stream/hls"
    )
    full.uri = "https://soundcloud.com/artist/song"
    report.record("preview detected", True, is_preview_track(preview), section=SECTION_COMMANDS)
    report.record("full stream not preview", False, is_preview_track(full), section=SECTION_COMMANDS)
    assert is_preview_track(preview) is True
    assert is_preview_track(full) is False


def test_full_stream_tracks_excludes_previews(report):
    preview = MagicMock()
    preview.identifier = "https://api-v2.soundcloud.com/media/x/preview/hls"
    preview.uri = "https://soundcloud.com/a/preview-song"
    full_a = MagicMock()
    full_a.identifier = "https://api-v2.soundcloud.com/media/a/stream/hls"
    full_a.uri = "https://soundcloud.com/a/full-a"
    full_b = MagicMock()
    full_b.identifier = "https://api-v2.soundcloud.com/media/b/stream/hls"
    full_b.uri = "https://soundcloud.com/a/full-b"
    full = full_stream_tracks([preview, full_a, full_b])
    report.record("full only", [full_a, full_b], full, section=SECTION_COMMANDS)
    assert full == [full_a, full_b]
    assert pick_playable_track([preview, full_a, full_b]) is full_a
    assert pick_playable_track([preview]) is None


def test_lavalink_user_message_offline(report):
    msg = _lavalink_user_message(Exception("No nodes currently available / node connect failed"))
    report.record("offline message", "backend is offline", msg, section=SECTION_COMMANDS)
    assert "offline" in msg.lower()


def test_lavalink_user_message_generic(report):
    msg = _lavalink_user_message(Exception("something else"))
    report.record("generic message", "Couldn't find", msg, section=SECTION_COMMANDS)
    assert "couldn't find" in msg.lower()
    assert "soundcloud" in msg.lower()


async def test_play_requires_voice_channel(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 1
    mock_ctx.guild.me = None
    mock_ctx.author.voice = None
    mock_ctx.voice_client = None

    cog = Music(mock_bot)
    await cog.play.callback(cog, mock_ctx, query="lofi")
    actual = mock_ctx.send.call_args.args[0]
    report.record("play without voice", "Join a voice channel", actual, section=SECTION_COMMANDS)
    assert "Join a voice channel" in actual


async def test_play_queues_when_busy(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 2
    mock_ctx.guild.me = None
    mock_ctx.author.voice = MagicMock()
    mock_ctx.author.voice.channel = MagicMock()
    mock_ctx.author.display_name = "Alex"

    player = MagicMock(spec=wavelink.Player)
    player.playing = True
    player.paused = False
    player.current = MagicMock()
    player.queue = MagicMock()
    player.queue.__len__ = MagicMock(return_value=1)
    player.queue.put_wait = AsyncMock()
    mock_ctx.voice_client = player

    track = MagicMock()
    track.title = "Song"
    track.length = 120000
    track.uri = "https://soundcloud.com/artist/song"
    track.identifier = "https://api-v2.soundcloud.com/media/x/stream/hls"

    cog = Music(mock_bot)
    with patch.object(cog, "_ensure_player", AsyncMock(return_value=player)):
        with patch("cogs.music.wavelink.Playable.search", AsyncMock(return_value=[track])) as search:
            await cog.play.callback(cog, mock_ctx, query="lofi")
            search.assert_awaited()
            assert search.await_args.kwargs.get("source") == wavelink.TrackSource.SoundCloud

    player.queue.put_wait.assert_awaited_once_with(track)
    player.play.assert_not_called()
    actual = mock_ctx.send.call_args.args[0]
    report.record("queued while busy", True, "Queued" in actual, section=SECTION_COMMANDS)
    assert "Queued" in actual
    assert " — " not in actual
    assert "Alex" not in actual
    report.record("queued without requester", True, True, section=SECTION_COMMANDS)


async def test_play_starts_when_idle(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 3
    mock_ctx.guild.me = None
    mock_ctx.author.voice = MagicMock()
    mock_ctx.author.display_name = "Alex"

    player = MagicMock(spec=wavelink.Player)
    player.playing = False
    player.paused = False
    player.current = None
    player.queue = MagicMock()
    player.queue.__bool__ = MagicMock(return_value=False)
    player.queue.__len__ = MagicMock(return_value=0)
    player.play = AsyncMock()
    mock_ctx.voice_client = player

    track = MagicMock()
    track.title = "Lofi Beat"
    track.length = 213000
    track.uri = "https://soundcloud.com/artist/lofi-beat"
    track.identifier = "https://api-v2.soundcloud.com/media/x/stream/hls"

    cog = Music(mock_bot)
    with patch.object(cog, "_ensure_player", AsyncMock(return_value=player)):
        with patch("cogs.music.wavelink.Playable.search", AsyncMock(return_value=[track])):
            await cog.play.callback(cog, mock_ctx, query="lofi")

    player.play.assert_awaited_once_with(track)
    assert player.music_pending_ctx is mock_ctx
    # Announce waits for track start so 404 fallbacks stay silent.
    mock_ctx.send.assert_not_called()

    start = MagicMock()
    start.player = player
    start.track = track
    await cog.on_wavelink_track_start(start)

    actual = mock_ctx.send.call_args.args[0]
    report.record("now playing", True, "Now playing" in actual, section=SECTION_COMMANDS)
    assert "Now playing" in actual
    assert "<https://soundcloud.com/artist/lofi-beat>" in actual
    assert " — " not in actual
    assert "Alex" not in actual
    report.record("url wrapped to hide embed", True, True, section=SECTION_COMMANDS)
    report.record("no requester name", True, True, section=SECTION_COMMANDS)


async def test_play_skips_preview_and_keeps_full_fallbacks(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 4
    mock_ctx.guild.me = None
    mock_ctx.author.voice = MagicMock()
    mock_ctx.author.display_name = "Alex"

    player = MagicMock(spec=wavelink.Player)
    player.playing = False
    player.paused = False
    player.current = None
    player.queue = MagicMock()
    player.queue.__bool__ = MagicMock(return_value=False)
    player.queue.__len__ = MagicMock(return_value=0)
    player.play = AsyncMock()
    mock_ctx.voice_client = player

    preview = MagicMock()
    preview.title = "Official Preview"
    preview.length = 180000
    preview.uri = "https://soundcloud.com/label/official"
    preview.identifier = "https://api-v2.soundcloud.com/media/x/preview/hls"
    full_a = MagicMock()
    full_a.title = "Fan Upload A"
    full_a.length = 180000
    full_a.uri = "https://soundcloud.com/fan/a"
    full_a.identifier = "https://api-v2.soundcloud.com/media/a/stream/hls"
    full_b = MagicMock()
    full_b.title = "Fan Upload B"
    full_b.length = 180000
    full_b.uri = "https://soundcloud.com/fan/b"
    full_b.identifier = "https://api-v2.soundcloud.com/media/b/stream/hls"

    cog = Music(mock_bot)
    with patch.object(cog, "_ensure_player", AsyncMock(return_value=player)):
        with patch(
            "cogs.music.wavelink.Playable.search",
            AsyncMock(return_value=[preview, full_a, full_b]),
        ):
            await cog.play.callback(cog, mock_ctx, query="tubthumping")

    player.play.assert_awaited_once_with(full_a)
    assert player.music_fallbacks == [full_b]
    assert player.music_pending_ctx is mock_ctx
    mock_ctx.send.assert_not_called()

    start = MagicMock()
    start.player = player
    start.track = full_a
    await cog.on_wavelink_track_start(start)

    actual = mock_ctx.send.call_args.args[0]
    report.record("preferred full stream", True, "Fan Upload A" in actual, section=SECTION_COMMANDS)
    assert "Fan Upload A" in actual
    assert "preview" not in actual.lower()


async def test_play_rejects_preview_only_results(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 5
    mock_ctx.guild.me = None
    mock_ctx.author.voice = MagicMock()
    mock_ctx.author.display_name = "Alex"

    player = MagicMock(spec=wavelink.Player)
    player.playing = False
    player.paused = False
    player.current = None
    player.queue = MagicMock()
    player.queue.__bool__ = MagicMock(return_value=False)
    player.queue.__len__ = MagicMock(return_value=0)
    player.play = AsyncMock()
    mock_ctx.voice_client = player

    preview = MagicMock()
    preview.title = "Safety Dance"
    preview.length = 30000
    preview.uri = "https://soundcloud.com/men-without-hats/safety-dance-1"
    preview.identifier = "https://api-v2.soundcloud.com/media/x/preview/hls"

    cog = Music(mock_bot)
    with patch.object(cog, "_ensure_player", AsyncMock(return_value=player)):
        with patch("cogs.music.wavelink.Playable.search", AsyncMock(return_value=[preview])):
            await cog.play.callback(cog, mock_ctx, query="safety dance")

    player.play.assert_not_called()
    actual = mock_ctx.send.call_args.args[0]
    report.record("preview-only rejected", True, "preview-only" in actual.lower(), section=SECTION_COMMANDS)
    assert "preview-only" in actual.lower()


async def test_track_exception_plays_next_full_stream(report, mock_bot):
    player = MagicMock(spec=wavelink.Player)
    player.play = AsyncMock()
    channel = MagicMock()
    channel.send = AsyncMock()
    player.music_text_channel = channel
    player.music_pending_ctx = None
    player.music_status_message = None
    player.music_announce_fallback = False
    preview = MagicMock()
    preview.title = "Preview Hit"
    preview.identifier = "https://api-v2.soundcloud.com/media/x/preview/hls"
    full_b = MagicMock()
    full_b.title = "Full Backup"
    full_b.length = 180000
    full_b.uri = "https://soundcloud.com/fan/b"
    full_b.identifier = "https://api-v2.soundcloud.com/media/b/stream/hls"
    # Preview must be skipped; next full stream should play.
    player.music_fallbacks = [preview, full_b]

    failed = MagicMock()
    failed.title = "Broken Full"
    payload = MagicMock()
    payload.exception = {"message": "Invalid status code for soundcloud stream: 404"}
    payload.track = failed
    payload.player = player

    cog = Music(mock_bot)
    await cog.on_wavelink_track_exception(payload)

    player.play.assert_awaited_once_with(full_b)
    assert player.music_announce_fallback is True
    # Failed candidates stay silent until a fallback actually starts.
    channel.send.assert_not_called()

    start = MagicMock()
    start.player = player
    start.track = full_b
    await cog.on_wavelink_track_start(start)

    sent = channel.send.await_args.args[0]
    report.record("fallback message", True, "Now playing" in sent, section=SECTION_COMMANDS)
    assert "Now playing" in sent
    assert "Playing instead" not in sent
    assert "Full Backup" in sent
    assert " — " not in sent
    report.record("no requester suffix", True, True, section=SECTION_COMMANDS)


async def test_track_exception_hides_failed_announce(report, mock_bot):
    player = MagicMock(spec=wavelink.Player)
    player.play = AsyncMock()
    channel = MagicMock()
    channel.send = AsyncMock()
    player.music_text_channel = channel
    player.music_pending_ctx = None
    failed_msg = MagicMock()
    failed_msg.delete = AsyncMock()
    player.music_status_message = failed_msg
    player.music_announce_fallback = False
    full_b = MagicMock()
    full_b.title = "Full Backup"
    full_b.length = 180000
    full_b.uri = "https://soundcloud.com/fan/b"
    full_b.identifier = "https://api-v2.soundcloud.com/media/b/stream/hls"
    player.music_fallbacks = [full_b]

    payload = MagicMock()
    payload.exception = {"message": "404"}
    payload.track = MagicMock(title="Broken")
    payload.player = player

    cog = Music(mock_bot)
    await cog.on_wavelink_track_exception(payload)

    failed_msg.delete.assert_awaited_once()
    assert player.music_status_message is None
    channel.send.assert_not_called()
    report.record("failed announce deleted", True, True, section=SECTION_COMMANDS)


async def test_queue_empty_message(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 42
    mock_ctx.voice_client = None
    cog = Music(mock_bot)
    await cog.queue.callback(cog, mock_ctx)
    actual = mock_ctx.send.call_args.args[0]
    report.record("empty queue", "Queue is empty.", actual, section=SECTION_COMMANDS)
    assert actual == "Queue is empty."


async def test_np_when_idle(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 42
    mock_ctx.voice_client = None
    cog = Music(mock_bot)
    await cog.now_playing.callback(cog, mock_ctx)
    actual = mock_ctx.send.call_args.args[0]
    report.record("np idle", "Nothing is playing", actual, section=SECTION_COMMANDS)
    assert "Nothing is playing" in actual


async def test_skip_when_idle(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.voice_client = None
    cog = Music(mock_bot)
    await cog.skip.callback(cog, mock_ctx)
    actual = mock_ctx.send.call_args.args[0]
    report.record("skip idle", "Nothing is playing", actual, section=SECTION_COMMANDS)
    assert "Nothing is playing" in actual


async def test_skip_clears_fallbacks_and_plays_queue(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    player = MagicMock(spec=wavelink.Player)
    player.playing = True
    queued = MagicMock()
    queued.title = "Queued Song"
    player.queue = MagicMock()
    player.queue.__bool__ = MagicMock(return_value=True)
    player.queue.get = MagicMock(return_value=queued)
    player.skip = AsyncMock()
    player.play = AsyncMock()
    player.music_fallbacks = [MagicMock(title="Search Hit B"), MagicMock(title="Search Hit C")]
    player.music_announce_fallback = True
    mock_ctx.voice_client = player

    cog = Music(mock_bot)
    await cog.skip.callback(cog, mock_ctx)

    assert player.music_fallbacks == []
    assert player.music_announce_fallback is False
    player.skip.assert_awaited_once_with(force=True)
    player.play.assert_awaited_once_with(queued)
    actual = mock_ctx.send.call_args.args[0]
    report.record("skip advances queue", "Skipped.", actual, section=SECTION_COMMANDS)
    assert actual == "Skipped."


def test_max_queue_size_constant(report):
    report.record("MAX_QUEUE_SIZE", 50, MAX_QUEUE_SIZE, section=SECTION_COMMANDS)
    assert MAX_QUEUE_SIZE == 50


def test_inactive_timeout_env(monkeypatch, report):
    monkeypatch.delenv("MUSIC_INACTIVE_TIMEOUT", raising=False)
    assert _inactive_timeout_seconds() == 300
    monkeypatch.setenv("MUSIC_INACTIVE_TIMEOUT", " 120 ")
    assert _inactive_timeout_seconds() == 120
    monkeypatch.setenv("MUSIC_INACTIVE_TIMEOUT", "nope")
    assert _inactive_timeout_seconds() == 300
    monkeypatch.setenv("MUSIC_INACTIVE_TIMEOUT", "0")
    assert _inactive_timeout_seconds() == 300
    monkeypatch.setenv("MUSIC_INACTIVE_TIMEOUT", "-5")
    assert _inactive_timeout_seconds() == 300
    monkeypatch.delenv("MUSIC_INACTIVE_TIMEOUT", raising=False)
    assert _inactive_timeout_seconds() == 300
    assert ALONE_TIMEOUT == 90
    report.record("idle timeout default", 300, _inactive_timeout_seconds(), section=SECTION_COMMANDS)


def _voice_player(*, guild_id: int, humans: int, playing: bool = False):
    """Player sitting in a channel with ``humans`` non-bot members plus the bot."""
    guild = MagicMock()
    guild.id = guild_id
    members = [MagicMock(bot=True)]
    members.extend(MagicMock(bot=False) for _ in range(humans))
    channel = MagicMock()
    channel.members = members
    player = MagicMock(spec=wavelink.Player)
    player.playing = playing
    player.connected = True
    player.paused = False
    player.channel = channel
    player.guild = guild
    player.queue = MagicMock()
    player.disconnect = AsyncMock()
    player.music_fallbacks = [MagicMock(title="leftover")]
    player.music_announce_fallback = True
    player.music_pending_ctx = MagicMock()
    guild.voice_client = player
    return guild, player


async def test_cog_load_sets_inactive_player_timeout(mock_bot, monkeypatch, report):
    monkeypatch.setenv("LAVALINK_URI", "http://lavalink.test:2333")
    monkeypatch.setenv("LAVALINK_PASSWORD", "pw")
    cog = Music(mock_bot)
    with patch("cogs.music.wavelink.Node", return_value=MagicMock()) as node:
        with patch("cogs.music.wavelink.Pool.connect", new=AsyncMock()) as connect:
            await cog.cog_load()
    node.assert_called_once_with(
        identifier="main",
        uri="http://lavalink.test:2333",
        password="pw",
        inactive_player_timeout=INACTIVE_TIMEOUT,
    )
    connect.assert_awaited_once()
    assert connect.await_args.kwargs["client"] is mock_bot
    report.record(
        "node idle timeout",
        INACTIVE_TIMEOUT,
        node.call_args.kwargs["inactive_player_timeout"],
        section=SECTION_COMMANDS,
    )


async def test_ensure_player_arms_inactive_timeout(mock_bot, mock_ctx, report):
    channel = MagicMock()
    perms = MagicMock()
    perms.connect = True
    perms.speak = True
    channel.permissions_for.return_value = perms
    player = MagicMock(spec=wavelink.Player)
    player.channel = channel
    channel.connect = AsyncMock(return_value=player)

    mock_ctx.guild = MagicMock()
    mock_ctx.guild.me = MagicMock()
    mock_ctx.author.voice = MagicMock(channel=channel)
    mock_ctx.voice_client = None
    mock_ctx.channel = MagicMock()

    cog = Music(mock_bot)
    got = await cog._ensure_player(mock_ctx)
    assert got is player
    assert player.inactive_timeout == INACTIVE_TIMEOUT
    assert player.autoplay is wavelink.AutoPlayMode.partial
    channel.connect.assert_awaited_once()
    report.record("player idle timeout", INACTIVE_TIMEOUT, player.inactive_timeout, section=SECTION_COMMANDS)


async def test_inactive_player_disconnects_and_clears(mock_bot, report):
    _guild, player = _voice_player(guild_id=11, humans=1, playing=False)
    cog = Music(mock_bot)
    await cog.on_wavelink_inactive_player(player)

    player.queue.clear.assert_called_once()
    assert player.music_fallbacks == []
    assert player.music_announce_fallback is False
    assert player.music_pending_ctx is None
    player.disconnect.assert_awaited_once()
    report.record("idle disconnect", True, player.disconnect.await_count == 1, section=SECTION_COMMANDS)


async def test_inactive_player_keeps_a_loaded_track(mock_bot, report):
    _guild, player = _voice_player(guild_id=12, humans=1, playing=True)
    cog = Music(mock_bot)
    await cog.on_wavelink_inactive_player(player)
    player.disconnect.assert_not_called()
    player.queue.clear.assert_not_called()
    report.record("playing skips idle leave", True, True, section=SECTION_COMMANDS)


async def test_inactive_player_cancels_alone_wait(mock_bot):
    guild, player = _voice_player(guild_id=13, humans=0, playing=False)
    mock_bot.get_guild.return_value = guild
    member = MagicMock()
    member.guild = guild
    cog = Music(mock_bot)
    with patch("cogs.music.ALONE_TIMEOUT", 30):
        await cog.on_voice_state_update(member, MagicMock(), MagicMock())
        alone = cog._alone_tasks[guild.id]
        await cog.on_wavelink_inactive_player(player)
        with pytest.raises(asyncio.CancelledError):
            await alone
    player.disconnect.assert_awaited_once()
    assert guild.id not in cog._alone_tasks


async def test_leaves_when_alone(mock_bot, report):
    guild, player = _voice_player(guild_id=14, humans=0, playing=True)
    mock_bot.get_guild.return_value = guild
    member = MagicMock()
    member.guild = guild
    cog = Music(mock_bot)
    with patch("cogs.music.ALONE_TIMEOUT", 0):
        await cog.on_voice_state_update(member, MagicMock(), MagicMock())
        await cog._alone_tasks[guild.id]

    player.disconnect.assert_awaited_once()
    player.queue.clear.assert_called_once()
    assert player.music_fallbacks == []
    assert player.music_pending_ctx is None
    assert guild.id not in cog._alone_tasks
    report.record("alone disconnect", True, player.disconnect.await_count == 1, section=SECTION_COMMANDS)


async def test_alone_wait_aborts_when_a_human_returns(mock_bot, report):
    guild, player = _voice_player(guild_id=15, humans=0, playing=True)
    mock_bot.get_guild.return_value = guild
    member = MagicMock()
    member.guild = guild
    cog = Music(mock_bot)
    with patch("cogs.music.ALONE_TIMEOUT", 0):
        await cog.on_voice_state_update(member, MagicMock(), MagicMock())
        task = cog._alone_tasks[guild.id]
        player.channel.members.append(MagicMock(bot=False))
        await task
    player.disconnect.assert_not_called()
    report.record("human returned", True, player.disconnect.await_count == 0, section=SECTION_COMMANDS)


async def test_alone_wait_cancelled_when_someone_joins(mock_bot):
    guild, player = _voice_player(guild_id=16, humans=0)
    mock_bot.get_guild.return_value = guild
    member = MagicMock()
    member.guild = guild
    cog = Music(mock_bot)
    with patch("cogs.music.ALONE_TIMEOUT", 30):
        await cog.on_voice_state_update(member, MagicMock(), MagicMock())
        task = cog._alone_tasks[guild.id]
        player.channel.members.append(MagicMock(bot=False))
        await cog.on_voice_state_update(member, MagicMock(), MagicMock())
        with pytest.raises(asyncio.CancelledError):
            await task
    player.disconnect.assert_not_called()
    assert cog._alone_tasks == {}


async def test_voice_activity_with_humans_does_not_arm_alone_timer(mock_bot):
    guild, player = _voice_player(guild_id=17, humans=2, playing=True)
    member = MagicMock()
    member.guild = guild
    cog = Music(mock_bot)
    await cog.on_voice_state_update(member, MagicMock(), MagicMock())
    assert cog._alone_tasks == {}
    player.disconnect.assert_not_called()


async def test_repeated_alone_updates_keep_one_timer(mock_bot):
    guild, _player = _voice_player(guild_id=18, humans=0)
    member = MagicMock()
    member.guild = guild
    cog = Music(mock_bot)
    with patch("cogs.music.ALONE_TIMEOUT", 30):
        await cog.on_voice_state_update(member, MagicMock(), MagicMock())
        first = cog._alone_tasks[guild.id]
        await cog.on_voice_state_update(member, MagicMock(), MagicMock())
        assert cog._alone_tasks[guild.id] is first
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first


async def test_leave_clears_player_state(mock_bot, mock_ctx, report):
    guild, player = _voice_player(guild_id=19, humans=1, playing=True)
    mock_ctx.guild = guild
    mock_ctx.voice_client = player
    cog = Music(mock_bot)
    await cog.leave.callback(cog, mock_ctx)
    player.queue.clear.assert_called_once()
    assert player.music_fallbacks == []
    assert player.music_pending_ctx is None
    player.disconnect.assert_awaited_once()
    actual = mock_ctx.send.call_args.args[0]
    report.record("leave message", "Left the voice channel.", actual, section=SECTION_COMMANDS)
    assert actual == "Left the voice channel."
