"""Daily Minecraft recap: collector reads, analytics, Grok narrative, screenshots."""

from minecraft.analytics import summarize_day
from minecraft.camera import (
    camera_overlap_seconds,
    exclude_camera_sessions,
    load_camera_sessions,
)
from minecraft.day import (
    activity_date_due,
    day_window,
    playday_date,
    pretty_date,
    yesterday_et,
)
from minecraft.models import (
    CameraSession,
    DayRecap,
    Death,
    MinecraftDataError,
    PeakStretch,
    PlayerSummary,
    PreparedPost,
    Session,
    SessionSlice,
    format_clock,
    format_duration,
)
from minecraft.narrative import (
    SYSTEM_PROMPT,
    ask_grok,
    clean_model_text,
    format_fallback,
    generate_narrative,
    is_valid_narrative,
    minecraft_grok_enabled,
    nobody_line,
)
from minecraft.recap import build_day_recap, prepare_post, summary_title
from minecraft.screenshots import pick_recap_screenshot

# Back-compat alias used by older call sites / docs.
MinecraftLogError = MinecraftDataError

__all__ = [
    "SYSTEM_PROMPT",
    "CameraSession",
    "DayRecap",
    "Death",
    "MinecraftDataError",
    "MinecraftLogError",
    "PeakStretch",
    "PlayerSummary",
    "PreparedPost",
    "Session",
    "SessionSlice",
    "activity_date_due",
    "ask_grok",
    "build_day_recap",
    "camera_overlap_seconds",
    "clean_model_text",
    "day_window",
    "exclude_camera_sessions",
    "format_clock",
    "format_duration",
    "format_fallback",
    "generate_narrative",
    "is_valid_narrative",
    "load_camera_sessions",
    "minecraft_grok_enabled",
    "nobody_line",
    "pick_recap_screenshot",
    "playday_date",
    "prepare_post",
    "pretty_date",
    "summarize_day",
    "summary_title",
    "yesterday_et",
]
