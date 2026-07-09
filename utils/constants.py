"""
Constants used throughout the Discord bot.
"""

import os

# Channel ID for #general
GENERAL_CHANNEL_ID = 94235299445493760

# Universal embed color
EMBED_COLOR = 0x4d4170

# Bot's user ID for recipe creation
BOT_USER_ID = 908765514753531934

# Max turns (user + assistant pairs) per Grok session before starting a new conversation.
# Limits context growth and cost; each turn adds more tokens to the stored session.
MAX_GROK_SESSION_TURNS = 20

# Grok Imagine multi-reference edit limit (xAI API: up to 3 source images).
MAX_IMAGINE_INPUT_IMAGES = 3

# Per-user _imagine rate limit (discord.py cooldown: rate uses per period seconds).
IMAGINE_RATE_LIMIT = 30
IMAGINE_RATE_PERIOD_SECONDS = 3600

# DINK awarded for each successful _1st claim (MySQL ledger only)
DINK_MINT_AMOUNT = float(os.getenv("DINK_MINT_AMOUNT", "1"))
