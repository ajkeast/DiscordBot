"""UI components, attachment extractors, and shared cooldown for /imagine."""

from __future__ import annotations

import asyncio
import io
import logging
import math
from types import SimpleNamespace
from typing import Optional

import discord
from discord.ext import commands

from config import (
    EMBED_COLOR,
    GROK_IMAGINE_FILENAME,
    IMAGINE_RATE_LIMIT,
    IMAGINE_RATE_PERIOD_SECONDS,
    MAX_IMAGINE_INPUT_IMAGES,
)
from services.xai import generate_image

logger = logging.getLogger(__name__)


def is_image_attachment(attachment) -> bool:
    content_type = getattr(attachment, "content_type", None) or ""
    if content_type.startswith("image/"):
        return True
    filename = (getattr(attachment, "filename", None) or "").lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


def image_urls_from_message(ctx) -> list[str]:
    return [a.url for a in ctx.message.attachments if is_image_attachment(a)]


def collect_image_urls(ctx, *attachments: Optional[discord.Attachment]) -> list[str]:
    """Prefer explicit slash attachment options; fall back to message attachments."""
    urls = [a.url for a in attachments if a is not None and is_image_attachment(a)]
    return urls or image_urls_from_message(ctx)


def first_image_url_from_message(message) -> str | None:
    if message is None:
        return None
    for attachment in getattr(message, "attachments", None) or []:
        if is_image_attachment(attachment):
            return attachment.url
    for embed in getattr(message, "embeds", None) or []:
        img = getattr(embed, "image", None)
        url = getattr(img, "url", None) if img is not None else None
        if url:
            return url
    return None


def image_url_from_reply(ctx) -> str | None:
    message = getattr(ctx, "message", None)
    if message is None:
        return None
    ref = getattr(message, "reference", None)
    if ref is None:
        return None
    resolved = getattr(ref, "resolved", None)
    if not isinstance(resolved, discord.Message):
        return None
    return first_image_url_from_message(resolved)


def collect_imagine_source(
    ctx, image: Optional[discord.Attachment]
) -> tuple[str | None, str | None]:
    """Return ``(image_url, error_message)`` for a single /imagine source image."""
    if image is not None:
        if not is_image_attachment(image):
            return None, "That attachment isn't an image."
        return image.url, None

    message_urls = image_urls_from_message(ctx)
    if len(message_urls) > MAX_IMAGINE_INPUT_IMAGES:
        noun = "image" if MAX_IMAGINE_INPUT_IMAGES == 1 else "images"
        return (
            None,
            f"You can attach at most {MAX_IMAGINE_INPUT_IMAGES} {noun} for `/imagine`.",
        )
    if message_urls:
        return message_urls[0], None
    return image_url_from_reply(ctx), None


class ImagineCooldown:
    """Shared per-user cooldown used by `/imagine`, Edit modal, and Retry button."""

    def __init__(
        self,
        rate: int = IMAGINE_RATE_LIMIT,
        per: float = IMAGINE_RATE_PERIOD_SECONDS,
    ) -> None:
        self.rate = rate
        self.per = per
        self._mapping = commands.CooldownMapping.from_cooldown(
            rate, per, commands.BucketType.user
        )

    def check_and_consume(self, user) -> float | None:
        """Consume one token for ``user``; return retry-after seconds if limited."""
        bucket = self._mapping.get_bucket(SimpleNamespace(author=user))  # type: ignore[arg-type]
        if bucket is None:
            return None
        return bucket.update_rate_limit()

    def format_limit_message(self, retry_after: float) -> str:
        minutes = max(1, math.ceil(retry_after / 60))
        return (
            f"You've hit the `/imagine` limit ({self.rate} per hour). "
            f"Try again in about {minutes} minute{'s' if minutes != 1 else ''}."
        )


def _embed_prompt(prompt: str) -> str:
    text = (prompt or "").strip() or "…"
    return text[:1021] + "..." if len(text) > 1024 else text


def _imagining_text(source) -> str:
    name = None
    me = getattr(source, "me", None)
    if me is not None:
        name = getattr(me, "display_name", None)
    if not isinstance(name, str) or not name:
        bot = getattr(source, "bot", None) or getattr(source, "client", None)
        user = getattr(bot, "user", None) if bot is not None else None
        name = getattr(user, "display_name", None)
    if not isinstance(name, str) or not name:
        name = "Peter Dinklage"
    return f"{name} is imagining…"


def _prompt_from_imagine_message(message) -> str | None:
    for embed in getattr(message, "embeds", None) or []:
        for field in getattr(embed, "fields", None) or []:
            if getattr(field, "name", None) == "Prompt":
                value = (getattr(field, "value", None) or "").strip()
                if value:
                    return value
    return None


def _source_image_url_from_imagine_message(message) -> str | None:
    for embed in getattr(message, "embeds", None) or []:
        url = getattr(embed, "url", None)
        if isinstance(url, str) and url:
            return url
    return None


