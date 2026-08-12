"""Unit tests for music helpers and command edge cases."""

from unittest.mock import AsyncMock, MagicMock, patch

from cogs.music import (
    MAX_QUEUE_SIZE,
    Music,
    Track,
    _webpage_url_from_entry,
    _yt_dlp_user_message,
    extract_stream_url,
    extract_track_info,
    format_duration,
    is_youtube_url,
    safe_title,
    to_ydl_query,
    ydl_options,
)
from tests.reporting import SECTION_COMMANDS


def _ydl_mock(extract_return):
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.return_value = extract_return
    return mock_ydl


def test_is_youtube_url_accepts_common_forms(report):
    cases = [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", True),
        ("https://youtu.be/dQw4w9WgXcQ", True),
        ("youtube.com/watch?v=dQw4w9WgXcQ", True),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", True),
        ("https://www.youtube.com/shorts/abc123xyz00", True),
        ("tubthumping", False),
        ("never gonna give you up", False),
        ("https://example.com/watch?v=dQw4w9WgXcQ", False),
        ("https://vimeo.com/123", False),
        ("https://youtube.com/", False),
        ("check out https://youtu.be/dQw4w9WgXcQ please", False),
        ("youtube.com/watch?v=dQw4w9WgXcQ and more", False),
    ]
    for value, expected in cases:
        actual = is_youtube_url(value)
        report.record(f"is_youtube_url({value!r})", expected, actual, section=SECTION_COMMANDS)
        assert actual is expected, value


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

    # Sentences must not be treated as URLs.
    assert to_ydl_query("play https://youtu.be/dQw4w9WgXcQ").startswith("ytsearch1:")


def test_format_duration(report):
    assert format_duration(None) == "?:??"
    assert format_duration(0) == "0:00"
    assert format_duration(65) == "1:05"
    assert format_duration(3661) == "1:01:01"
    report.record("format_duration(65)", "1:05", format_duration(65), section=SECTION_COMMANDS)


def test_safe_title_strips_link_breakers(report):
    actual = safe_title("Song [Live] (Official)")
    report.record("safe_title", "no brackets", actual, section=SECTION_COMMANDS)
    assert "[" not in actual and "]" not in actual


