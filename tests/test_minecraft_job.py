"""Minecraft summary narrative, ledger, and admin preview."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.minecraft import DAILY_AT, Minecraft, actor_is_admin_result
from utils.minecraft_job import (
    SCHEMA_STATEMENTS,
    SYSTEM_PROMPT,
    activity_date_due,
    claim_daily_post,
    grok_payload,
    minecraft_channel_id,
    minecraft_model,
    narrative_ok,
    prepare_post,
    render_narrative,
    render_template,
)
from utils.minecraft_log import EASTERN, parse_log_directory, summarize_window

FIXTURES = Path(__file__).parent / "fixtures" / "minecraft"
WINDOW_START = datetime(2026, 10, 25, 15, 0, tzinfo=EASTERN)
WINDOW_END = datetime(2026, 10, 26, 15, 0, tzinfo=EASTERN)


def _reference_stats():
    if not (FIXTURES / "2026-10-25-1.log").is_file() or not (FIXTURES / "latest.log").is_file():
        pytest.skip("Sample Minecraft logs are kept out of git")
    parsed = parse_log_directory(FIXTURES, as_of=WINDOW_END)
    return summarize_window(parsed, WINDOW_START, WINDOW_END)


def _steve_stats():
    return {
        "date": "2026-10-25",
        "pretty_date": "Sunday, October 25, 2026",
        "weekday": "Sunday",
        "timezone": "America/New_York",
        "window_start": "2026-10-25T00:00:00-04:00",
        "window_end": "2026-10-26T00:00:00-04:00",
        "partial_day": False,
        "nobody_played": False,
        "players": [
            {
                "name": "Steve",
                "uuid": None,
                "playtime_seconds": 3600,
                "playtime": "1h",
                "sessions": 1,
                "longest_session_seconds": 3600,
                "longest_session": "1h",
                "likely_afk": False,
                "mostly_afk": False,
                "deaths": 2,
                "advancements": 1,
            }
        ],
        "totals": {
            "players": 1,
            "playtime_seconds": 3600,
            "sessions": 1,
            "deaths": 2,
            "advancements": 1,
        },
        "deaths": [
            {
                "player": "Steve",
                "uuid": None,
                "at": "2026-10-25T12:00:00-04:00",
                "time": "12:00:00",
                "message": "fell from a high place",
            },
            {
                "player": "Steve",
                "uuid": None,
                "at": "2026-10-25T13:00:00-04:00",
                "time": "13:00:00",
                "message": "fell from a high place",
            },
        ],
        "death_counts": [
            {"player": "Steve", "message": "fell from a high place", "count": 2}
        ],
        "advancements": [
            {
                "player": "Steve",
                "uuid": None,
                "at": "2026-10-25T11:00:00-04:00",
                "time": "11:00:00",
                "kind": "advancement",
                "title": "Stone Age",
            }
        ],
        "server_notes": [],
        "slices": [
            {
                "player_name": "Steve",
                "player_uuid": None,
                "started_at": "2026-10-25T10:00:00-04:00",
                "ended_at": "2026-10-25T11:00:00-04:00",
                "slice_started_at": "2026-10-25T10:00:00-04:00",
                "slice_ended_at": "2026-10-25T11:00:00-04:00",
                "duration_seconds": 3600,
                "full_seconds": 3600,
                "end_reason": "leave",
                "likely_afk": False,
            }
        ],
    }


def test_activity_date_is_due_at_and_after_8am():
    before = datetime(2026, 10, 26, 7, 59, tzinfo=EASTERN)
    at_eight = datetime(2026, 10, 26, 8, 0, tzinfo=EASTERN)
    afternoon = datetime(2026, 10, 26, 15, 30, tzinfo=EASTERN)
    assert activity_date_due(before) is None
    assert activity_date_due(at_eight) == date(2026, 10, 25)
    assert activity_date_due(afternoon) == date(2026, 10, 25)
    assert DAILY_AT.hour == 8
    assert DAILY_AT.minute == 0


def test_channel_and_model_defaults(monkeypatch):
    monkeypatch.delenv("MINECRAFT_CHANNEL_ID", raising=False)
    monkeypatch.delenv("GROK_MINECRAFT_MODEL", raising=False)
    assert minecraft_channel_id() == 581136950074015761
    assert minecraft_model() == "grok-4.7"
    monkeypatch.setenv("MINECRAFT_CHANNEL_ID", "42")
    monkeypatch.setenv("GROK_MINECRAFT_MODEL", "grok-test")
    assert minecraft_channel_id() == 42
    assert minecraft_model() == "grok-test"
    monkeypatch.setenv("MINECRAFT_CHANNEL_ID", "nope")
    assert minecraft_channel_id() == 581136950074015761


def test_schema_is_additive_and_matches_the_sql_file():
    sql = "\n".join(SCHEMA_STATEMENTS).lower()
    file_sql = Path("scripts/minecraft_schema.sql").read_text(encoding="utf-8").lower()
    assert "drop table" not in sql
    assert "alter table" not in sql
    assert "drop table" not in file_sql
    for name in (
        "minecraft_sessions",
        "minecraft_deaths",
        "minecraft_advancements",
        "minecraft_daily_posts",
    ):
        assert f"create table if not exists {name}" in sql
        assert f"create table if not exists {name}" in file_sql


def test_template_for_reference_window_fits_discord_and_uses_the_facts():
    stats = _reference_stats()
    text = render_template(stats)
    assert len(text) <= 2000
    for name in (
        "Conkeastador",
        "EpicPigz",
        "NiftyNarwhalz",
        "LeNelli",
        "Goated",
        "jphelps",
    ):
        assert name in text
    assert "Time played" in text
    assert "Deaths (19)" in text
    assert "Advancements (28)" in text
    assert "likely AFK" in text
    assert "The Nether trip went badly" in text
    assert "8 to 11 PM" in text
    assert "three or four" in text
    assert "EpicPigz 6 times" in text
    assert "lava, Magma Cubes, and Ghasts" in text
    assert "45 hours" in text
    assert "Acquire Hardware" in text
    assert "Cover Me with Diamonds" in text
    assert "got 5 each, and Conkeastador got 3." in text
    assert "Hot Tourist Destinations" in text
    assert "A Terrible Fortress" in text
    assert "didn't earn" in text
    assert "squished" in text
    assert "FishNet" not in text
    assert text.count("\n\n") >= 3
    payload = grok_payload(stats)
    assert "slices" not in payload
    assert "death_lines" not in payload
    assert payload["highlights"]["deaths"]["total"] == 19
    assert payload["highlights"]["deaths"]["nether"] is True
    assert payload["highlights"]["advancements"]["total"] == 28


def test_nobody_played_is_a_one_liner():
    stats = {
        "pretty_date": "Sunday, October 25, 2026",
        "nobody_played": True,
        "players": [],
        "totals": {"players": 0, "sessions": 0, "deaths": 0, "advancements": 0},
        "death_counts": [],
        "advancements": [],
        "server_notes": [],
        "partial_day": False,
    }
    text, source = render_narrative(stats)
    assert text == "Nobody played Minecraft on Sunday, October 25, 2026."
    assert source == "template"
    assert len(text) < 200


def test_prompt_asks_for_sections_and_a_nether_call_only_when_supported():
    assert "Time played" in SYSTEM_PROMPT
    assert "busiest" in SYSTEM_PROMPT
    assert "Nether" in SYSTEM_PROMPT
    assert "deaths.nether" in SYSTEM_PROMPT


def test_narrative_requires_a_nudge_and_a_welcome_when_the_json_says_so():
    stats = _steve_stats()
    stats["players"][0]["no_advancements"] = True
    stats["players"][0]["new_player"] = True
    stats["players"][0]["advancements"] = 0
    stats["advancements"] = []
    stats["totals"]["advancements"] = 0
    assert narrative_ok("Steve fell from a high place.", stats) is False
    assert narrative_ok(
        "Time played\nWelcome Steve. He fell from a high place. No advancement today.",
        stats,
    ) is True


def test_template_names_a_confirmed_miss_and_welcomes_a_new_player():
    stats = _steve_stats()
    stats["players"][0]["new_player"] = True
    stats["players"][0]["no_advancements"] = True
    stats["players"][0]["advancements"] = 0
    stats["players"][0]["advancement_titles"] = []
    stats["players"][0]["missed_advancements"] = ["Stone Age"]
    stats["players"][0]["deaths"] = 0
    stats["players"][0]["death_lines"] = []
    stats["deaths"] = []
    stats["death_counts"] = []
    stats["advancements"] = [
        {
            "player": "Alex",
            "uuid": None,
            "at": "2026-10-25T11:00:00-04:00",
            "time": "11:00:00",
            "kind": "advancement",
            "title": "Stone Age",
        }
    ]
    stats["totals"]["deaths"] = 0
    stats["totals"]["advancements"] = 1
    text = render_template(stats)
    assert "Welcome" in text
    assert "Hey, Steve, I saw you missed Stone Age." in text
    assert "Died" not in text


def test_narrative_requires_the_death_and_the_title():
    stats = _steve_stats()
    assert narrative_ok("Steve had a nice day.", stats) is False
    assert narrative_ok(
        "Time played\nSteve played 1h. He fell from a high place and earned Stone Age.",
        stats,
    ) is True
    assert narrative_ok(
        "Steve fell from a high place. 2 deaths, 1 advancement.",
        stats,
    ) is False


def test_grok_text_is_used_when_it_matches_the_json(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    stats = _steve_stats()
    client = MagicMock()
    chat = MagicMock()
    client.chat.create.return_value = chat
    chat.sample.return_value = MagicMock(
        content=(
            "```\n**Time played**\nSteve, 1h, 1 session.\n\n"
            "**Deaths (2)**\nHe fell from a high place.\n\n"
            "**Advancements (1)**\nStone Age.\n```"
        )
    )
    text, source = render_narrative(stats, client=client)
    assert source == "grok"
    assert "Stone Age" in text
    assert "fell from a high place" in text
    assert client.chat.create.call_args.kwargs["model"] == "grok-4.7"
    assert client.chat.create.call_args.kwargs["reasoning_effort"] == "low"


def test_grok_failure_falls_back_to_the_template(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    stats = _steve_stats()
    client = MagicMock()
    client.chat.create.side_effect = RuntimeError("boom")
    with patch("utils.minecraft_job.time.sleep"):
        text, source = render_narrative(stats, client=client)
    assert source == "template"
    assert "Steve" in text
    assert "2" in text
    assert "1" in text


def test_missing_api_key_uses_the_template(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    text, source = render_narrative(_steve_stats())
    assert source == "template"
    assert "Steve" in text


class _Cursor:
    def __init__(self, row, *, rowcount=1):
        self.row = row
        self.rowcount = rowcount
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))

    def executemany(self, query, params):
        self.calls.append((query, list(params)))

    def fetchone(self):
        return self.row


def test_claim_skips_a_day_that_was_already_posted():
    cursor = _Cursor(("posted",))

    @contextmanager
    def fake_cursor():
        yield cursor

    with patch("utils.minecraft_job.ensure_minecraft_tables"):
        with patch("utils.minecraft_job.db_ops.db.cursor", fake_cursor):
            result = claim_daily_post(date(2026, 10, 25), 581136950074015761)
    assert result == "already_posted"
    assert len(cursor.calls) == 1


def test_persist_replaces_one_day_and_casts_booleans():
    cursor = _Cursor(None)

    @contextmanager
    def fake_cursor():
        yield cursor

    with patch("utils.minecraft_job.ensure_minecraft_tables"):
        with patch("utils.minecraft_job.db_ops.db.execute"):
            with patch("utils.minecraft_job.db_ops.db.cursor", fake_cursor):
                from utils.minecraft_job import persist_activity

                persist_activity(_steve_stats())
    sql = "\n".join(call[0] for call in cursor.calls)
    assert "DELETE FROM minecraft_sessions" in sql
    assert "DELETE FROM minecraft_deaths" in sql
    assert "DELETE FROM minecraft_advancements" in sql
    assert "%s::boolean" in sql
    inserts = [call for call in cursor.calls if call[0].lstrip().startswith("INSERT")]
    assert len(inserts) == 3


def test_stale_pending_claim_is_not_stolen_once_posted():
    cursor = _Cursor(("pending",), rowcount=0)

    @contextmanager
    def fake_cursor():
        yield cursor

    with patch("utils.minecraft_job.ensure_minecraft_tables"):
        with patch("utils.minecraft_job.db_ops.db.cursor", fake_cursor):
            result = claim_daily_post(date(2026, 10, 25), 99)
    assert result == "already_posted"


def test_claim_inserts_a_pending_row_when_the_day_is_new():
    cursor = _Cursor(None)

    @contextmanager
    def fake_cursor():
        yield cursor

    with patch("utils.minecraft_job.ensure_minecraft_tables"):
        with patch("utils.minecraft_job.db_ops.db.cursor", fake_cursor):
            result = claim_daily_post(date(2026, 10, 25), 99)
    assert result == "claimed"
    insert = cursor.calls[-1][0]
    assert "INSERT INTO minecraft_daily_posts" in insert
    assert cursor.calls[-1][1] == (date(2026, 10, 25), 99)


def test_prepare_post_still_previews_when_persist_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-10-25-1.log").write_text(
        "\n".join(
            [
                "[10:00:00] [Server thread/INFO]: System chat: Steve joined the game",
                "[11:00:00] [Server thread/INFO]: System chat: Steve left the game",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with patch("utils.minecraft_job.persist_activity", side_effect=RuntimeError("db down")):
        prepared = prepare_post(
            date(2026, 10, 25),
            data_dir=tmp_path,
            as_of=datetime(2026, 10, 26, 8, 0, tzinfo=EASTERN),
            persist="best_effort",
        )
    assert prepared.persisted is False
    assert prepared.source == "template"
    assert "Steve" in prepared.narrative
    assert prepared.stats["totals"]["playtime_seconds"] == 3600

    with patch("utils.minecraft_job.persist_activity", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            prepare_post(
                date(2026, 10, 25),
                data_dir=tmp_path,
                as_of=datetime(2026, 10, 26, 8, 0, tzinfo=EASTERN),
                persist="required",
            )


def test_compose_mounts_the_minecraft_volume_read_only():
    text = Path("docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "minecraft_data:/minecraft:ro" in text
    assert "name: minecraft_data" in text
    assert "MINECRAFT_DATA_DIR: /minecraft" in text


def test_admin_check_allows_owner_or_administrator():
    user = MagicMock()
    user.guild_permissions.administrator = False
    assert actor_is_admin_result(True, user, guild=object()) is True
    assert actor_is_admin_result(False, user, guild=object()) is False
    user.guild_permissions.administrator = True
    assert actor_is_admin_result(False, user, guild=object()) is True
    assert actor_is_admin_result(False, user, guild=None) is False


async def test_preview_is_rejected_for_non_admins(mock_bot, mock_ctx):
    cog = Minecraft(mock_bot)
    mock_bot.is_owner = AsyncMock(return_value=False)
    await cog.minecraft_summary.callback(cog, mock_ctx, day="2026-10-25")
    sent = mock_ctx.send.call_args.args[0]
    assert "admins" in sent.lower()


def _finished_post():
    prepared = MagicMock()
    prepared.nobody_played = False
    prepared.narrative = "Steve fell from a high place and earned Stone Age."
    prepared.stats = {"pretty_date": "Sunday, October 25, 2026", "partial_day": False}
    prepared.source = "template"
    return prepared


async def test_post_flag_sends_the_recap_to_the_channel(mock_bot, mock_ctx):
    cog = Minecraft(mock_bot)
    mock_ctx.author.guild_permissions.administrator = True
    mock_bot.is_owner = AsyncMock(return_value=False)
    channel = AsyncMock()
    channel.send = AsyncMock(return_value=MagicMock(id=99))
    mock_bot.get_channel.return_value = channel
    with patch("cogs.minecraft.claim_daily_post", return_value="claimed"):
        with patch("cogs.minecraft.prepare_post", return_value=_finished_post()):
            with patch("cogs.minecraft.mark_posted") as mark:
                await cog.minecraft_summary.callback(
                    cog, mock_ctx, day="2026-10-25", post=True
                )
    channel.send.assert_awaited()
    mark.assert_called_once()
    assert "Posted" in mock_ctx.send.call_args.args[0]
    assert mock_ctx.channel.send.await_count == 0


async def test_post_flag_skips_a_day_already_in_the_channel(mock_bot, mock_ctx):
    cog = Minecraft(mock_bot)
    mock_ctx.author.guild_permissions.administrator = True
    mock_bot.is_owner = AsyncMock(return_value=False)
    with patch("cogs.minecraft.claim_daily_post", return_value="already_posted"):
        with patch("cogs.minecraft.prepare_post") as prepare:
            await cog.minecraft_summary.callback(
                cog, mock_ctx, day="2026-10-25", post=True
            )
    prepare.assert_not_called()
    assert "already" in mock_ctx.send.call_args.args[0].lower()


async def test_post_flag_refuses_a_day_still_in_progress(mock_bot, mock_ctx):
    cog = Minecraft(mock_bot)
    mock_ctx.author.guild_permissions.administrator = True
    mock_bot.is_owner = AsyncMock(return_value=False)
    frozen = datetime(2026, 10, 26, 15, 0, tzinfo=EASTERN)
    with patch("cogs.minecraft.datetime") as clock:
        clock.now.return_value = frozen
        with patch("cogs.minecraft.claim_daily_post") as claim:
            await cog.minecraft_summary.callback(
                cog, mock_ctx, day="2026-10-26", post=True
            )
    claim.assert_not_called()
    assert "in progress" in mock_ctx.send.call_args.args[0].lower()


async def test_post_flag_defaults_to_yesterday(mock_bot, mock_ctx):
    cog = Minecraft(mock_bot)
    mock_ctx.author.guild_permissions.administrator = True
    mock_bot.is_owner = AsyncMock(return_value=False)
    frozen = datetime(2026, 10, 26, 15, 0, tzinfo=EASTERN)
    with patch("cogs.minecraft.datetime") as clock:
        clock.now.return_value = frozen
        with patch("cogs.minecraft.claim_daily_post", return_value="already_posted") as claim:
            await cog.minecraft_summary.callback(cog, mock_ctx, post=True)
    claim.assert_called_once()
    assert claim.call_args.args[0] == date(2026, 10, 25)


async def test_preview_sends_an_embed_without_posting(mock_bot, mock_ctx):
    cog = Minecraft(mock_bot)
    mock_ctx.author.guild_permissions.administrator = True
    mock_bot.is_owner = AsyncMock(return_value=False)
    prepared = MagicMock()
    prepared.nobody_played = False
    prepared.narrative = "Steve played 1h. 0 deaths, 0 advancements."
    prepared.stats = {
        "pretty_date": "Sunday, October 25, 2026",
        "partial_day": False,
    }
    with patch("cogs.minecraft.prepare_post", return_value=prepared):
        await cog.minecraft_summary.callback(cog, mock_ctx, day="2026-10-25")
    mock_ctx.send.assert_awaited()
    embed = mock_ctx.send.call_args.kwargs["embed"]
    assert "October 25, 2026" in embed.title
    assert "Steve played" in embed.description
    assert mock_ctx.channel.send.await_count == 0


async def test_scheduled_post_does_not_run_twice(mock_bot):
    cog = Minecraft(mock_bot)
    day = date(2026, 10, 25)
    with patch("cogs.minecraft.claim_daily_post", return_value="already_posted"):
        with patch("cogs.minecraft.prepare_post") as prepare:
            await cog._post_due_date(day, reason="test")
    prepare.assert_not_called()


async def test_failed_send_releases_the_claim(mock_bot):
    cog = Minecraft(mock_bot)
    channel = AsyncMock()
    channel.send = AsyncMock(side_effect=RuntimeError("discord down"))
    mock_bot.get_channel.return_value = channel
    prepared = MagicMock()
    prepared.nobody_played = True
    prepared.narrative = "Nobody played Minecraft on Sunday, October 25, 2026."
    prepared.stats = {"pretty_date": "Sunday, October 25, 2026", "partial_day": False}
    prepared.source = "template"
    with patch("cogs.minecraft.claim_daily_post", return_value="claimed"):
        with patch("cogs.minecraft.prepare_post", return_value=prepared):
            with patch("cogs.minecraft.release_claim") as release:
                with patch("cogs.minecraft.mark_posted") as mark:
                    await cog._post_due_date(date(2026, 10, 25), reason="test")
    release.assert_called_once()
    mark.assert_not_called()