def _imagine_result_embed(
    prompt: str, author, source_image_url: str | None
) -> discord.Embed:
    embed = discord.Embed(color=EMBED_COLOR, url=source_image_url or None)
    embed.set_image(url=f"attachment://{GROK_IMAGINE_FILENAME}")
    embed.add_field(name="Prompt", value=_embed_prompt(prompt), inline=False)
    embed.set_footer(text=f"Requested by {author.display_name}")
    return embed


def _imagine_error_embed() -> discord.Embed:
    return discord.Embed(
        title="❌ Error",
        description="Failed to generate image. Check the bot logs and try again.",
        color=EMBED_COLOR,
    )


def _editable_message(sent):
    if hasattr(sent, "edit") and callable(getattr(sent, "edit", None)):
        return sent
    resource = getattr(sent, "resource", None)
    if hasattr(resource, "edit") and callable(getattr(resource, "edit", None)):
        return resource
    return None


async def start_imagine(
    source,
    send,
    prompt: str,
    image_url: str | None,
    author,
    *,
    cooldown: ImagineCooldown | None = None,
) -> None:
    sent = await send(_imagining_text(source))
    message = _editable_message(sent)
    if message is None:
        message = await source.original_response()
    try:
        response = await asyncio.to_thread(
            generate_image, prompt, input_image_url=image_url
        )
        if response["status"] != "success":
            logger.error("/imagine failed: %s", response.get("error"))
            await message.edit(content=None, embed=_imagine_error_embed(), view=None)
            return

        image_file = discord.File(
            io.BytesIO(response["image_bytes"]),
            filename=GROK_IMAGINE_FILENAME,
        )
        await message.edit(
            content=None,
            embed=_imagine_result_embed(prompt, author, image_url),
            attachments=[image_file],
            view=ImagineResultView(cooldown=cooldown),
        )
    except Exception:
        logger.exception("Failed to finish /imagine result")
        try:
            await message.edit(content=None, embed=_imagine_error_embed(), view=None)
        except Exception:
            logger.exception("Failed to update imagining placeholder")


async def _consume_interaction_cooldown(
    interaction: discord.Interaction, cooldown: ImagineCooldown | None
) -> bool:
    if cooldown is None:
        get_cog = getattr(interaction.client, "get_cog", None)
        cog = get_cog("AI") if callable(get_cog) else None
        cooldown = getattr(cog, "imagine_cooldown", None) if cog is not None else None
    if cooldown is None:
        return True
    retry_after = cooldown.check_and_consume(interaction.user)
    if not retry_after:
        return True
    await interaction.response.send_message(
        cooldown.format_limit_message(retry_after),
        ephemeral=True,
    )
    return False


class ImagineEditModal(discord.ui.Modal, title="Edit image"):
    prompt_input: discord.ui.TextInput = discord.ui.TextInput(
        label="What should we change?",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    def __init__(
        self, image_url: str, *, cooldown: ImagineCooldown | None = None
    ) -> None:
        super().__init__()
        self.image_url = image_url
        self._cooldown = cooldown

    async def on_submit(self, interaction: discord.Interaction) -> None:
        prompt = str(self.prompt_input.value).strip()
        if not prompt:
            await interaction.response.send_message(
                "Please describe what to change.",
                ephemeral=True,
            )
            return
        if not await _consume_interaction_cooldown(interaction, self._cooldown):
            return
        await start_imagine(
            interaction,
            interaction.response.send_message,
            prompt,
            self.image_url,
            interaction.user,
            cooldown=self._cooldown,
        )


class ImagineResultView(discord.ui.View):
    """Persistent Edit and Retry buttons on /imagine results."""

    def __init__(self, *, cooldown: ImagineCooldown | None = None) -> None:
        super().__init__(timeout=None)
        self._cooldown = cooldown

    @discord.ui.button(
        label="Edit",
        style=discord.ButtonStyle.primary,
        custom_id="imagine:edit",
    )
    async def edit(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        image_url = first_image_url_from_message(interaction.message)
        if not image_url:
            await interaction.response.send_message(
                "Couldn't find an image on that message to edit.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            ImagineEditModal(image_url=image_url, cooldown=self._cooldown)
        )

    @discord.ui.button(
        label="Retry",
        style=discord.ButtonStyle.secondary,
        custom_id="imagine:retry",
    )
    async def retry(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        prompt = _prompt_from_imagine_message(interaction.message)
        if not prompt:
            await interaction.response.send_message(
                "Couldn't find the original prompt to retry.",
                ephemeral=True,
            )
            return
        if not await _consume_interaction_cooldown(interaction, self._cooldown):
            return
        source_url = _source_image_url_from_imagine_message(interaction.message)
        await start_imagine(
            interaction,
            interaction.response.send_message,
            prompt,
            source_url,
            interaction.user,
            cooldown=self._cooldown,
        )
