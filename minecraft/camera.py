"""Camera-plugin session filter (plugins/Camera/sessions.jsonl)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from minecraft.models import CameraSession, Session

CAMERA_GRACE = timedelta(seconds=5)
_CAMERA_OPEN = datetime(9999, 1, 1, tzinfo=timezone.utc)


def _parse_instant(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_camera_sessions(data_dir: Path) -> list[CameraSession]:
    """Load camera-mode intervals from ``plugins/Camera/sessions.jsonl``."""
    path = data_dir / "plugins" / "Camera" / "sessions.jsonl"
    if not path.is_file():
        return []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    sessions: list[CameraSession] = []
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        player = row.get("player")
        start = _parse_instant(row.get("start"))
        if not isinstance(player, str) or not player.strip() or start is None:
            continue
        end = _parse_instant(row.get("end")) if row.get("end") else None
        if end is not None and end < start:
            continue
        sessions.append(CameraSession(player=player.strip(), start=start, end=end))
    return sessions


def _camera_end(camera: CameraSession) -> datetime:
    return camera.end if camera.end is not None else _CAMERA_OPEN


def _session_covered(session: Session, cameras: list[CameraSession]) -> bool:
    own = [c for c in cameras if c.player == session.player]
    if not own:
        return False
    for cam in own:
        end = _camera_end(cam)
        if session.start >= cam.start - CAMERA_GRACE and session.end <= end + CAMERA_GRACE:
            return True
    spans = sorted(
        (max(c.start, session.start), min(_camera_end(c), session.end))
        for c in own
        if _camera_end(c) > session.start and c.start < session.end
    )
    cursor = session.start
    for start, end in spans:
        if start > cursor:
            return False
        if end > cursor:
            cursor = end
    return cursor >= session.end


def camera_overlap_seconds(
    start: datetime,
    end: datetime,
    cameras: list[CameraSession],
    player: str,
) -> int:
    spans = sorted(
        (max(start, c.start), min(end, _camera_end(c)))
        for c in cameras
        if c.player == player and min(end, _camera_end(c)) > max(start, c.start)
    )
    if not spans:
        return 0
    total = 0.0
    cur_lo, cur_hi = spans[0]
    for lo, hi in spans[1:]:
        if lo <= cur_hi:
            if hi > cur_hi:
                cur_hi = hi
        else:
            total += (cur_hi - cur_lo).total_seconds()
            cur_lo, cur_hi = lo, hi
    total += (cur_hi - cur_lo).total_seconds()
    return int(round(total))


def exclude_camera_sessions(
    sessions: list[Session], cameras: list[CameraSession]
) -> list[Session]:
    if not cameras:
        return sessions
    return [s for s in sessions if not _session_covered(s, cameras)]
