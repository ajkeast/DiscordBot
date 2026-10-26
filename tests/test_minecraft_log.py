"""Unit tests for the Minecraft log parser, using the sample log fixtures."""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from utils.minecraft_log import (
    AFK_SESSION_SECONDS,
    EASTERN,
    ParsedLogs,
    Session,
    apply_world_files,
    format_duration,
    parse_log_directory,
    play_time_looks_new,
    strip_codes,
    summarize_day,
    summarize_window,
)

FIXTURES = Path(__file__).parent / "fixtures" / "minecraft"
WINDOW_START = datetime(2026, 10, 25, 15, 0, tzinfo=EASTERN)
WINDOW_END = datetime(2026, 10, 26, 15, 0, tzinfo=EASTERN)

# Reference recap for 2026-10-25 15:00 → 2026-10-26 15:00 (Eastern).
# Durations are exact seconds from the sample logs; the prose recap rounded them.
REFERENCE_PLAYTIME = {
    "Conkeastador": 64393,  # ~17h 50m, one long AFK session through 15:00
    "EpicPigz": 34036,  # ~9h 25m
    "LeNelli": 19980,  # ~5h 35m
    "Goated": 7924,  # ~2h 10m
    "jphelps": 5051,  # ~1h 25m
}
REFERENCE_SESSIONS = {
    "Conkeastador": 3,
    "EpicPigz": 11,
    "NiftyNarwhalz": 13,
    "LeNelli": 2,
    "Goated": 1,
    "jphelps": 1,
}


def _load(as_of=WINDOW_END):
    return parse_log_directory(FIXTURES, as_of=as_of)


def _window_stats(as_of=WINDOW_END):
    return summarize_window(_load(as_of), WINDOW_START, WINDOW_END)


def _player(stats, name):
    return next(player for player in stats["players"] if player["name"] == name)


