"""Helpers for hybrid (prefix + slash) command acknowledgements."""

from contextlib import asynccontextmanager


@asynccontextmanager
async def acknowledge(ctx):
    """Defer slash interactions; show typing for prefix invocations."""
    if ctx.interaction is not None:
        await ctx.defer()
        yield
    else:
        async with ctx.typing():
            yield
