"""Reusable Discord UI components."""

import discord
from utils.constants import DINKSCORD_URL


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
