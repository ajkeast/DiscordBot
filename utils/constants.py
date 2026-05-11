"""
Constants used throughout the Discord bot.
"""

# Channel ID for #general
GENERAL_CHANNEL_ID = 94235299445493760

# Universal embed color
EMBED_COLOR = 0x4d4170

# Bot's user ID for recipe creation
BOT_USER_ID = 908765514753531934

# Max turns (user + assistant pairs) per Grok session before starting a new conversation.
# Limits context growth and cost; each turn adds more tokens to the stored session.
MAX_GROK_SESSION_TURNS = 20
