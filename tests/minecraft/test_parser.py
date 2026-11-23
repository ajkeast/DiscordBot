"""Tests for the date-windowed Minecraft log parser and camera-session filter."""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from config import EASTERN
from minecraft.models import MinecraftLogError, ParsedLogs, Session
from minecraft.parser import (
    camera_overlap_seconds,
    day_window,
    exclude_camera_sessions,
    load_camera_sessions,
    parse_data_dir,
    parse_log_directory,
    playday_date,
    strip_codes,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "minecraft"


def test_strip_codes_removes_ansi_and_section_formatting():
    assert strip_codes("\x1b[93mSteve joined the game\x1b[0m") == "Steve joined the game"
    assert strip_codes("§aAlex §lwon") == "Alex won"


def test_playday_date_and_window_boundary():
    before = datetime(2026, 11, 18, 4, 59, tzinfo=EASTERN)
    at_open = datetime(2026, 11, 18, 5, 0, tzinfo=EASTERN)
    assert playday_date(before) == date(2026, 11, 17)
    assert playday_date(at_open) == date(2026, 11, 18)

    ws, we = day_window(date(2026, 10, 25))
    assert ws == datetime(2026, 10, 25, 5, 0, tzinfo=EASTERN)
    assert we == datetime(2026, 10, 26, 5, 0, tzinfo=EASTERN)


def test_parse_real_fixtures_extracts_sessions_deaths_and_entities():
    parsed = parse_log_directory(FIXTURES_DIR)
    assert parsed.files_found == 2
    assert parsed.sessions
    assert parsed.deaths
    assert parsed.entity_deaths
    assert "NiftyNarwhalz" in parsed.uuids
    assert any(d.player == "EpicPigz" and "high place" in d.message for d in parsed.deaths)


def test_parse_log_directory_skips_old_rolled_gz_files_outside_day_window(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()

    # Old gz file far outside the target day window — intentionally corrupt gz to prove it's never opened!
    old_gz = logs / "2025-01-01-1.log.gz"
    old_gz.write_bytes(b"not-valid-gzip-must-not-be-read")

    # Target day rolled gz
    target_gz = logs / "2026-10-25-1.log.gz"
    content = (
        "[10:00:00] [Server thread/INFO]: System chat: Steve joined the game\n"
        "[11:00:00] [Server thread/INFO]: System chat: Steve left the game\n"
    )
    with gzip.open(target_gz, "wt", encoding="utf-8") as fh:
        fh.write(content)

    # Newer rolled file well past target day (so latest.log is also skipped)
    newer = logs / "2026-11-20-1.log"
    newer.write_text("[10:00:00] [Server thread/INFO]: System chat: Bob joined the game\n")
    (logs / "latest.log").write_text("[12:00:00] [Server thread/INFO]: System chat: Carol joined the game\n")

    parsed = parse_data_dir(tmp_path, day=date(2026, 10, 25))
    assert parsed.files_found == 1
    assert len(parsed.sessions) == 1
    assert parsed.sessions[0].player == "Steve"
    assert parsed.sessions[0].duration_seconds == 3600


def test_parse_missing_dir_raises(tmp_path):
    with pytest.raises(MinecraftLogError):
        parse_log_directory(tmp_path / "nope")


def test_camera_sessions_drop_fully_covered_and_subtract_partial_overlaps(tmp_path):
    cam_dir = tmp_path / "plugins" / "Camera"
    cam_dir.mkdir(parents=True)
    t0 = datetime(2026, 10, 25, 10, 0, tzinfo=EASTERN)
    rows = [
        {"player": "CamBot", "start": t0.isoformat(), "end": (t0 + timedelta(hours=1)).isoformat()},
        {"player": "Alice", "start": (t0 + timedelta(minutes=30)).isoformat(), "end": (t0 + timedelta(minutes=45)).isoformat()},
    ]
    (cam_dir / "sessions.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n{bad json\n",
        encoding="utf-8",
    )

    cameras = load_camera_sessions(tmp_path)
    assert len(cameras) == 2

    parsed = ParsedLogs(
        sessions=[
            Session("CamBot", t0, t0 + timedelta(hours=1), "leave"),
            Session("Alice", t0, t0 + timedelta(hours=1), "leave"),
        ]
    )
    exclude_camera_sessions(parsed, cameras)
    assert [s.player for s in parsed.sessions] == ["Alice"]
    overlap = camera_overlap_seconds(t0, t0 + timedelta(hours=1), cameras, "Alice")
    assert overlap == 15 * 60
