"""Tests for Grok Minecraft narrative generation, fallback, and screenshots."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

from config import EASTERN
from minecraft.analytics import summarize_day
from minecraft.day import activity_date_due
from minecraft.models import Death, Session
from minecraft.narrative import (
    SYSTEM_PROMPT,
    clean_model_text,
    format_fallback,
    generate_narrative,
    is_valid_narrative,
)
from minecraft.screenshots import pick_recap_screenshot


def _sample_recap():
    day = date(2026, 10, 25)
    t0 = datetime(2026, 10, 25, 20, 0, tzinfo=EASTERN)
    sessions = [
        Session("Steve", t0, t0 + timedelta(hours=2), "leave"),
        Session(
            "Alex",
            t0 + timedelta(minutes=15),
            t0 + timedelta(hours=1, minutes=45),
            "leave",
        ),
    ]
    deaths = [
        Death("Steve", t0 + timedelta(minutes=30), "fell from a high place"),
        Death("Steve", t0 + timedelta(minutes=35), "drowned"),
        Death("Alex", t0 + timedelta(minutes=40), "was slain by Wither Skeleton"),
    ]
    return summarize_day(sessions, deaths, day, history_known=True)


def test_validator_has_no_rigid_keyword_or_mob_rejections():
    recap = _sample_recap()
    natural_reply = (
        "Two players hopped on for 3h 30m.\n\n"
        "**Playtime**\n"
        "- **Steve**: 2h (1 session) — first appearance on the server!\n"
        "- **Alex**: 1h 30m (1 session) — first appearance on the server!\n\n"
        "**Deaths (3)**\n"
        "Steve took a fatal fall and then ran out of air drowning, "
        "while Alex got cut down by a Wither Skeleton in a Nether fortress."
    )
    assert is_valid_narrative(natural_reply, recap) is True


def test_validator_still_guards_structural_sanity():
    recap = _sample_recap()
    assert is_valid_narrative("", recap) is False
    assert is_valid_narrative("x" * 2000, recap) is False
    assert is_valid_narrative("Steve played 2 hours and died twice.", recap) is False


def test_system_prompt_covers_required_voice_and_death_rules():
    prompt = SYSTEM_PROMPT.lower()
    assert "advancements" in prompt
    assert "we" in prompt and "us" in prompt and "our" in prompt
    assert "pvp" in prompt
    assert "third-party" in prompt or "third party" in prompt
    assert "ghast" not in prompt
    assert "piglin" not in prompt


def test_generate_narrative_uses_grok_and_falls_back_on_error(monkeypatch):
    recap = _sample_recap()
    monkeypatch.setenv("XAI_API_KEY", "test-key")

    client = MagicMock()
    chat = MagicMock()
    client.chat.create.return_value = chat
    chat.sample.return_value = MagicMock(
        content="```markdown\nSteve and Alex played 3h 30m and took 3 deaths.\n```"
    )
    text, source = generate_narrative(recap, client=client)
    assert source == "grok"
    assert not text.startswith("```")
    assert "Steve" in text and "Alex" in text

    client.chat.create.side_effect = RuntimeError("timeout")
    with patch("minecraft.narrative.time.sleep"):
        fallback, source = generate_narrative(recap, client=client)
    assert source == "template"
    assert "**Time played**" in fallback
    assert "Steve" in fallback and "Alex" in fallback


def test_format_fallback_handles_nobody_played():
    day = date(2026, 10, 25)
    empty = summarize_day([], [], day)
    assert format_fallback(empty) == "Nobody played Minecraft on Sunday, October 25, 2026."
    assert clean_model_text("```\nhello\n```") == "hello"


def test_activity_date_due_and_screenshot_picker(tmp_path):
    assert activity_date_due(datetime(2026, 10, 26, 7, 59, tzinfo=EASTERN)) is None
    assert activity_date_due(datetime(2026, 10, 26, 8, 0, tzinfo=EASTERN)) == date(
        2026, 10, 25
    )

    day = date(2026, 10, 25)
    folder = tmp_path / day.isoformat()
    folder.mkdir()
    early = folder / "2026-10-25T10-00-00.png"
    late = folder / "2026-10-25T22-30-00.png"
    early.write_bytes(b"a")
    late.write_bytes(b"b")
    assert pick_recap_screenshot(day, tmp_path) == late
