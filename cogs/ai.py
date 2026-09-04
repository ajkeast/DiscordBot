from discord.ext import commands, tasks
from discord import app_commands
from chatgpt_functions import GrokClient, call_grok_imagine, GROK_IMAGINE_FILENAME
from utils.constants import (
    EMBED_COLOR,
    IMAGINE_RATE_LIMIT,
    IMAGINE_RATE_PERIOD_SECONDS,
    MAX_GROK_SESSION_TURNS,
    MAX_IMAGINE_INPUT_IMAGES,
)
from utils.db import db_ops
from utils.interactions import acknowledge
from datetime import datetime, timedelta
from typing import List, Optional
import asyncio
import math
import discord
import pytz
import requests
import os
import io
import logging

logger = logging.getLogger(__name__)

EASTERN = pytz.timezone("US/Eastern")
DAILY_CLEAR_HOUR = 3  # 3am US/Eastern


def _is_image_attachment(attachment) -> bool:
    content_type = getattr(attachment, "content_type", None) or ""
    if content_type.startswith("image/"):
        return True
    filename = (getattr(attachment, "filename", None) or "").lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


def _image_urls_from_message(ctx) -> List[str]:
    return [a.url for a in ctx.message.attachments if _is_image_attachment(a)]


def _collect_image_urls(ctx, *attachments: Optional[discord.Attachment]) -> List[str]:
    """Prefer explicit slash attachment options; fall back to message attachments."""
    urls = [
        a.url
        for a in attachments
        if a is not None and _is_image_attachment(a)
    ]
    if urls:
        return urls
    return _image_urls_from_message(ctx)


def _first_image_url_from_discord_message(message) -> Optional[str]:
    if message is None:
        return None
    for attachment in getattr(message, "attachments", None) or []:
        if _is_image_attachment(attachment):
            return attachment.url
    for embed in getattr(message, "embeds", None) or []:
        image = getattr(embed, "image", None)
        url = getattr(image, "url", None) if image is not None else None
        if url:
            return url
    return None


def _image_url_from_reply(ctx) -> Optional[str]:
    message = getattr(ctx, "message", None)
    if message is None:
        return None
    ref = getattr(message, "reference", None)
    if ref is None:
        return None
    resolved = getattr(ref, "resolved", None)
    if not isinstance(resolved, discord.Message):
        return None
    return _first_image_url_from_discord_message(resolved)


def _collect_imagine_source(
    ctx, image: Optional[discord.Attachment]
) -> tuple[Optional[str], Optional[str]]:
    """Return (image_url, error_message) for a single /imagine source image."""
    if image is not None:
        if not _is_image_attachment(image):
            return None, "That attachment isn't an image."
        return image.url, None

    message_urls = _image_urls_from_message(ctx)
    if len(message_urls) > MAX_IMAGINE_INPUT_IMAGES:
        noun = "image" if MAX_IMAGINE_INPUT_IMAGES == 1 else "images"
        return (
            None,
            f"You can attach at most {MAX_IMAGINE_INPUT_IMAGES} {noun} for `/imagine`.",
        )
    if message_urls:
        return message_urls[0], None
    return _image_url_from_reply(ctx), None


def _embed_prompt(prompt: str) -> str:
    text = (prompt or "").strip() or "…"
    if len(text) > 1024:
        return text[:1021] + "..."
    return text


class _CooldownMessage:
    __slots__ = ("author",)

    def __init__(self, author):
        self.author = author


def _imagine_cooldown_message(retry_after: float) -> str:
    minutes = max(1, math.ceil(retry_after / 60))
    return (
        f"You've hit the `/imagine` limit ({IMAGINE_RATE_LIMIT} per hour). "
        f"Try again in about {minutes} minute{'s' if minutes != 1 else ''}."
    )


async def _consume_imagine_cooldown(interaction: discord.Interaction) -> bool:
    """Take one /imagine token for this user. Reply ephemerally if they are limited."""
    cog = interaction.client.get_cog("AI")
    command = getattr(cog, "imagine", None) if cog is not None else None
    buckets = getattr(command, "_buckets", None)
    if buckets is None:
        return True
    retry_after = buckets.update_rate_limit(_CooldownMessage(interaction.user))
    if not retry_after:
        return True
    await interaction.response.send_message(
        _imagine_cooldown_message(retry_after),
        ephemeral=True,
    )
    return False


