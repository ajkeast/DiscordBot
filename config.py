"""Centralized configuration, constants, and timezone helpers for DiscordBot."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")

GENERAL_CHANNEL_ID = 94235299445493760
DEFAULT_MINECRAFT_CHANNEL_ID = 581136950074015761
EMBED_COLOR = 0x4D4170

MAX_GROK_SESSION_TURNS = 20
MAX_IMAGINE_INPUT_IMAGES = 1
IMAGINE_RATE_LIMIT = 30
IMAGINE_RATE_PERIOD_SECONDS = 3600

DINKSCORD_URL = "https://dinkscord.com"
PROMOTE_DINKSCORD_ON_FIRST = True

DEFAULT_GROK_MODEL = "grok-4.3"
GROK_IMAGINE_MODEL = "grok-imagine-image-2.0"
GROK_IMAGINE_FILENAME = "grok-imagine.jpg"
DEFAULT_SENTIMENT_MODEL = "grok-4.3"
DEFAULT_MINECRAFT_MODEL = "grok-4.7"

DEFAULT_MINECRAFT_DATA_DIR = "/minecraft"
DEFAULT_MINECRAFT_SCREENSHOT_DIR = "/recap-screenshots"
DEFAULT_LAVALINK_URI = "http://127.0.0.1:2333"
DEFAULT_MUSIC_INACTIVE_TIMEOUT = 300
DEFAULT_SENTIMENT_BATCH_SIZE = 10


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    user: str | None
    password: str | None
    dbname: str | None
    min_size: int = 1
    max_size: int = 10


def env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name, "").strip()
    return raw or default


def env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("Ignoring non-positive %s=%s; using %s", name, value, default)
        return default
    return value


def env_optional_positive_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r", name, raw)
        return None
    if value <= 0:
        logger.warning("Ignoring non-positive %s=%s", name, value)
        return None
    return value


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default


def discord_token() -> str:
    return env_str("DISCORD_TOKEN")


def discord_guild_id() -> int | None:
    return env_optional_positive_int("DISCORD_GUILD_ID")


def xai_api_key() -> str:
    return env_str("XAI_API_KEY")


def dink_mint_amount() -> float:
    return env_float("DINK_MINT_AMOUNT", 1.0)


def db_config() -> DatabaseConfig:
    host = env_str("SQL_HOST", "localhost")
    port = env_positive_int("SQL_PORT", 5432)
    if ":" in host:
        host_part, port_str = host.rsplit(":", 1)
        try:
            parsed_port = int(port_str)
            if parsed_port > 0:
                host = host_part
                port = parsed_port
        except ValueError:
            logger.warning("Ignoring invalid port in SQL_HOST=%r; using %s", host, port)
    return DatabaseConfig(
        host=host,
        port=port,
        user=os.getenv("SQL_USER"),
        password=os.getenv("SQL_PASSWORD"),
        dbname=os.getenv("SQL_DATABASE"),
        min_size=env_positive_int("SQL_POOL_MIN_SIZE", 1),
        max_size=env_positive_int("SQL_POOL_MAX_SIZE", 10),
    )


def lavalink_uri() -> str:
    return env_str("LAVALINK_URI", DEFAULT_LAVALINK_URI)


def lavalink_password() -> str:
    return env_str("LAVALINK_PASSWORD")


def music_inactive_timeout() -> int:
    return env_positive_int("MUSIC_INACTIVE_TIMEOUT", DEFAULT_MUSIC_INACTIVE_TIMEOUT)


def sentiment_model() -> str:
    return env_str("GROK_SENTIMENT_MODEL", DEFAULT_SENTIMENT_MODEL)


def sentiment_batch_size() -> int:
    return env_positive_int("SENTIMENT_BATCH_SIZE", DEFAULT_SENTIMENT_BATCH_SIZE)


def sentiment_nightly_limit() -> int | None:
    return env_optional_positive_int("SENTIMENT_NIGHTLY_LIMIT")


def minecraft_channel_id() -> int:
    return env_positive_int("MINECRAFT_CHANNEL_ID", DEFAULT_MINECRAFT_CHANNEL_ID)


def minecraft_model() -> str:
    return env_str("GROK_MINECRAFT_MODEL", DEFAULT_MINECRAFT_MODEL)


def minecraft_data_dir() -> Path:
    return Path(env_str("MINECRAFT_DATA_DIR", DEFAULT_MINECRAFT_DATA_DIR))


def minecraft_screenshot_dir() -> Path:
    return Path(env_str("MC_RECAP_SCREENSHOT_DIR", DEFAULT_MINECRAFT_SCREENSHOT_DIR))


def enable_console_post_loop() -> bool:
    return os.getenv("ENABLE_CONSOLE_POST", "").strip().lower() in {"1", "true", "yes"}