def _write(directory: Path, name: str, lines: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_strip_ansi_and_section_codes():
    raw = "\x1b[93mEpicPigz joined the game\x1b[0m"
    assert strip_codes(raw) == "EpicPigz joined the game"
    assert strip_codes("§aSteve§r fell") == "Steve fell"


def test_reference_window_players_playtime_and_sessions():
    stats = _window_stats()
    names = {player["name"] for player in stats["players"]}
    assert names == set(REFERENCE_SESSIONS)
    for name, seconds in REFERENCE_PLAYTIME.items():
        assert _player(stats, name)["playtime_seconds"] == seconds
        assert _player(stats, name)["sessions"] == REFERENCE_SESSIONS[name]
    nifty = _player(stats, "NiftyNarwhalz")
    assert nifty["sessions"] == 13
    # ~8h 40m, give or take a few minutes of rounding in the original note.
    assert abs(nifty["playtime_seconds"] - (8 * 3600 + 40 * 60)) <= 5 * 60
    assert stats["totals"]["deaths"] == 19
    assert stats["totals"]["advancements"] == 28
    assert stats["nobody_played"] is False


def test_reference_window_flags_long_session_as_afk():
    stats = _window_stats()
    conke = _player(stats, "Conkeastador")
    assert conke["likely_afk"] is True
    assert conke["mostly_afk"] is True
    assert conke["longest_session_seconds"] > AFK_SESSION_SECONDS
    assert _player(stats, "EpicPigz")["likely_afk"] is False
    assert _player(stats, "LeNelli")["likely_afk"] is False


def test_reference_window_deaths_ignore_villagers_scanners_and_rcon():
    stats = _window_stats()
    messages = [death["message"] for death in stats["deaths"]]
    assert "was slain by Piglin" in messages
    assert "tried to swim in lava to escape Magma Cube" in messages
    assert "discovered the floor was lava" in messages
    assert "was blown up by Creeper" in messages
    assert "fell from a high place" in messages
    assert "was doomed to fall by Ghast" in messages
    assert all(not message.startswith("Villager") for message in messages)
    assert all(death["player"] in REFERENCE_SESSIONS for death in stats["deaths"])
    assert len(stats["server_notes"]) == 1
    note = stats["server_notes"][0]
    assert note["entity"] == "Villager"
    assert note["message"] == "was squished too much"
    assert note["count"] >= 30


def test_reference_window_advancement_kinds():
    stats = _window_stats()
    titles = {(item["player"], item["kind"], item["title"]) for item in stats["advancements"]}
    assert ("LeNelli", "advancement", "Stone Age") in titles
    assert ("NiftyNarwhalz", "challenge", "Hot Tourist Destinations") in titles
    assert ("Goated", "challenge", "Hot Tourist Destinations") in titles
    assert ("Conkeastador", "advancement", "Voluntary Exile") in titles
    assert ("EpicPigz", "advancement", "A Terrible Fortress") in titles


def test_calendar_day_clips_sessions_at_midnight():
    parsed = _load()
    oct_25 = summarize_day(parsed, date(2026, 10, 25), as_of=WINDOW_END)
    oct_26 = summarize_day(parsed, date(2026, 10, 26), as_of=WINDOW_END)

    # 23:20:13 → midnight, then midnight → 00:44:24.
    assert _player(oct_25, "jphelps")["playtime_seconds"] == 2387
    assert _player(oct_26, "jphelps")["playtime_seconds"] == 2664
    assert (
        _player(oct_25, "jphelps")["playtime_seconds"]
        + _player(oct_26, "jphelps")["playtime_seconds"]
        == 5051
    )
    # Still online at 15:00, so the 26th slice is the full 15 hours after midnight.
    assert _player(oct_26, "Conkeastador")["playtime_seconds"] == 15 * 3600
    assert _player(oct_26, "Conkeastador")["likely_afk"] is True

    assert oct_25["totals"]["deaths"] == 18
    assert oct_26["totals"]["deaths"] == 1
    assert oct_25["totals"]["advancements"] == 24
    assert oct_26["totals"]["advancements"] == 4
    assert oct_25["server_notes"] == []
    assert oct_25["partial_day"] is False
    assert oct_26["partial_day"] is True


def test_latest_log_date_comes_from_rollover_not_the_filename():
    """latest.log has no date in the name; 00:12 after a 23:37 roll is the next day."""
    parsed = _load()
    fell = [death for death in parsed.deaths if death.message == "fell from a high place"]
    assert any(death.at.astimezone(EASTERN).date() == date(2026, 10, 26) for death in fell)
    assert any(death.player == "EpicPigz" and death.at.astimezone(EASTERN).hour == 0 for death in fell)


def test_uuids_recorded_from_authenticator_lines():
    parsed = _load()
    assert parsed.uuids["EpicPigz"] == "2c053059-2f7d-4831-a4c6-c9838af825c7"
    assert parsed.uuids["LeNelli"] == "22df0076-bf42-4578-a777-995f5e0b51ed"


def test_prelogin_disconnects_are_not_players(tmp_path):
    _write(
        tmp_path,
        "2026-10-25-1.log",
        [
            "[01:42:17] [Server thread/INFO]: FishNet (/1.2.3.4:61000) lost connection: Disconnected",
            "[12:23:36] [Server thread/INFO]: JollyMango4018 (/1.2.3.4:41893) lost connection: Disconnected",
        ],
    )
    stats = summarize_day(parse_log_directory(tmp_path), date(2026, 10, 25))
    assert stats["nobody_played"] is True
    assert stats["totals"]["deaths"] == 0
    assert stats["players"] == []


def test_file_crossing_midnight_when_the_clock_goes_backwards(tmp_path):
    _write(
        tmp_path,
        "2026-10-25-1.log",
        [
            "[23:00:00] [Server thread/INFO]: System chat: Steve joined the game",
            "[01:00:00] [Server thread/INFO]: System chat: Steve left the game",
        ],
    )
    parsed = parse_log_directory(tmp_path)
    oct_25 = summarize_day(parsed, date(2026, 10, 25))
    oct_26 = summarize_day(parsed, date(2026, 10, 26))
    assert _player(oct_25, "Steve")["playtime_seconds"] == 3600
    assert _player(oct_26, "Steve")["playtime_seconds"] == 3600
    assert _player(oct_25, "Steve")["sessions"] == 1
    assert _player(oct_26, "Steve")["sessions"] == 1
    assert _player(oct_25, "Steve")["likely_afk"] is False


def test_small_clock_jitter_does_not_roll_the_day(tmp_path):
    _write(
        tmp_path,
        "2026-10-25-1.log",
        [
            "[10:00:00] [Server thread/INFO]: System chat: Steve joined the game",
            "[10:00:02] [Async Chat Thread - #1/INFO]: <Steve> hi",
            "[10:00:01] [Async Chat Thread - #1/INFO]: <Steve> hi again",
            "[12:00:00] [Server thread/INFO]: System chat: Steve left the game",
        ],
    )
    stats = summarize_day(parse_log_directory(tmp_path), date(2026, 10, 25))
    assert _player(stats, "Steve")["playtime_seconds"] == 2 * 3600


def test_server_stop_and_restart_end_open_sessions(tmp_path):
    _write(
        tmp_path,
        "2026-10-25-1.log",
        [
            "[10:00:00] [Server thread/INFO]: System chat: Steve joined the game",
            "[10:30:00] [Async Chat Thread - #1/INFO]: <Steve> brb",
            "[11:05:00] [Server thread/INFO]: Starting minecraft server version 1.21.8",
            '[11:05:02] [Server thread/INFO]: Done (2.0s)! For help, type "help"',
            "[11:06:00] [Server thread/INFO]: System chat: Steve joined the game",
            "[11:30:00] [Server thread/INFO]: System chat: Steve left the game",
        ],
    )
    parsed = parse_log_directory(tmp_path)
    stats = summarize_day(parsed, date(2026, 10, 25))
    assert _player(stats, "Steve")["playtime_seconds"] == 30 * 60 + 24 * 60
    assert _player(stats, "Steve")["sessions"] == 2
    reasons = [session.end_reason for session in parsed.sessions]
    assert reasons == ["server_stop", "leave"]


def test_explicit_stop_ends_the_session_at_the_stop_line(tmp_path):
    _write(
        tmp_path,
        "2026-10-25-1.log",
        [
            "[10:00:00] [Server thread/INFO]: System chat: Steve joined the game",
            "[11:00:00] [Server thread/INFO]: Stopping server",
            "[11:05:00] [Server thread/INFO]: Starting minecraft server version 1.21.8",
            "[11:06:00] [Server thread/INFO]: System chat: Steve joined the game",
            "[11:10:00] [Server thread/INFO]: System chat: Steve left the game",
        ],
    )
    stats = summarize_day(parse_log_directory(tmp_path), date(2026, 10, 25))
    assert _player(stats, "Steve")["playtime_seconds"] == 3600 + 4 * 60
    assert _player(stats, "Steve")["sessions"] == 2


def test_rejoin_without_a_leave_splits_the_session(tmp_path):
    _write(
        tmp_path,
        "2026-10-25-1.log",
        [
            "[10:00:00] [Server thread/INFO]: System chat: Steve joined the game",
            "[12:00:00] [Server thread/INFO]: System chat: Steve joined the game",
            "[12:30:00] [Server thread/INFO]: System chat: Steve left the game",
        ],
    )
    parsed = parse_log_directory(tmp_path)
    stats = summarize_day(parsed, date(2026, 10, 25))
    assert [session.end_reason for session in parsed.sessions] == ["rejoin", "leave"]
    assert _player(stats, "Steve")["playtime_seconds"] == 2 * 3600 + 30 * 60
    assert _player(stats, "Steve")["sessions"] == 2


def test_afk_threshold_is_strictly_over_six_hours(tmp_path):
    _write(
        tmp_path,
        "2026-10-25-1.log",
        [
            "[00:00:00] [Server thread/INFO]: System chat: Exact joined the game",
            "[06:00:00] [Server thread/INFO]: System chat: Exact left the game",
            "[08:00:00] [Server thread/INFO]: System chat: Over joined the game",
            "[14:00:01] [Server thread/INFO]: System chat: Over left the game",
        ],
    )
    stats = summarize_day(parse_log_directory(tmp_path), date(2026, 10, 25))
    assert _player(stats, "Exact")["likely_afk"] is False
    assert _player(stats, "Exact")["playtime_seconds"] == AFK_SESSION_SECONDS
    assert _player(stats, "Over")["likely_afk"] is True


def test_goal_advancement_and_ansi_join(tmp_path):
    _write(
        tmp_path,
        "2026-10-25-1.log",
        [
            "[12:00:00] [Server thread/INFO]: System chat: \x1b[93mSteve joined the game\x1b[0m",
            "[12:10:00] [Server thread/INFO]: System chat: Steve has reached the goal \x1b[92m[Adventure Time]\x1b[0m",
            "[12:30:00] [Server thread/INFO]: System chat: \x1b[93mSteve left the game\x1b[0m",
        ],
    )
    stats = summarize_day(parse_log_directory(tmp_path), date(2026, 10, 25))
    assert _player(stats, "Steve")["playtime_seconds"] == 30 * 60
    assert stats["advancements"][0]["kind"] == "goal"
    assert stats["advancements"][0]["title"] == "Adventure Time"


def test_villager_deaths_need_a_repeat_before_they_are_a_note(tmp_path):
    squish = (
        "[12:00:{sec:02d}] [Server thread/INFO]: Villager Villager['Villager'/1, uuid='abc', "
        "l='ServerLevel[world]', x=1, y=2, z=3] died, message: 'Villager was squished too much'"
    )
    lines = [squish.format(sec=second) for second in range(4)]
    _write(tmp_path, "2026-10-25-1.log", lines)
    quiet = summarize_day(parse_log_directory(tmp_path), date(2026, 10, 25))
    assert quiet["server_notes"] == []
    assert quiet["totals"]["deaths"] == 0

    lines.append(squish.format(sec=10))
    _write(tmp_path, "2026-10-25-1.log", lines)
    noisy = summarize_day(parse_log_directory(tmp_path), date(2026, 10, 25))
    assert noisy["server_notes"] == [
        {"entity": "Villager", "message": "was squished too much", "count": 5}
    ]


def test_rcon_lines_are_not_deaths(tmp_path):
    _write(
        tmp_path,
        "2026-10-25-1.log",
        [
            "[10:00:00] [Server thread/INFO]: System chat: \x1b[93mSteve joined the game\x1b[0m",
            "[10:05:00] [Server thread/INFO]: System chat: \x1b[3m\x1b[37m[Rcon: Saved the game]\x1b[0m",
            "[10:06:00] [Server thread/INFO]: System chat: Steve was slain by Zombie",
            "[10:10:00] [Server thread/INFO]: System chat: \x1b[93mSteve left the game\x1b[0m",
        ],
    )
    stats = summarize_day(parse_log_directory(tmp_path), date(2026, 10, 25))
    assert stats["totals"]["deaths"] == 1
    assert stats["deaths"][0]["message"] == "was slain by Zombie"


def test_gzip_rolled_log_matches_plain_text(tmp_path):
    lines = [
        "[10:00:00] [Server thread/INFO]: System chat: Steve joined the game",
        "[11:00:00] [Server thread/INFO]: System chat: Steve left the game",
    ]
    plain = tmp_path / "plain"
    packed = tmp_path / "packed"
    _write(plain, "2026-10-25-1.log", lines)
    packed.mkdir()
    with gzip.open(packed / "2026-10-25-1.log.gz", "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    plain_stats = summarize_day(parse_log_directory(plain), date(2026, 10, 25))
    gzip_stats = summarize_day(parse_log_directory(packed), date(2026, 10, 25))
    assert gzip_stats["totals"] == plain_stats["totals"]
    assert _player(gzip_stats, "Steve")["playtime_seconds"] == 3600


def test_open_session_extends_only_within_the_live_grace(tmp_path):
    _write(
        tmp_path,
        "latest.log",
        [
            "[10:00:00] [Server thread/INFO]: System chat: Steve joined the game",
        ],
    )
    as_of = datetime(2026, 10, 26, 10, 10, tzinfo=EASTERN)
    parsed = parse_log_directory(tmp_path, as_of=as_of)
    stats = summarize_day(parsed, date(2026, 10, 26), as_of=as_of)
    assert _player(stats, "Steve")["playtime_seconds"] == 10 * 60
    assert parsed.sessions[0].end_reason == "still_online"

    stale = datetime(2026, 10, 26, 18, 0, tzinfo=EASTERN)
    stale_parsed = parse_log_directory(tmp_path, as_of=stale)
    stale_stats = summarize_day(stale_parsed, date(2026, 10, 26), as_of=stale)
    # Last line is the join itself, eight hours before as_of. That gap is not playtime.
    assert stale_stats["totals"]["playtime_seconds"] == 0


def test_missing_logs_directory_raises(tmp_path):
    import pytest

    from utils.minecraft_log import MinecraftLogError

    with pytest.raises(MinecraftLogError):
        parse_log_directory(tmp_path / "nope")


def test_format_duration_rounds_minutes():
    assert format_duration(30) == "30s"
    assert format_duration(90) == "2m"
    assert format_duration(17 * 3600 + 53 * 60 + 13) == "17h 53m"
    assert format_duration(3600) == "1h"


def test_reference_players_carry_death_lines_and_titles():
    stats = _window_stats()
    conkeastador = _player(stats, "Conkeastador")
    assert any("lava" in line for line in conkeastador["death_lines"])
    assert "This Boat Has Legs" in conkeastador["advancement_titles"]
    jphelps = _player(stats, "jphelps")
    assert jphelps["death_lines"] == []
    assert jphelps["advancement_titles"] == []
    assert jphelps["no_advancements"] is True
    assert jphelps["new_player"] is False
    assert jphelps["missed_advancements"] == []
    assert stats["new_players"] == []


def test_play_time_matches_only_a_short_history():
    assert play_time_looks_new(3600, 3600) is True
    assert play_time_looks_new(3600 + 30 * 60, 3600) is True
    assert play_time_looks_new(3600 + 30 * 60 + 1, 3600) is False
    assert play_time_looks_new(3600 - 10 * 60, 3600) is True
    assert play_time_looks_new(3600 - 10 * 60 - 1, 3600) is False
    assert play_time_looks_new(0, 0) is False
    assert play_time_looks_new(100 * 3600, 3600) is False


def _world_stats():
    start = datetime(2026, 10, 25, 10, 0, tzinfo=EASTERN)
    newbie_id = "11111111-1111-1111-1111-111111111111"
    veteran_id = "22222222-2222-2222-2222-222222222222"
    parsed = ParsedLogs(
        sessions=[
            Session("Newbie", start, start + timedelta(hours=1), "leave"),
            Session("Veteran", start, start + timedelta(hours=1), "leave"),
        ],
        uuids={"Newbie": newbie_id, "Veteran": veteran_id},
    )
    stats = summarize_window(
        parsed,
        datetime(2026, 10, 25, tzinfo=EASTERN),
        datetime(2026, 10, 26, tzinfo=EASTERN),
    )
    stats["advancements"].append(
        {
            "player": "Alex",
            "uuid": None,
            "at": start.isoformat(),
            "time": "10:00:00",
            "kind": "advancement",
            "title": "Stone Age",
        }
    )
    stats["advancements"].append(
        {
            "player": "Alex",
            "uuid": None,
            "at": start.isoformat(),
            "time": "11:00:00",
            "kind": "advancement",
            "title": "Suit Up",
        }
    )
    return stats, newbie_id, veteran_id, parsed


def test_world_files_welcome_a_new_player_and_name_a_real_miss(tmp_path):
    stats, newbie_id, veteran_id, parsed = _world_stats()
    stats_dir = tmp_path / "world" / "players" / "stats"
    adv_dir = tmp_path / "world" / "players" / "advancements"
    stats_dir.mkdir(parents=True)
    adv_dir.mkdir(parents=True)
    (stats_dir / f"{newbie_id}.json").write_text(
        json.dumps(
            {
                "stats": {"minecraft:custom": {"minecraft:play_time": 3600 * 20}},
                "DataVersion": 1,
            }
        ),
        encoding="utf-8",
    )
    (stats_dir / f"{veteran_id}.json").write_text(
        json.dumps(
            {
                "stats": {"minecraft:custom": {"minecraft:play_time": 100 * 3600 * 20}},
                "DataVersion": 1,
            }
        ),
        encoding="utf-8",
    )
    (adv_dir / f"{newbie_id}.json").write_text(
        json.dumps(
            {
                "minecraft:story/mine_stone": {"criteria": {}, "done": False},
                "DataVersion": 1,
            }
        ),
        encoding="utf-8",
    )
    (adv_dir / f"{veteran_id}.json").write_text(
        json.dumps(
            {
                "minecraft:story/mine_stone": {"criteria": {"stone": "x"}, "done": True},
                "DataVersion": 1,
            }
        ),
        encoding="utf-8",
    )

    apply_world_files(stats, parsed, tmp_path)
    newbie = _player(stats, "Newbie")
    veteran = _player(stats, "Veteran")
    assert newbie["new_player"] is True
    assert newbie["missed_advancements"] == ["Stone Age", "Suit Up"]
    assert veteran["new_player"] is False
    assert veteran["missed_advancements"] == ["Suit Up"]
    assert stats["new_players"] == ["Newbie"]


def test_world_stats_fallback_without_an_advancement_file_does_not_invent_a_miss(tmp_path):
    stats, newbie_id, _veteran_id, parsed = _world_stats()
    stats_dir = tmp_path / "world" / "stats"
    stats_dir.mkdir(parents=True)
    (stats_dir / f"{newbie_id}.json").write_text(
        json.dumps({"stats": {"minecraft:custom": {"minecraft:play_time": 3600 * 20}}}),
        encoding="utf-8",
    )
    apply_world_files(stats, parsed, tmp_path)
    newbie = _player(stats, "Newbie")
    assert newbie["new_player"] is True
    assert newbie["missed_advancements"] == []