def test_webpage_url_prefers_watch_url_not_cdn(report):
    entry = {
        "id": "dQw4w9WgXcQ",
        "url": "https://googlevideo.com/videoplayback?expire=1",
        "title": "Rick Roll",
    }
    url = _webpage_url_from_entry(entry)
    report.record("webpage from id", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", url, section=SECTION_COMMANDS)
    assert url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@patch("cogs.music.yt_dlp.YoutubeDL")
def test_extract_track_info_search_uses_first_entry(mock_ydl_cls, report):
    mock_ydl_cls.return_value = _ydl_mock({
        "entries": [
            {
                "id": "abc123xyz00",
                "title": "Tubthumping",
                "webpage_url": "https://www.youtube.com/watch?v=abc123xyz00",
                "duration": 271,
                "url": "https://googlevideo.com/stream",
            }
        ]
    })

    info = extract_track_info("tubthumping")
    mock_ydl_cls.return_value.extract_info.assert_called_once_with(
        "ytsearch1:tubthumping", download=False
    )
    assert info["title"] == "Tubthumping"
    assert info["webpage_url"] == "https://www.youtube.com/watch?v=abc123xyz00"
    report.record("extract search title", "Tubthumping", info["title"], section=SECTION_COMMANDS)


@patch("cogs.music.yt_dlp.YoutubeDL")
def test_extract_track_info_empty_search_raises(mock_ydl_cls, report):
    mock_ydl_cls.return_value = _ydl_mock({"entries": []})
    try:
        extract_track_info("zzznonsensequeryzzz")
        raised = False
    except ValueError:
        raised = True
    report.record("empty search raises", True, raised, section=SECTION_COMMANDS)
    assert raised


def test_ydl_options_attaches_cookiefile(tmp_path, monkeypatch, report):
    cookies = tmp_path / "youtube.cookies"
    runtime = tmp_path / "runtime.cookies"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", str(cookies))
    monkeypatch.setattr("cogs.music._RUNTIME_COOKIE_PATH", str(runtime))
    opts = ydl_options()
    report.record("cookiefile set", str(runtime), opts.get("cookiefile"), section=SECTION_COMMANDS)
    assert opts["cookiefile"] == str(runtime)
    assert runtime.is_file()


def test_ydl_options_skips_missing_cookiefile(tmp_path, monkeypatch, report):
    missing = tmp_path / "missing.cookies"
    monkeypatch.setenv("YOUTUBE_COOKIES_FILE", str(missing))
    opts = ydl_options()
    report.record("missing cookiefile omitted", False, "cookiefile" in opts, section=SECTION_COMMANDS)
    assert "cookiefile" not in opts


def test_yt_dlp_user_message_bot_check(report):
    msg = _yt_dlp_user_message(
        Exception("ERROR: [youtube] abc: Sign in to confirm you’re not a bot")
    )
    report.record("bot check message", "refresh cookies", msg, section=SECTION_COMMANDS)
    assert "cookies" in msg.lower()


@patch("cogs.music.yt_dlp.YoutubeDL")
def test_extract_stream_url_rejects_non_youtube(mock_ydl_cls, report):
    try:
        extract_stream_url("https://example.com/audio.mp3")
        rejected = False
    except ValueError:
        rejected = True
    report.record("reject non-youtube stream", True, rejected, section=SECTION_COMMANDS)
    assert rejected
    mock_ydl_cls.assert_not_called()


async def test_play_requires_voice_channel(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 1
    mock_ctx.guild.me = None
    mock_ctx.author.voice = None
    mock_ctx.voice_client = None

    cog = Music(mock_bot)
    await cog.play.callback(cog, mock_ctx, query="tubthumping")
    actual = mock_ctx.send.call_args.args[0]
    report.record("play without VC", "Join a voice channel", actual, section=SECTION_COMMANDS)
    assert "Join a voice channel" in actual


async def test_play_queues_when_busy(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 9
    mock_ctx.guild.me = None
    voice_state = MagicMock()
    voice_state.channel = MagicMock()
    mock_ctx.author.voice = voice_state
    mock_ctx.voice_client = MagicMock()
    mock_ctx.voice_client.is_playing.return_value = True
    mock_ctx.voice_client.is_paused.return_value = False
    mock_ctx.voice_client.channel = voice_state.channel

    cog = Music(mock_bot)
    player = cog._player(9)
    player.current = Track("Current", "https://youtu.be/cur", 10, 1, "A")

    with patch(
        "cogs.music.extract_track_info",
        return_value={
            "title": "Next Up",
            "webpage_url": "https://www.youtube.com/watch?v=next123456",
            "duration": 12,
        },
    ):
        await cog.play.callback(cog, mock_ctx, query="next song")

    actual = mock_ctx.send.call_args.args[0]
    report.record("play while busy", "Queued", actual, section=SECTION_COMMANDS)
    assert "Queued" in actual
    assert len(player.queue) == 1
    assert player.queue[0].title == "Next Up"


async def test_play_respects_queue_limit(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 11
    mock_ctx.guild.me = None
    voice_state = MagicMock()
    voice_state.channel = MagicMock()
    mock_ctx.author.voice = voice_state
    mock_ctx.voice_client = MagicMock()
    mock_ctx.voice_client.is_playing.return_value = True
    mock_ctx.voice_client.is_paused.return_value = False
    mock_ctx.voice_client.channel = voice_state.channel

    cog = Music(mock_bot)
    player = cog._player(11)
    player.current = Track("Current", "https://youtu.be/cur", 10, 1, "A")
    for i in range(MAX_QUEUE_SIZE):
        player.queue.append(Track(f"T{i}", "https://youtu.be/x", 1, 1, "A"))

    with patch(
        "cogs.music.extract_track_info",
        return_value={
            "title": "Overflow",
            "webpage_url": "https://www.youtube.com/watch?v=overflow01",
            "duration": 1,
        },
    ):
        await cog.play.callback(cog, mock_ctx, query="overflow")

    actual = mock_ctx.send.call_args.args[0]
    report.record("queue full", "Queue is full", actual, section=SECTION_COMMANDS)
    assert "Queue is full" in actual
    assert len(player.queue) == MAX_QUEUE_SIZE


async def test_stop_bumps_generation_and_clears(report, mock_bot, mock_ctx):
    mock_ctx.guild = MagicMock()
    mock_ctx.guild.id = 5
    mock_ctx.voice_client = MagicMock()
    mock_ctx.voice_client.is_playing.return_value = True
    mock_ctx.voice_client.is_paused.return_value = False

    cog = Music(mock_bot)
    player = cog._player(5)
    player.current = Track("Now", "https://youtu.be/n", 1, 1, "A")
    player.queue.append(Track("Next", "https://youtu.be/x", 1, 1, "B"))
    before = player.generation

    await cog.stop.callback(cog, mock_ctx)

    report.record("stop generation", before + 1, player.generation, section=SECTION_COMMANDS)
    assert player.generation == before + 1
    assert player.current is None
    assert len(player.queue) == 0
    mock_ctx.voice_client.stop.assert_called_once()


async def test_play_next_aborts_stale_generation(report, mock_bot):
    guild = MagicMock()
    guild.id = 3
    voice = MagicMock()
    voice.is_connected.return_value = True
    voice.is_playing.return_value = False
    voice.is_paused.return_value = False
    guild.voice_client = voice
    mock_bot.get_guild.return_value = guild

    cog = Music(mock_bot)
    player = cog._player(3)
    track = Track("Song", "https://www.youtube.com/watch?v=abc123xyz00", 10, 1, "A")
    player.queue.append(track)

    def slow_extract(_url):
        # Simulate /stop during extract: bump generation before play starts.
        player.generation += 1
        return "https://cdn.example/stream"

    with patch("cogs.music.extract_stream_url", side_effect=slow_extract):
        with patch("cogs.music.discord.FFmpegOpusAudio") as ffmpeg:
            await cog._play_next(3)

    ffmpeg.assert_not_called()
    voice.play.assert_not_called()
    assert player.current is None
    report.record("stale generation aborts play", True, True, section=SECTION_COMMANDS)


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


async def test_auto_leave_when_alone(report, mock_bot):
    cog = Music(mock_bot)
    mock_bot.user = MagicMock()
    mock_bot.user.id = 999

    channel = MagicMock()
    channel.guild.id = 77
    bot_member = MagicMock()
    bot_member.bot = True
    channel.members = [bot_member]

    voice = AsyncMock()
    voice.channel = channel
    channel.guild.voice_client = voice

    human = MagicMock()
    human.id = 1
    human.bot = False
    before = MagicMock()
    before.channel = channel
    after = MagicMock()
    after.channel = None

    player = cog._player(77)
    player.current = Track("X", "https://youtu.be/x", 1, 1, "A")
    player.queue.append(Track("Y", "https://youtu.be/y", 1, 1, "B"))

    await cog.on_voice_state_update(human, before, after)

    voice.disconnect.assert_awaited()
    assert player.current is None
    assert len(player.queue) == 0
    report.record("auto-leave when alone", True, True, section=SECTION_COMMANDS)
