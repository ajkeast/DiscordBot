"""Playday screenshot picker for the daily Minecraft recap."""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from config import minecraft_screenshot_dir

logger = logging.getLogger(__name__)

_SCREENSHOT_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
_SCREENSHOT_STAMP = re.compile(
    r"^\d{4}-?\d{2}-?\d{2}[T_\-]?\d{2}[-:]?\d{2}[-:]?\d{2}"
)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def pick_recap_screenshot(day: date, root: Path | None = None) -> Path | None:
    """Return the newest image in ``<root>/<YYYY-MM-DD>``, or None if absent."""
    folder = (root if root is not None else minecraft_screenshot_dir()) / day.isoformat()
    if not folder.is_dir():
        return None
    try:
        entries = list(folder.iterdir())
    except OSError:
        logger.warning("Could not read Minecraft recap screenshots in %s", folder)
        return None

    images = [
        path
        for path in entries
        if path.is_file() and path.suffix.lower() in _SCREENSHOT_SUFFIXES
    ]
    if not images:
        return None

    stamped = [path for path in images if _SCREENSHOT_STAMP.match(path.stem)]
    if stamped:
        return max(stamped, key=lambda p: (p.stem, _mtime(p)))
    return max(images, key=_mtime)
