"""Cross-cutting Discord UI and interaction helpers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Callable

import discord
from discord.ext import commands

from config import DINKSCORD_URL


def hybrid_command(*args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Any]:
    """Typed passthrough to ``commands.hybrid_command`` (avoids mypy ParamSpec bug)."""
    return commands.hybrid_command(*args, **kwargs)


@asynccontextmanager
async def acknowledge(ctx, *, ephemeral: bool = False):
    """Defer slash interactions; show typing for prefix invocations."""
    if ctx.interaction is not None:
        await ctx.defer(ephemeral=ephemeral)
        yield
    else:
        async with ctx.typing():
            yield


async def reply_ephemeral(ctx, message: str) -> None:
    """Reply ephemerally when invoked as an unacknowledged slash interaction."""
    if ctx.interaction is not None and not ctx.interaction.response.is_done():
        await ctx.interaction.response.send_message(message, ephemeral=True)
        return
    await ctx.send(message)


def dinkscord_link_view(label: str = "Visit dinkscord.com") -> discord.ui.View:
    """Return a view with a single link button to the public dashboard."""
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.link,
            url=DINKSCORD_URL,
        )
    )
    return view


def resolve_display_name(bot, user_id: int | str) -> str:
    """Resolve a cached Discord display name, falling back safely if uncached."""
    if bot is not None:
        try:
            user = bot.get_user(int(user_id))
        except (TypeError, ValueError):
            user = None
        if user is not None:
            return getattr(user, "display_name", None) or str(user)
    return f"User {user_id}"