def _prompt_from_imagine_message(message) -> Optional[str]:
    for embed in getattr(message, "embeds", None) or []:
        for field in getattr(embed, "fields", None) or []:
            if getattr(field, "name", None) == "Prompt":
                value = (getattr(field, "value", None) or "").strip()
                if value:
                    return value
    return None


async def _send_imagine_result(*, send, prompt: str, image_url: Optional[str], author) -> None:
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None,
        lambda: call_grok_imagine(prompt, input_image_url=image_url),
    )
    if response["status"] != "success":
        logger.error("/imagine failed: %s", response.get("error"))
        await send(
            embed=discord.Embed(
                title="❌ Error",
                description="Failed to generate image. Check the bot logs and try again.",
                color=EMBED_COLOR,
            )
        )
        return

    image_file = discord.File(
        io.BytesIO(response["image_bytes"]),
        filename=GROK_IMAGINE_FILENAME,
    )
    embed = discord.Embed(color=EMBED_COLOR)
    embed.set_image(url=f"attachment://{GROK_IMAGINE_FILENAME}")
    embed.add_field(name="Prompt", value=_embed_prompt(prompt), inline=False)
    embed.set_footer(text=f"Requested by {author.display_name}")
    await send(embed=embed, file=image_file, view=ImagineResultView())


