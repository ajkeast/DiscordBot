"""Tests for Grok self-knowledge tools and the client-side tool-call loop."""

import json
from unittest.mock import MagicMock, patch

import pandas as pd

from tests.reporting import SECTION_SELF_KNOWLEDGE, assert_eq
from utils import self_knowledge

EXPECTED_TOOL_NAMES = frozenset({
    "get_bot_documentation",
    "list_bot_commands",
    "get_first_game_stats",
    "get_juice_stats",
    "get_dink_ledger",
})


def test_all_topic_docs_present(report):
    missing = [
        topic
        for topic in self_knowledge.TOPICS
        if not self_knowledge.get_topic(topic).startswith("#")
    ]
    assert_eq(
        report,
        SECTION_SELF_KNOWLEDGE,
        "topic docs",
        "all present",
        "all present" if not missing else f"missing: {', '.join(missing)}",
    )


def test_unknown_topic_lists_available(report):
    result = self_knowledge.get_topic("nonsense")
    assert "Unknown topic" in result
    assert_eq(report, SECTION_SELF_KNOWLEDGE, "unknown topic hint", "lists topics", "lists topics")


def test_tool_registry(report):
    schema_names = {schema["name"] for schema in self_knowledge.TOOL_SCHEMAS}
    handler_names = set(self_knowledge.build_tool_handlers(bot=None))
    assert schema_names == handler_names == EXPECTED_TOOL_NAMES
    assert_eq(report, SECTION_SELF_KNOWLEDGE, "registered tools", 5, len(schema_names))


def test_first_game_doc_juice_rules(report):
    doc = self_knowledge.get_topic("first_game").lower()
    assert "more juice is better" in doc
    assert "wait as long as possible" in doc
    assert_eq(report, SECTION_SELF_KNOWLEDGE, "juice rules in doc", "present", "present")


def test_build_command_reference_lists_real_commands(report, mock_bot):
    from cogs.utility import Utility

    fake_bot = MagicMock()
    fake_bot.cogs = {"Utility": Utility(mock_bot)}
    reference = self_knowledge.build_command_reference(fake_bot)

    assert "/ping" in reference
    assert "/hello" in reference
    assert_eq(report, SECTION_SELF_KNOWLEDGE, "command reference", "lists /ping", "lists /ping")


def test_get_first_game_stats(report, mock_db_ops, sample_first_df):
    mock_db_ops.get_table_data.return_value = sample_first_df
    data = json.loads(self_knowledge.get_first_game_stats(bot=None))

    assert data["leaderboard_top10"][0] == {"name": "User 111", "wins": 3}
    assert data["most_recent_winner"] == "User 111"
    assert data["current_streak_days"] == 1
    assert_eq(report, SECTION_SELF_KNOWLEDGE, "first wins leader", "User 111 (3)", "User 111 (3)")


def test_get_juice_stats(report, mock_db_ops, sample_first_df):
    mock_db_ops.get_table_data.return_value = sample_first_df
    data = json.loads(self_knowledge.get_juice_stats(bot=None))

    leader = data["most_total_juice"]
    assert leader["name"] == data["leaderboard_top10_total_juice"][0]["name"]
    assert "single_day_high" in data
    assert "how_juice_works" not in data
    assert_eq(
        report,
        SECTION_SELF_KNOWLEDGE,
        "juice leader",
        leader["name"],
        leader["name"],
    )


def test_get_juice_stats_empty(report, mock_db_ops, empty_first_df):
    mock_db_ops.get_table_data.return_value = empty_first_df
    data = json.loads(self_knowledge.get_juice_stats(bot=None))
    assert_eq(
        report,
        SECTION_SELF_KNOWLEDGE,
        "empty juice stats",
        "No firsts have been claimed yet.",
        data["message"],
    )


def test_get_dink_ledger_stats(report, mock_db_ops):
    mock_db_ops.get_dink_ledger.return_value = pd.DataFrame(
        {"user_id": ["111", "222"], "balance": [12.0, 5.0]}
    )
    mock_db_ops.get_total_dink_circulation.return_value = 17.0
    data = json.loads(self_knowledge.get_dink_ledger_stats(bot=None))

    assert data["top_holders"][0] == {"name": "User 111", "balance": 12.0}
    assert_eq(report, SECTION_SELF_KNOWLEDGE, "DINK circulation", 17.0, data["total_circulation"])


def test_ai_system_prompt_uses_self_knowledge_tools(report, mock_bot):
    from cogs.ai import AI

    with patch("cogs.ai.GrokClient"):
        cog = AI(mock_bot)

    assert "get_juice_stats" in cog.system_prompt
    assert "get_dink_ledger" in cog.system_prompt
    assert_eq(report, SECTION_SELF_KNOWLEDGE, "AI prompt tools", "juice + dink", "juice + dink")


