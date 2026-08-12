"""Unit tests for music helpers and command edge cases (Lavalink/Wavelink SoundCloud)."""

from unittest.mock import AsyncMock, MagicMock, patch

import wavelink

from cogs.music import (
    MAX_QUEUE_SIZE,
    Music,
    _lavalink_user_message,
    format_duration,
    is_soundcloud_url,
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


def test_format_duration(report):
    assert format_duration(None) == "?:??"
    assert format_duration(65) == "1:05"
    assert format_duration(3661) == "1:01:01"
    report.record("format_duration(65)", "1:05", format_duration(65), section=SECTION_COMMANDS)


def test_safe_title(report):
    actual = safe_title("Song [Live] *wow*")
    report.record("safe_title strips brackets", True, "[" not in actual and "]" not in actual, section=SECTION_COMMANDS)
    assert "[" not in actual and "]" not in actual


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
    track.identifier = "123"

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
    track.identifier = "456"

    cog = Music(mock_bot)
    with patch.object(cog, "_ensure_player", AsyncMock(return_value=player)):
        with patch("cogs.music.wavelink.Playable.search", AsyncMock(return_value=[track])):
            await cog.play.callback(cog, mock_ctx, query="lofi")

    player.play.assert_awaited_once_with(track)
    actual = mock_ctx.send.call_args.args[0]
    report.record("now playing", True, "Now playing" in actual, section=SECTION_COMMANDS)
    assert "Now playing" in actual


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


def test_max_queue_size_constant(report):
    report.record("MAX_QUEUE_SIZE", 50, MAX_QUEUE_SIZE, section=SECTION_COMMANDS)
    assert MAX_QUEUE_SIZE == 50
