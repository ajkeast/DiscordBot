"""Unit tests for music helpers and command edge cases."""

from unittest.mock import AsyncMock, MagicMock, patch

from cogs.music import (
    Music,
    Track,
    extract_track_info,
    format_duration,
    is_youtube_url,
    to_ydl_query,
)
from tests.reporting import SECTION_COMMANDS


def test_is_youtube_url_accepts_common_forms(report):
    cases = [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", True),
        ("https://youtu.be/dQw4w9WgXcQ", True),
        ("youtube.com/watch?v=dQw4w9WgXcQ", True),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", True),
        ("tubthumping", False),
        ("https://example.com/watch?v=dQw4w9WgXcQ", False),
        ("https://vimeo.com/123", False),
    ]
    for value, expected in cases:
        actual = is_youtube_url(value)
        report.record(f"is_youtube_url({value!r})", expected, actual, section=SECTION_COMMANDS)
        assert actual is expected


def test_to_ydl_query_url_vs_search(report):
    url = "https://youtu.be/dQw4w9WgXcQ"
    assert to_ydl_query(url) == url
    report.record("to_ydl_query(url)", url, to_ydl_query(url), section=SECTION_COMMANDS)

    search = to_ydl_query("tubthumping")
    expected = "ytsearch1:tubthumping"
    report.record("to_ydl_query(search)", expected, search, section=SECTION_COMMANDS)
    assert search == expected

    bare = to_ydl_query("youtube.com/watch?v=dQw4w9WgXcQ")
    assert bare.startswith("https://")
    report.record("to_ydl_query(bare host)", "https://…", bare, section=SECTION_COMMANDS)


def test_format_duration(report):
    assert format_duration(None) == "?:??"
    assert format_duration(65) == "1:05"
    assert format_duration(3661) == "1:01:01"
    report.record("format_duration(65)", "1:05", format_duration(65), section=SECTION_COMMANDS)


@patch("cogs.music.yt_dlp.YoutubeDL")
def test_extract_track_info_search_uses_first_entry(mock_ydl_cls, report):
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.return_value = {
        "entries": [
            {
                "title": "Tubthumping",
                "webpage_url": "https://www.youtube.com/watch?v=abc",
                "duration": 271,
            }
        ]
    }
    mock_ydl_cls.return_value = mock_ydl

    info = extract_track_info("tubthumping")
    mock_ydl.extract_info.assert_called_once_with("ytsearch1:tubthumping", download=False)
    assert info["title"] == "Tubthumping"
    assert info["webpage_url"] == "https://www.youtube.com/watch?v=abc"
    report.record("extract search title", "Tubthumping", info["title"], section=SECTION_COMMANDS)


async def test_play_requires_voice_channel(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 1
    mock_ctx.author.voice = None
    mock_ctx.voice_client = None

    cog = Music(mock_bot)
    await cog.play.callback(cog, mock_ctx, query="tubthumping")
    actual = mock_ctx.send.call_args.args[0]
    report.record("play without VC", "Join a voice channel", actual, section=SECTION_COMMANDS)
    assert "Join a voice channel" in actual


async def test_queue_empty_message(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 42
    cog = Music(mock_bot)
    await cog.queue.callback(cog, mock_ctx)
    actual = mock_ctx.send.call_args.args[0]
    report.record("empty queue", "Queue is empty.", actual, section=SECTION_COMMANDS)
    assert actual == "Queue is empty."


async def test_np_when_idle(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 42
    cog = Music(mock_bot)
    await cog.now_playing.callback(cog, mock_ctx)
    actual = mock_ctx.send.call_args.args[0]
    report.record("np idle", "Nothing is playing", actual, section=SECTION_COMMANDS)
    assert "Nothing is playing" in actual


async def test_queue_lists_current_and_upcoming(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 7
    cog = Music(mock_bot)
    player = cog._player(7)
    player.current = Track(
        title="Now Song",
        webpage_url="https://youtu.be/now",
        duration=120,
        requester_id=1,
        requester_name="Alice",
    )
    player.queue.append(
        Track(
            title="Next Song",
            webpage_url="https://youtu.be/next",
            duration=90,
            requester_id=2,
            requester_name="Bob",
        )
    )
    await cog.queue.callback(cog, mock_ctx)
    actual = mock_ctx.send.call_args.args[0]
    report.record("queue contents", "Now Song + Next Song", actual, section=SECTION_COMMANDS)
    assert "Now Song" in actual
    assert "Next Song" in actual
    assert "Bob" in actual
