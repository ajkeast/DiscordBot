"""Tests for self-knowledge docs, privacy hygiene, and live tool handlers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from cogs.utility import Utility
from db.dinkcoin import LedgerEntry
from services import self_knowledge
from tests.reporting import SECTION_SELF_KNOWLEDGE, assert_eq

EXPECTED_TOOLS = frozenset(
    {
        "get_bot_documentation",
        "list_bot_commands",
        "get_first_game_stats",
        "get_juice_stats",
        "get_dink_ledger",
    }
)


def test_all_topic_docs_present_and_music_doc_has_no_internal_infra(report):
    missing = [
        topic
        for topic in self_knowledge.TOPICS
        if not self_knowledge.get_topic(topic).startswith("#")
    ]
    assert_eq(report, SECTION_SELF_KNOWLEDGE, "missing docs", [], missing)

    music_doc = self_knowledge.get_topic("music").lower()
    assert "lavalink" not in music_doc
    assert "soundy" not in music_doc
    assert "vps" not in music_doc


def test_tool_registry_and_command_reference(mock_bot):
    schema_names = {s["name"] for s in self_knowledge.TOOL_SCHEMAS}
    handler_names = set(self_knowledge.build_tool_handlers(bot=None))
    assert schema_names == handler_names == EXPECTED_TOOLS

    fake_bot = MagicMock()
    fake_bot.cogs = {"Utility": Utility(mock_bot)}
    ref = self_knowledge.build_command_reference(fake_bot)
    assert "/ping" in ref
    assert "/donation" in ref


def test_live_data_tools_with_injected_repositories(sample_first_entries):
    first_repo = MagicMock()
    first_repo.get_all_entries.return_value = sample_first_entries
    dink_repo = MagicMock()
    dink_repo.get_ledger.return_value = [LedgerEntry("111", 12.0), LedgerEntry("222", 5.0)]
    dink_repo.get_total_circulation.return_value = 17.0

    first_data = json.loads(self_knowledge.get_first_game_stats(first_repo=first_repo))
    assert first_data["leaderboard_top10"][0] == {"name": "User 111", "wins": 3}

    juice_data = json.loads(self_knowledge.get_juice_stats(first_repo=first_repo))
    assert juice_data["most_total_juice"]["name"] in {"User 111", "User 222"}

    dink_data = json.loads(self_knowledge.get_dink_ledger_stats(dink_repo=dink_repo))
    assert dink_data["total_circulation"] == 17.0
    assert dink_data["top_holders"][0] == {"name": "User 111", "balance": 12.0}