def test_ai_system_prompt_includes_eastern_date(report, mock_bot):
    from cogs.ai import AI, EASTERN
    from datetime import datetime

    with patch("cogs.ai.GrokClient"):
        cog = AI(mock_bot)

    prompt = cog._build_system_prompt()
    today = datetime.now(EASTERN).strftime("%B %d, %Y")
    assert "US/Eastern" in prompt
    assert today in prompt
    date_clause = prompt.split("Today's date is", 1)[1]
    assert ":" not in date_clause  # date only, no clock time
    assert_eq(report, SECTION_SELF_KNOWLEDGE, "AI prompt date", today, today)


def test_daily_chat_clear_resets_session_at_clear_hour(report, mock_bot):
    from cogs.ai import AI, DAILY_CLEAR_HOUR, EASTERN
    from datetime import datetime

    with patch("cogs.ai.GrokClient"):
        cog = AI(mock_bot)

    cog.last_response_id = "stale-id"
    cog._session_turns = 7
    clear_time = EASTERN.localize(datetime(2026, 7, 10, DAILY_CLEAR_HOUR, 0, 0))
    with patch("cogs.ai.datetime") as mock_dt:
        mock_dt.now.return_value = clear_time
        cleared = cog._clear_session_if_new_day()

    assert cleared is True
    assert cog.last_response_id is None
    assert cog._session_turns == 0
    assert_eq(report, SECTION_SELF_KNOWLEDGE, "daily clear", "reset", "reset")


def test_daily_chat_clear_skips_other_hours(report, mock_bot):
    from cogs.ai import AI, EASTERN
    from datetime import datetime

    with patch("cogs.ai.GrokClient"):
        cog = AI(mock_bot)

    cog.last_response_id = "keep-me"
    cog._session_turns = 3
    noon = EASTERN.localize(datetime(2026, 7, 10, 12, 0, 0))
    with patch("cogs.ai.datetime") as mock_dt:
        mock_dt.now.return_value = noon
        cleared = cog._clear_session_if_new_day()

    assert cleared is False
    assert cog.last_response_id == "keep-me"
    assert cog._session_turns == 3
    assert_eq(report, SECTION_SELF_KNOWLEDGE, "non-clear-hour", "kept", "kept")


def _make_tool_call(name: str, arguments: dict, call_id: str = "call-1"):
    tool_call = MagicMock()
    tool_call.id = call_id
    tool_call.function.name = name
    tool_call.function.arguments = json.dumps(arguments)
    return tool_call


def test_grok_client_executes_doc_tool(report, mock_db_ops):
    from chatgpt_functions import GrokClient

    with patch("chatgpt_functions.Client") as mock_client_cls:
        grok = GrokClient(api_key="test-key", bot=None)
        chat = MagicMock()
        mock_client_cls.return_value.chat.create.return_value = chat

        tool_response = MagicMock()
        tool_response.tool_calls = [
            _make_tool_call("get_bot_documentation", {"topic": "dinkcoin"})
        ]
        final_response = MagicMock()
        final_response.tool_calls = []
        final_response.id = "resp-final"
        final_response.content = "DINK is minted by winning _1st."
        chat.sample.side_effect = [tool_response, final_response]

        next_id, text = grok.send_message("how does dinkcoin work?", system_prompt="sys")

    assert chat.sample.call_count == 2
    assert text == "DINK is minted by winning _1st."
    assert next_id == "resp-final"
    assert_eq(report, SECTION_SELF_KNOWLEDGE, "doc tool loop", "dinkcoin", "dinkcoin")


def test_grok_client_caps_tool_rounds(report, mock_db_ops):
    from chatgpt_functions import MAX_TOOL_ROUNDS, GrokClient

    with patch("chatgpt_functions.Client") as mock_client_cls:
        grok = GrokClient(api_key="test-key", bot=None)
        chat = MagicMock()
        mock_client_cls.return_value.chat.create.return_value = chat

        looping_response = MagicMock()
        looping_response.tool_calls = [_make_tool_call("list_bot_commands", {})]
        looping_response.id = "resp-loop"
        looping_response.content = "still thinking"
        chat.sample.return_value = looping_response

        grok.send_message("hi", system_prompt="sys")

    assert_eq(
        report,
        SECTION_SELF_KNOWLEDGE,
        "tool round cap",
        MAX_TOOL_ROUNDS + 1,
        chat.sample.call_count,
    )