class ImagineEditModal(discord.ui.Modal, title="Edit image"):
    prompt_input = discord.ui.TextInput(
        label="What should we change?",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    def __init__(self, image_url: str):
        super().__init__()
        self.image_url = image_url

    async def on_submit(self, interaction: discord.Interaction):
        prompt = str(self.prompt_input.value).strip()
        if not prompt:
            await interaction.response.send_message(
                "Please describe what to change.",
                ephemeral=True,
            )
            return
        if not await _consume_imagine_cooldown(interaction):
            return
        await interaction.response.defer()
        await _send_imagine_result(
            send=interaction.followup.send,
            prompt=prompt,
            image_url=self.image_url,
            author=interaction.user,
        )


class ImagineResultView(discord.ui.View):
    """Persistent Edit and Retry buttons on /imagine results."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Edit",
        style=discord.ButtonStyle.primary,
        custom_id="imagine:edit",
    )
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        image_url = _first_image_url_from_discord_message(interaction.message)
        if not image_url:
            await interaction.response.send_message(
                "Couldn't find an image on that message to edit.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(ImagineEditModal(image_url=image_url))

    @discord.ui.button(
        label="Retry",
        style=discord.ButtonStyle.secondary,
        custom_id="imagine:retry",
    )
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        prompt = _prompt_from_imagine_message(interaction.message)
        if not prompt:
            await interaction.response.send_message(
                "Couldn't find the original prompt to retry.",
                ephemeral=True,
            )
            return
        if not await _consume_imagine_cooldown(interaction):
            return
        await interaction.response.defer()
        await _send_imagine_result(
            send=interaction.followup.send,
            prompt=prompt,
            image_url=None,
            author=interaction.user,
        )


async def _ensure_message_row(ctx, content: str = "") -> None:
    """Insert a messages row for slash invocations (they skip on_message).

    AI logging FKs chatgpt_logs / dalle_3_prompts.message_id → messages.id.
    Prefix commands are already stored in on_message before process_commands.
    """
    if ctx.interaction is None:
        return
    message_data = (
        ctx.message.id,
        ctx.author.id,
        ctx.channel.id,
        content or (getattr(ctx.message, "content", None) or ""),
        ctx.message.created_at,
    )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, db_ops.update_messages, message_data)


def _format_slash_ask_message(author, prompt: str, response: str) -> str:
    """Format slash /ask and /voice as '@user: prompt\\n\\nresponse' (Discord 2000-char limit)."""
    header = f"{author.mention}: "
    sep = "\n\n"
    prompt_body = (prompt or "").strip() or "…"
    answer = (response or "").strip() or "…"
    available = 2000 - len(header) - len(sep)
    if len(prompt_body) + len(answer) > available:
        # Prefer keeping the answer; trim the prompt first, then the answer.
        max_prompt = min(len(prompt_body), max(32, available // 3))
        if len(prompt_body) > max_prompt:
            prompt_body = prompt_body[: max_prompt - 3] + "..."
        max_answer = available - len(prompt_body)
        if len(answer) > max_answer:
            answer = answer[: max_answer - 3] + "..."
    return f"{header}{prompt_body}{sep}{answer}"


async def _send_ask_answer(ctx, prompt: str, content: str, **kwargs):
    """Send /ask or /voice answer; slash includes '@user: prompt' in one message."""
    if ctx.interaction is not None:
        await ctx.send(_format_slash_ask_message(ctx.author, prompt, content), **kwargs)
    else:
        await ctx.send(content, **kwargs)


class AI(commands.Cog):
    """Chat, image generation, and voice."""

    def __init__(self, bot):
        self.bot = bot
        self.grok = GrokClient(bot=bot)
        # Single shared session for all users (last Grok response_id, or None for new conversation)
        self.last_response_id = None
        self._session_turns = 0  # turns in current session; reset when starting fresh or hitting limit
        self.system_prompt = (
            "You are Peter Dinklage, the resident bot of this Discord server (Dinkscord). "
            "You are not just a chat assistant: you run the daily /1st game, the DinkCoin (DINK) economy, "
            "image generation, and server stats. Commands are slash commands (also available with the '_' prefix). "
            "You speak to server members as yourself — never as a separate AI product. "
            "Never mention API providers, model names, databases, table names, code files, "
            "environment variables, or other internal implementation details. "
            "You think from first principles and reason step by step when applicable. "
            "You have tools to look up your own documentation (get_bot_documentation), your live command list "
            "(list_bot_commands), and live data (get_first_game_stats, get_juice_stats, get_dink_ledger). "
            "Whenever someone asks about you, your commands, your capabilities, the first game, juice, streaks, "
            "DINK, or how any of your features work, call those tools and answer from their output instead of guessing. "
            "You also have real-time web and X search; use them to confirm facts and fetch primary sources for current events. "
            "In your final answer, write economically. A single sentence should often be enough. "
            "Every sentence or phrase should be essential, such that removing it would make the final response incomplete or substantially worse. "
            "Do not use markdown bold (**text**) for whole responses or multiple sentences—especially after web search. "
        )

    async def cog_load(self):
        self.bot.add_view(ImagineResultView())
        self.daily_chat_clear.start()

    def cog_unload(self):
        self.daily_chat_clear.cancel()
        self.bot.remove_view(ImagineResultView())

    def _reset_session(self):
        self.last_response_id = None
        self._session_turns = 0

    def _build_system_prompt(self) -> str:
        """Base prompt plus today's US/Eastern date (no clock time)."""
        today = datetime.now(EASTERN).strftime("%A, %B %d, %Y")
        return (
            f"{self.system_prompt}"
            f"Today's date is {today} (US/Eastern). "
            "Use this for any date-sensitive answers."
        )

    def _clear_session_if_new_day(self) -> bool:
        """Clear the shared chat at the configured daily hour (US/Eastern). Returns True if cleared."""
        now = datetime.now(EASTERN)
        if now.hour == DAILY_CLEAR_HOUR:
            self._reset_session()
            logger.info(
                "Auto-cleared shared Grok chat at %sam US/Eastern",
                DAILY_CLEAR_HOUR,
            )
            return True
        return False

    @tasks.loop(hours=1)
    async def daily_chat_clear(self):
        """Clear the shared chat daily so the next session gets a fresh Eastern date."""
        self._clear_session_if_new_day()

    @daily_chat_clear.before_loop
    async def before_daily_chat_clear(self):
        await self.bot.wait_until_ready()
        now = datetime.now(EASTERN)
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        await asyncio.sleep((next_hour - now).total_seconds())

    @commands.hybrid_command(brief="Ask a question")
    @app_commands.describe(
        prompt="Your question or message",
        image="Optional image to include with your question",
    )
    async def ask(
        self,
        ctx,
        *,
        prompt: str,
        image: Optional[discord.Attachment] = None,
    ):
        """Ask a question. You can also attach images."""

        image_urls = _collect_image_urls(ctx, image)

        async with acknowledge(ctx):
            # Start a new session if we've hit the turn limit (keeps context/cost bounded)
            if self._session_turns >= MAX_GROK_SESSION_TURNS:
                self._reset_session()

            try:
                await _ensure_message_row(ctx, content=prompt)
                next_response_id, response_text = self.grok.send_message(
                    prompt,
                    previous_response_id=self.last_response_id,
                    system_prompt=self._build_system_prompt(),
                    user_id=ctx.author.id,
                    message_id=ctx.message.id,
                    image_urls=image_urls if image_urls else None,
                )
            except Exception:
                logger.exception("/ask failed for user %s", ctx.author.id)
                await _send_ask_answer(
                    ctx,
                    prompt,
                    "Something broke on my end, dude. Check the bot logs and try again.",
                )
                return

            if next_response_id is not None:
                self.last_response_id = next_response_id
                self._session_turns += 1

            await _send_ask_answer(
                ctx,
                prompt,
                response_text or "Sorry, I couldn't generate a reply this time. Please try again.",
            )

    @commands.hybrid_command(brief="Generate an AI image")
    @commands.cooldown(IMAGINE_RATE_LIMIT, IMAGINE_RATE_PERIOD_SECONDS, commands.BucketType.user)
    @app_commands.describe(
        prompt="Describe the image to generate or edit",
        image="Optional image to edit",
    )
    async def imagine(
        self,
        ctx,
        *,
        prompt: str,
        image: Optional[discord.Attachment] = None,
    ):
        """Generate an AI image, or edit one attached image / a replied-to image."""

        image_url, error = _collect_imagine_source(ctx, image)
        if error:
            await ctx.send(error)
            return

        async with acknowledge(ctx):
            await _ensure_message_row(ctx, content=prompt)
            db_ops.write_dalle_entry(user_id=ctx.author.id, prompt=prompt, message_id=ctx.message.id)
            await _send_imagine_result(
                send=ctx.send,
                prompt=prompt,
                image_url=image_url,
                author=ctx.author,
            )

    @commands.hybrid_command(brief="Clear the shared chat")
    async def clear(self, ctx):
        """Clear the shared chat so the next /ask starts fresh."""

        self._reset_session()
        await ctx.send("Chat history cleared! Starting fresh, dude! 🤙")

    @commands.hybrid_command(brief="Answer a prompt out loud")
    @app_commands.describe(prompt="The prompt to answer and speak aloud")
    async def voice(self, ctx, *, prompt: str):
        """Answer a prompt and read the reply aloud."""

        if not prompt:
            await ctx.send("Please provide text to convert to speech.")
            return

        if len(prompt) > 15000:
            await ctx.send("Text is too long. Maximum 15,000 characters.")
            return

        api_key = os.getenv('XAI_API_KEY')
        if not api_key:
            await ctx.send("XAI API key not configured.")
            return

        async with acknowledge(ctx):
            try:
                if self._session_turns >= MAX_GROK_SESSION_TURNS:
                    self._reset_session()

                await _ensure_message_row(ctx, content=prompt)
                next_response_id, response_text = self.grok.send_message(
                    prompt,
                    previous_response_id=self.last_response_id,
                    system_prompt=self._build_system_prompt(),
                    user_id=ctx.author.id,
                    message_id=ctx.message.id,
                )

                if next_response_id is not None:
                    self.last_response_id = next_response_id
                    self._session_turns += 1

                if not response_text:
                    await _send_ask_answer(
                        ctx,
                        prompt,
                        "Sorry, I couldn't generate a spoken response. Please try again.",
                    )
                    return

                tts_response = requests.post(
                    "https://api.x.ai/v1/tts",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": response_text,
                        "voice_id": "leo",
                        "language": "en",
                    },
                )
                tts_response.raise_for_status()

                audio_file = discord.File(
                    io.BytesIO(tts_response.content),
                    filename="voice.mp3"
                )

                # Slash: one message like /ask ('@user: prompt\\n\\nanswer' + audio).
                # Prefix: audio only (prompt is already visible in the invoking message).
                if ctx.interaction is not None:
                    await _send_ask_answer(ctx, prompt, response_text, file=audio_file)
                else:
                    await ctx.send(file=audio_file)

            except requests.exceptions.RequestException:
                logger.exception("/voice TTS request failed for user %s", ctx.author.id)
                await _send_ask_answer(
                    ctx,
                    prompt,
                    "Something broke generating speech. Check the bot logs and try again.",
                )
            except Exception:
                logger.exception("/voice failed for user %s", ctx.author.id)
                await _send_ask_answer(
                    ctx,
                    prompt,
                    "Something broke on my end, dude. Check the bot logs and try again.",
                )


async def setup(bot):
    await bot.add_cog(AI(bot))
