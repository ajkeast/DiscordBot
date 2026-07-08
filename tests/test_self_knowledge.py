"""Tests for the Grok self-knowledge tools and the client-side tool-call loop."""

import json
from unittest.mock import MagicMock, patch

import pandas as pd

from tests.reporting import SECTION_UNIT
from utils import self_knowledge


def test_every_topic_has_a_doc(report):
    for topic in self_knowledge.TOPICS:
        content = self_knowledge.get_topic(topic)
        report.record(f"doc '{topic}'", "markdown content", content[:60], section=SECTION_UNIT)
        assert content.startswith("#"), f"doc for {topic} missing or malformed"


def test_unknown_topic_lists_available(report):
    result = self_knowledge.get_topic("nonsense")
    report.record("unknown topic", "error listing topics", result, section=SECTION_UNIT)
    assert "Unknown topic" in result
    for topic in self_knowledge.TOPICS:
        assert topic in result


def test_tool_schemas_match_handlers(report):
    schema_names = {schema["name"] for schema in self_knowledge.TOOL_SCHEMAS}
    handler_names = set(self_knowledge.build_tool_handlers(bot=None))
    report.record("tool names", sorted(schema_names), sorted(handler_names), section=SECTION_UNIT)
    assert schema_names == handler_names


def test_build_command_reference_lists_real_commands(report, mock_bot):
    from cogs.utility import Utility

    cog = Utility(mock_bot)
    fake_bot = MagicMock()
    fake_bot.cogs = {"Utility": cog}

    reference = self_knowledge.build_command_reference(fake_bot)

    report.record("command reference", "_ping, _hello, _simonsays", reference, section=SECTION_UNIT)
    assert "_ping" in reference
    assert "_hello" in reference
    assert "_simonsays <arg>" in reference


def test_get_first_game_stats(report, mock_db_ops, sample_first_df):
    mock_db_ops.get_table_data.return_value = sample_first_df

    data = json.loads(self_knowledge.get_first_game_stats(bot=None))

    report.record("leaderboard", "111 has 3 wins", data["leaderboard_top10"][0], section=SECTION_UNIT)
    report.record("recent winner", "User 111", data["most_recent_winner"], section=SECTION_UNIT)
    assert data["leaderboard_top10"][0] == {"name": "User 111", "wins": 3}
    assert data["most_recent_winner"] == "User 111"
    assert data["total_days_played"] == 4
    assert data["current_streak_days"] == 1


def test_get_dink_ledger_stats(report, mock_db_ops):
    mock_db_ops.get_dink_ledger.return_value = pd.DataFrame(
        {"user_id": ["111", "222"], "balance": [12.0, 5.0]}
    )
    mock_db_ops.get_total_dink_circulation.return_value = 17.0

    data = json.loads(self_knowledge.get_dink_ledger_stats(bot=None))

    report.record("top holder", "User 111 with 12 DINK", data["top_holders"][0], section=SECTION_UNIT)
    report.record("circulation", 17.0, data["total_circulation"], section=SECTION_UNIT)
    assert data["top_holders"][0] == {"name": "User 111", "balance": 12.0}
    assert data["total_circulation"] == 17.0


def _make_tool_call(name: str, arguments: dict, call_id: str = "call-1"):
    tool_call = MagicMock()
    tool_call.id = call_id
    tool_call.function.name = name
    tool_call.function.arguments = json.dumps(arguments)
    return tool_call


def test_send_message_executes_self_knowledge_tools(report, mock_db_ops):
    """Grok requests a doc lookup; the client executes it and returns the final text."""
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

    tool_messages = [
        call.args[0]
        for call in chat.append.call_args_list
        if getattr(call.args[0], "tool_call_id", None) == "call-1"
    ]
    doc_sent_to_grok = tool_messages[0].content[0].text

    report.record("sample calls", 2, chat.sample.call_count, section=SECTION_UNIT)
    report.record("tool result", "DinkCoin markdown doc", doc_sent_to_grok[:60], section=SECTION_UNIT)
    report.record("final text", final_response.content, text, section=SECTION_UNIT)
    report.record("next id", "resp-final", next_id, section=SECTION_UNIT)

    assert chat.sample.call_count == 2
    assert "DinkCoin" in doc_sent_to_grok
    assert text == "DINK is minted by winning _1st."
    assert next_id == "resp-final"


def test_send_message_caps_tool_rounds(report, mock_db_ops):
    """A model that keeps requesting tools is cut off after MAX_TOOL_ROUNDS."""
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

    report.record("sample calls", MAX_TOOL_ROUNDS + 1, chat.sample.call_count, section=SECTION_UNIT)
    assert chat.sample.call_count == MAX_TOOL_ROUNDS + 1
