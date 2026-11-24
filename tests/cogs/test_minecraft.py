"""Tests for the Minecraft cog: scheduled posting, admin preview, screenshots."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.minecraft import Minecraft
from minecraft.day import day_window, pretty_date
from minecraft.models import DayRecap, PlayerSummary, PreparedPost


def _prepared(day: date, *, nobody_played: bool = False) -> PreparedPost:
    ws, we = day_window(day)
    players = (
        []
        if nobody_played
        else [
            PlayerSummary(
                name="Steve",
                uuid=None,
                playtime_seconds=3600,
                playtime="1h",
                sessions=1,
                longest_session_seconds=3600,
                longest_session="1h",
                still_online=False,
                new_player=False,
                death_count=0,
            )
        ]
    )
    recap = DayRecap(
        day=day,
        pretty_date=pretty_date(day),
        window_start=ws,
        window_end=we,
        partial_day=False,
        players=players,
        slices=[],
        deaths=[],
        player_uuids={},
    )
    narrative = (
        f"Nobody played Minecraft on {recap.pretty_date}."
        if nobody_played
        else "Steve had a quiet hour of mining."
    )
    return PreparedPost(
        recap=recap,
        narrative=narrative,
        source="grok" if not nobody_played else "template",
        persisted=False,
    )


@pytest.fixture
def mc_cog(mock_bot):
    repo = MagicMock()
    return Minecraft(mock_bot, repo=repo)


async def test_minecraft_summary_rejects_non_admin(mc_cog, mock_ctx):
    await mc_cog.minecraft_summary.callback(mc_cog, mock_ctx)
    assert "Only server admins" in mock_ctx.send.call_args.args[0]


@patch("cogs.minecraft.pick_recap_screenshot", return_value=None)
@patch("cogs.minecraft.prepare_post")
async def test_minecraft_preview_is_strictly_read_only(
    mock_prepare, _mock_shot, mc_cog, mock_ctx, mock_admin_author
):
    mock_ctx.author = mock_admin_author
    mock_prepare.return_value = _prepared(date(2026, 10, 25))

    await mc_cog.minecraft_summary.callback(
        mc_cog, mock_ctx, day="2026-10-25", post=False
    )

    assert mock_prepare.call_args.kwargs["persist"] is False
    embed = mock_ctx.send.call_args.kwargs["embed"]
    assert embed.title == "Sunday, October 25, 2026"
    assert "Steve" in embed.description


@patch("cogs.minecraft.pick_recap_screenshot")
@patch("cogs.minecraft.prepare_post")
async def test_post_due_date_claims_persists_attaches_screenshot_and_marks_posted(
    mock_prepare, mock_shot, tmp_path, mc_cog
):
    shot = tmp_path / "2026-10-25T20-00-00.png"
    shot.write_bytes(b"png-bytes")
    mock_shot.return_value = shot

    day = date(2026, 10, 25)
    mc_cog.repo.claim_daily_post.return_value = "claimed"
    mock_prepare.return_value = _prepared(day)
    channel = AsyncMock()
    channel.send.return_value = MagicMock(id=55555)
    mc_cog.bot.get_channel.return_value = channel

    status = await mc_cog._post_due_date(day, reason="scheduled")

    assert status == "posted"
    assert mock_prepare.call_args.kwargs["persist"] is True
    channel.send.assert_awaited_once()
    assert channel.send.call_args.kwargs["file"].filename == shot.name
    assert channel.send.call_args.kwargs["embed"].image.url == f"attachment://{shot.name}"
    mc_cog.repo.mark_posted.assert_called_once()


async def test_post_due_date_skips_when_already_posted(mc_cog):
    mc_cog.repo.claim_daily_post.return_value = "already_posted"
    status = await mc_cog._post_due_date(date(2026, 10, 25), reason="scheduled")
    assert status == "already_posted"
    mc_cog.repo.mark_posted.assert_not_called()


@patch("cogs.minecraft.prepare_post", side_effect=RuntimeError("boom"))
async def test_post_due_date_releases_claim_on_failure(_mock_prep, mc_cog):
    day = date(2026, 10, 25)
    mc_cog.repo.claim_daily_post.return_value = "claimed"
    status = await mc_cog._post_due_date(day, reason="scheduled")
    assert status == "failed"
    mc_cog.repo.release_claim.assert_called_once_with(day)


@patch("cogs.minecraft.pick_recap_screenshot")
async def test_deliver_nobody_played_with_and_without_screenshot(
    mock_shot, tmp_path, mc_cog
):
    day = date(2026, 10, 25)
    send = AsyncMock()
    mock_shot.return_value = None
    await mc_cog._deliver(send, _prepared(day, nobody_played=True))
    assert "Nobody played Minecraft" in send.call_args.args[0]

    send.reset_mock()
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"png")
    mock_shot.return_value = shot
    await mc_cog._deliver(send, _prepared(day, nobody_played=True))
    assert send.call_args.kwargs["embed"].title == "Sunday, October 25, 2026"
    assert send.call_args.kwargs["file"].filename == "shot.png"
