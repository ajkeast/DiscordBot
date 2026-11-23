"""Shared Discord authorization checks."""

from __future__ import annotations

import asyncio

from utils.discord_helpers import reply_ephemeral


def actor_is_admin_result(is_owner: bool, user, guild) -> bool:
    """True for the bot owner or a guild administrator."""
    if is_owner:
        return True
    if guild is None:
        return False
    perms = getattr(user, "guild_permissions", None)
    return bool(perms and perms.administrator is True)


async def is_bot_owner_or_admin(bot, user, guild) -> bool:
    """Check whether ``user`` is the bot owner or an admin in ``guild``."""
    is_owner = False
    checker = getattr(bot, "is_owner", None)
    if checker is not None:
        result = checker(user)
        if asyncio.iscoroutine(result):
            result = await result
        is_owner = result is True
    return actor_is_admin_result(is_owner, user, guild)


async def require_admin(
    bot,
    ctx,
    message: str = "Only server admins can run this command.",
) -> bool:
    """Return True if authorized; otherwise notify the caller and return False."""
    if await is_bot_owner_or_admin(bot, ctx.author, ctx.guild):
        return True
    await reply_ephemeral(ctx, message)
    return False
