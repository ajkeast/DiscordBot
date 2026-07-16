"""
Self-knowledge toolkit for the Grok integration.

Gives Grok a set of client-side tools it can call to understand the bot it is
part of: documentation lookups (docs/self_knowledge/*.md), the live command
list, and live game/ledger data. Tool schemas are plain dicts so this module
stays independent of the xAI SDK; chatgpt_functions.py converts them.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs" / "self_knowledge"

# topic name -> one-line description surfaced in the tool schema
TOPICS = {
    "overview": "What the bot is and what it can do for server members",
    "first_game": "Rules of the daily /1st game: claiming, score, streaks, and juice",
    "dinkcoin": "How DINK works: earning it, spending it, and the leaderboard",
    "ai_features": "How /ask, /imagine, /voice, and /clear work for members",
    "server_stats": "Activity tracking, the monthly report, and the public dashboard",
}


def get_topic(topic: str) -> str:
    """Return the markdown documentation for a topic."""
    if topic not in TOPICS:
        available = ", ".join(sorted(TOPICS))
        return f"Unknown topic '{topic}'. Available topics: {available}"
    path = DOCS_DIR / f"{topic}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("Failed to read self-knowledge doc %s", path)
        return f"Documentation for '{topic}' is unavailable right now."


def build_command_reference(bot) -> str:
    """Build a live command reference from the bot's registered cogs."""
    if bot is None:
        return (
            "Live command registry unavailable. Use get_bot_documentation "
            "(topics: overview, first_game, dinkcoin, ai_features) for command lists."
        )
    lines = ["Commands (slash /name; underscore _name also works, case-insensitive):"]
    for cog_name, cog in bot.cogs.items():
        cog_doc = (cog.__doc__ or "").strip().splitlines()
        lines.append(f"\n{cog_name}: {cog_doc[0] if cog_doc else ''}".rstrip())
        for cmd in cog.get_commands():
            help_text = (cmd.help or cmd.brief or "").strip().splitlines()
            summary = help_text[0] if help_text else ""
            params = " ".join(
                f"<{p}>" for p in cmd.clean_params
            )
            usage = f"/{cmd.name} {params}".strip()
            lines.append(f"  {usage} — {summary}")
    return "\n".join(lines)


def _resolve_name(bot, user_id) -> str:
    if bot is not None:
        user = bot.get_user(int(user_id))
        if user is not None:
            return user.display_name
    return f"User {user_id}"


def get_first_game_stats(bot=None) -> str:
    """Live first-game data: leaderboard, current streak, most recent winner."""
    from utils.db import db_ops, streak_calc

    df = db_ops.get_table_data("firstlist_id")
    if df.empty:
        return json.dumps({"message": "No firsts have been claimed yet."})

    counts = df.user_id.value_counts()
    leaderboard = [
        {"name": _resolve_name(bot, uid), "wins": int(wins)}
        for uid, wins in counts.head(10).items()
    ]
    latest_uid = df.user_id.iloc[-1]
    return json.dumps({
        "leaderboard_top10": leaderboard,
        "total_days_played": int(len(df)),
        "most_recent_winner": _resolve_name(bot, latest_uid),
        "most_recent_win_utc": str(df.timesent.iloc[-1]),
        "current_streak_days": int(streak_calc.calculate_streak(df)),
    })


def get_juice_stats(bot=None) -> str:
    """Live juice data: total-juice leaderboard and single-day high score."""
    from utils.db import db_ops, juice_calc

    df = db_ops.get_table_data("firstlist_id")
    if df.empty:
        return json.dumps({"message": "No firsts have been claimed yet."})

    juice_df, highscore_user_id, highscore_value = juice_calc.calculate_juice(df)
    leaderboard = [
        {
            "name": _resolve_name(bot, row["user_id"]),
            "total_juice": int(row["juice"]),
        }
        for _, row in juice_df.head(10).iterrows()
    ]
    return json.dumps({
        "leaderboard_top10_total_juice": leaderboard,
        "most_total_juice": leaderboard[0] if leaderboard else None,
        "single_day_high": {
            "name": _resolve_name(bot, highscore_user_id),
            "juice": int(highscore_value),
        },
    })


def get_dink_ledger_stats(bot=None) -> str:
    """Live DinkCoin data: top holders and total circulation."""
    from utils.db import db_ops

    df = db_ops.get_dink_ledger(10)
    holders = [
        {"name": _resolve_name(bot, row["user_id"]), "balance": float(row["balance"])}
        for _, row in df.iterrows()
    ]
    return json.dumps({
        "top_holders": holders,
        "total_circulation": db_ops.get_total_dink_circulation(),
    })


# Plain-dict tool schemas (converted to xAI SDK tools in chatgpt_functions.py)
TOOL_SCHEMAS = [
    {
        "name": "get_bot_documentation",
        "description": (
            "Look up member-facing documentation about the bot. Use when someone "
            "asks what you can do, how commands work, the /1st game, juice, DINK, "
            "chat/imagine/voice, or server stats. Answer in plain language for "
            "Discord users — never expose internal implementation details. "
            "Topics: " + "; ".join(f"'{k}' = {v}" for k, v in TOPICS.items())
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": sorted(TOPICS),
                    "description": "Documentation topic to retrieve",
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "list_bot_commands",
        "description": (
            "List every command the bot currently has registered, grouped by "
            "feature, with usage and a short description. Use this when someone "
            "asks what commands exist or how to invoke one."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_first_game_stats",
        "description": (
            "Fetch live data for the daily first game: top-10 win leaderboard, "
            "most recent winner, and the current streak. Use for questions about "
            "who has the most first wins or who got first today — not juice."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_juice_stats",
        "description": (
            "Fetch live juice leaderboard data: top-10 players by total juice, "
            "who has the most juice overall, and the single-day high score. "
            "Use for any question about who has the most juice, juice rankings, "
            "or juice records. Higher juice is better — it rewards claiming /1st "
            "as late in the day as possible."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_dink_ledger",
        "description": (
            "Fetch the live DinkCoin ledger: top-10 DINK holders and total DINK "
            "in circulation. Use for questions about who is richest or how much "
            "DINK exists."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
]


def build_tool_handlers(bot=None) -> dict:
    """Map tool names to callables. Handlers take the parsed JSON args dict."""
    return {
        "get_bot_documentation": lambda args: get_topic(args.get("topic", "")),
        "list_bot_commands": lambda args: build_command_reference(bot),
        "get_first_game_stats": lambda args: get_first_game_stats(bot),
        "get_juice_stats": lambda args: get_juice_stats(bot),
        "get_dink_ledger": lambda args: get_dink_ledger_stats(bot),
    }
