"""Tests for play-day windows and camera-session filtering."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from config import EASTERN
from minecraft.camera import (
    camera_overlap_seconds,
    exclude_camera_sessions,
    load_camera_sessions,
)
from minecraft.day import day_window, playday_date
from minecraft.models import Session


def test_playday_date_and_window_boundary():
    before = datetime(2026, 11, 18, 4, 59, tzinfo=EASTERN)
    at_open = datetime(2026, 11, 18, 5, 0, tzinfo=EASTERN)
    assert playday_date(before) == date(2026, 11, 17)
    assert playday_date(at_open) == date(2026, 11, 18)

    ws, we = day_window(date(2026, 10, 25))
    assert ws == datetime(2026, 10, 25, 5, 0, tzinfo=EASTERN)
    assert we == datetime(2026, 10, 26, 5, 0, tzinfo=EASTERN)


def test_camera_sessions_drop_fully_covered_and_subtract_partial_overlaps(tmp_path: Path):
    cam_dir = tmp_path / "plugins" / "Camera"
    cam_dir.mkdir(parents=True)
    t0 = datetime(2026, 10, 25, 10, 0, tzinfo=EASTERN)
    rows = [
        {
            "player": "CamBot",
            "start": t0.isoformat(),
            "end": (t0 + timedelta(hours=1)).isoformat(),
        },
        {
            "player": "Alice",
            "start": (t0 + timedelta(minutes=30)).isoformat(),
            "end": (t0 + timedelta(minutes=45)).isoformat(),
        },
    ]
    (cam_dir / "sessions.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n{bad json\n",
        encoding="utf-8",
    )

    cameras = load_camera_sessions(tmp_path)
    assert len(cameras) == 2

    sessions = [
        Session("CamBot", t0, t0 + timedelta(hours=1), "leave"),
        Session("Alice", t0, t0 + timedelta(hours=1), "leave"),
    ]
    kept = exclude_camera_sessions(sessions, cameras)
    assert [s.player for s in kept] == ["Alice"]
    overlap = camera_overlap_seconds(t0, t0 + timedelta(hours=1), cameras, "Alice")
    assert overlap == 15 * 60
