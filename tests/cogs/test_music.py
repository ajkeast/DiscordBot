"""Tests for Music cog, typed DinkPlayer, SoundCloud helpers, and voice idle leave."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import wavelink

from cogs.music import DinkPlayer, Music
from services.soundcloud import (
    expand_soundcloud_short_link,
    format_duration,
    full_stream_tracks,
    is_preview_track,
    is_soundcloud_url,
    to_search_query,
)


def _track(title: str, uri: str, length_ms: int = 180_000) -> MagicMock:
    t = MagicMock(spec=wavelink.Playable)
    t.title = title
    t.uri = uri
    t.identifier = uri
    t.length = length_ms
    return t


def _fake_player() -> MagicMock:
    player = MagicMock(spec=DinkPlayer)
    player.playing = False
    player.paused = False
    player.connected = True
    player.current = None
    player.channel = MagicMock(members=[])
    player.guild = MagicMock(id=42)
    player.queue = wavelink.Queue()
    player.fallbacks = []
    player.pending_ctx = None
    player.announce_fallback = False
    player.status_message = None
    player.text_channel = None
    player.play = AsyncMock()
    player.skip = AsyncMock()
    player.stop = AsyncMock()
    player.pause = AsyncMock()
    player.disconnect = AsyncMock()
    player.discard_fallbacks.side_effect = lambda: (
        setattr(player, "fallbacks", []),
        setattr(player, "announce_fallback", False),
    )

    def _arm(candidates, ctx):
        player.fallbacks = candidates[1:]
        player.pending_ctx = ctx
        player.announce_fallback = False
        player.status_message = None
        return candidates[0]

    player.arm_candidates.side_effect = _arm
    return player


def test_soundcloud_url_and_preview_helpers():
    assert is_soundcloud_url("https://soundcloud.com/artist/track")
    assert is_soundcloud_url("m.soundcloud.com/artist/track")
    assert not is_soundcloud_url("lofi hip hop")
    assert to_search_query("m.soundcloud.com/artist/track") == "https://soundcloud.com/artist/track"
    assert format_duration(125) == "2:05"
    assert format_duration(3661) == "1:01:01"

    preview = _track("Go+ Clip", "https://api-v2.soundcloud.com/preview/123")
    full = _track("Full Track", "https://soundcloud.com/artist/full")
    assert is_preview_track(preview)
    assert full_stream_tracks([preview, full]) == [full]


async def test_expand_soundcloud_short_link_async():
    response = MagicMock()
    response.url = "https://soundcloud.com/artist/expanded-track"
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get.return_value = cm

    expanded = await expand_soundcloud_short_link("https://on.soundcloud.com/abc", session)
    assert expanded == "https://soundcloud.com/artist/expanded-track"
    session.get.assert_called_once()


async def test_play_skips_previews_and_falls_back_on_exception(mock_bot, mock_ctx):
    cog = Music(mock_bot)
    player = _fake_player()
    mock_ctx.voice_client = player
    mock_ctx.author.voice = MagicMock(channel=MagicMock())
    mock_ctx.guild.me = None
    player.channel = mock_ctx.author.voice.channel

    preview = _track("Preview", "https://soundcloud.com/preview/1")
    first_full = _track("Broken Full", "https://soundcloud.com/artist/broken")
    second_full = _track("Working Full", "https://soundcloud.com/artist/working")

    with patch(
        "cogs.music.wavelink.Playable.search",
        new_callable=AsyncMock,
        return_value=[preview, first_full, second_full],
    ):
        await cog.play.callback(cog, mock_ctx, query="synthwave")

    player.play.assert_awaited_once_with(first_full)
    assert player.fallbacks == [second_full]

    # Simulate track exception -> falls back to second_full quietly
    exc_payload = SimpleNamespace(
        player=player, track=first_full, exception={"message": "404"}
    )
    await cog.on_wavelink_track_exception(exc_payload)
    assert player.play.await_count == 2
    assert player.play.call_args.args[0] is second_full
    assert player.announce_fallback is True

    # Track start announces now playing once
    start_payload = SimpleNamespace(player=player, track=second_full)
    await cog.on_wavelink_track_start(start_payload)
    assert "Working Full" in mock_ctx.send.call_args.args[0]


async def test_skip_discards_fallbacks_and_advances_queue(mock_bot, mock_ctx):
    cog = Music(mock_bot)
    player = _fake_player()
    player.playing = True
    queued = _track("Next Song", "https://soundcloud.com/artist/next")
    player.queue.put(queued)
    player.fallbacks = [_track("Old Fallback", "https://soundcloud.com/artist/old")]
    mock_ctx.voice_client = player

    await cog.skip.callback(cog, mock_ctx)

    player.discard_fallbacks.assert_called_once()
    player.skip.assert_awaited_once_with(force=True)
    player.play.assert_awaited_once_with(queued)
    mock_ctx.send.assert_awaited_once_with("Skipped.")


async def test_inactive_player_and_leave_disconnect(mock_bot, mock_ctx):
    cog = Music(mock_bot)
    player = _fake_player()
    player.playing = False
    mock_ctx.voice_client = player

    await cog.on_wavelink_inactive_player(player)
    player.disconnect.assert_awaited_once()

    player.disconnect.reset_mock()
    await cog.leave.callback(cog, mock_ctx)
    player.disconnect.assert_awaited_once()


async def test_pause_resume_stop_queue_np(mock_bot, mock_ctx):
    cog = Music(mock_bot)
    player = _fake_player()
    track = _track("Track 1", "https://soundcloud.com/artist/t1")
    player.playing = True
    player.current = track
    mock_ctx.voice_client = player

    await cog.now_playing.callback(cog, mock_ctx)
    assert "Track 1" in mock_ctx.send.call_args.args[0]

    await cog.queue.callback(cog, mock_ctx)
    assert "Track 1" in mock_ctx.send.call_args.args[0]

    await cog.pause.callback(cog, mock_ctx)
    player.pause.assert_awaited_with(True)

    player.paused = True
    await cog.resume.callback(cog, mock_ctx)
    player.pause.assert_awaited_with(False)

    await cog.stop.callback(cog, mock_ctx)
    player.stop.assert_awaited_once()


@pytest.mark.parametrize("human_present,expect_task", [(False, True), (True, False)])
async def test_voice_state_update_arms_or_cancels_alone_timer(
    mock_bot, human_present, expect_task
):
    cog = Music(mock_bot)
    player = _fake_player()
    bot_member = MagicMock(bot=True)
    human_member = MagicMock(bot=False)
    player.channel.members = [bot_member, human_member] if human_present else [bot_member]

    guild = MagicMock(id=99, voice_client=player)
    member = MagicMock(guild=guild)

    await cog.on_voice_state_update(member, MagicMock(), MagicMock())
    assert (99 in cog._alone_tasks) is expect_task
    cog._cancel_alone(99)
