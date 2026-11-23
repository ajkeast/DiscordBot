"""AI chat, image generation, and text-to-speech commands."""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, time
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.ai_imagine_ui import (
    ImagineCooldown,
    ImagineResultView,
    collect_image_urls,
    collect_imagine_source,
    start_imagine,
)
from config import EASTERN, MAX_GROK_SESSION_TURNS, xai_api_key
from db.ai_logs import AILogsRepository
from db.messages import MessagesRepository
from services.xai import GrokChatService, synthesize_speech
from utils.discord_helpers import acknowledge, hybrid_command

logger = logging.getLogger(__name__)

DAILY_CLEAR_AT = time(hour=3, minute=0, tzinfo=EASTERN)

BASE_SYSTEM_PROMPT = (
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


def _format_slash_ask_message(author, prompt: str, response: str) -> str:
    """Format slash /ask and /voice as '@user: prompt\\n\\nresponse' (2000-char limit)."""
    header = f"{author.mention}: "
    sep = "\n\n"
    prompt_body = (prompt or "").strip() or "…"
    answer = (response or "").strip() or "…"
    available = 2000 - len(header) - len(sep)
    if len(prompt_body) + len(answer) > available:
        max_prompt = min(len(prompt_body), max(32, available // 3))
        if len(prompt_body) > max_prompt:
            prompt_body = prompt_body[: max_prompt - 3] + "..."
        max_answer = available - len(prompt_body)
        if len(answer) > max_answer:
            answer = answer[: max_answer - 3] + "..."
    return f"{header}{prompt_body}{sep}{answer}"


async def _send_ask_answer(ctx, prompt: str, content: str, **kwargs) -> None:
    if ctx.interaction is not None:
        await ctx.send(_format_slash_ask_message(ctx.author, prompt, content), **kwargs)
    else:
        await ctx.send(content, **kwargs)


class AI(commands.Cog):
    """Chat, image generation, and voice."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        grok: GrokChatService | None = None,
        ai_logs_repo: AILogsRepository | None = None,
        messages_repo: MessagesRepository | None = None,
    ) -> None:
        self.bot = bot
        self.ai_logs_repo = ai_logs_repo or AILogsRepository()
        self.messages_repo = messages_repo or getattr(
            bot, "messages_repo", None
        ) or MessagesRepository()
        self.grok = grok or GrokChatService(bot=bot, logs_repo=self.ai_logs_repo)
        self.system_prompt = BASE_SYSTEM_PROMPT

        # Shared server-wide session guarded by an asyncio lock.
        self._session_lock = asyncio.Lock()
        self.last_response_id: str | None = None
        self._session_turns = 0

        self.imagine_cooldown = ImagineCooldown()
        self._imagine_view = ImagineResultView(cooldown=self.imagine_cooldown)

    async def cog_load(self) -> None:
        self.bot.add_view(self._imagine_view)
        self.daily_chat_clear.start()

    async def cog_unload(self) -> None:
        if self.daily_chat_clear.is_running():
            self.daily_chat_clear.cancel()
        self._imagine_view.stop()

    def _reset_session(self) -> None:
        self.last_response_id = None
        self._session_turns = 0

    def _build_system_prompt(self) -> str:
        today = datetime.now(EASTERN).strftime("%A, %B %d, %Y")
        return (
            f"{self.system_prompt}"
            f"Today's date is {today} (US/Eastern). "
            "Use this for any date-sensitive answers."
        )

    async def _ensure_message_row(self, ctx, content: str = "") -> None:
        """Insert a messages row for slash invocations (they skip on_message)."""
        if ctx.interaction is None:
            return
        await asyncio.to_thread(
            self.messages_repo.insert_message,
            ctx.message.id,
            ctx.author.id,
            ctx.channel.id,
            content or (getattr(ctx.message, "content", None) or ""),
            ctx.message.created_at,
        )

    async def _run_grok_turn(
        self,
        prompt: str,
        *,
        user_id: int,
        message_id: int,
        image_urls: list[str] | None = None,
    ) -> str:
        """Execute one Grok turn off the event loop with serialized session state."""
        async with self._session_lock:
            if self._session_turns >= MAX_GROK_SESSION_TURNS:
                self._reset_session()
            prev_id = self.last_response_id
            sys_prompt = self._build_system_prompt()
            next_id, response_text = await asyncio.to_thread(
                self.grok.send_message,
                prompt,
                previous_response_id=prev_id,
                system_prompt=sys_prompt,
                user_id=user_id,
                message_id=message_id,
                image_urls=image_urls,
            )
            if next_id is not None:
                self.last_response_id = next_id
                self._session_turns += 1
            return response_text

    @tasks.loop(time=DAILY_CLEAR_AT)
    async def daily_chat_clear(self) -> None:
        """Reset shared chat daily at 3:00 AM Eastern so sessions get a fresh date."""
        async with self._session_lock:
            self._reset_session()
        logger.info("Auto-cleared shared Grok chat at 3:00 AM Eastern")

    @daily_chat_clear.before_loop
    async def before_daily_chat_clear(self) -> None:
        await self.bot.wait_until_ready()

    @hybrid_command(brief="Ask a question")
    @app_commands.describe(
        prompt="Your question or message",
        image="Optional image to include with your question",
    )
    async def ask(
        self,
        ctx: commands.Context,
        *,
        prompt: str,
        image: Optional[discord.Attachment] = None,
    ) -> None:
        """Ask a question. You can also attach images."""
        image_urls = collect_image_urls(ctx, image)

        async with acknowledge(ctx):
            try:
                await self._ensure_message_row(ctx, content=prompt)
                response_text = await self._run_grok_turn(
                    prompt,
                    user_id=ctx.author.id,
                    message_id=ctx.message.id,
                    image_urls=image_urls or None,
                )
            except Exception:
                logger.exception("/ask failed for user %s", ctx.author.id)
                await _send_ask_answer(
                    ctx,
                    prompt,
                    "Something broke on my end, dude. Check the bot logs and try again.",
                )
                return

            await _send_ask_answer(
                ctx,
                prompt,
                response_text
                or "Sorry, I couldn't generate a reply this time. Please try again.",
            )

    @hybrid_command(brief="Generate an AI image")
    @app_commands.describe(
        prompt="Describe the image to generate or edit",
        image="Optional image to edit",
    )
    async def imagine(
        self,
        ctx: commands.Context,
        *,
        prompt: str,
        image: Optional[discord.Attachment] = None,
    ) -> None:
        """Generate an AI image, or edit one attached image / a replied-to image."""
        retry_after = self.imagine_cooldown.check_and_consume(ctx.author)
        if retry_after:
            msg = self.imagine_cooldown.format_limit_message(retry_after)
            if ctx.interaction is not None and not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(msg, ephemeral=True)
            else:
                await ctx.send(msg)
            return

        image_url, error = collect_imagine_source(ctx, image)
        if error:
            await ctx.send(error)
            return

        await self._ensure_message_row(ctx, content=prompt)
        await asyncio.to_thread(
            self.ai_logs_repo.log_image_prompt,
            user_id=ctx.author.id,
            prompt=prompt,
            message_id=ctx.message.id,
        )
        await start_imagine(
            ctx,
            ctx.send,
            prompt,
            image_url,
            ctx.author,
            cooldown=self.imagine_cooldown,
        )

    @hybrid_command(brief="Clear the shared chat")
    async def clear(self, ctx: commands.Context) -> None:
        """Clear the shared chat so the next /ask starts fresh."""
        async with self._session_lock:
            self._reset_session()
        await ctx.send("Chat history cleared! Starting fresh, dude! 🤙")

    @hybrid_command(brief="Answer a prompt out loud")
    @app_commands.describe(prompt="The prompt to answer and speak aloud")
    async def voice(self, ctx: commands.Context, *, prompt: str) -> None:
        """Answer a prompt and read the reply aloud."""
        if not prompt:
            await ctx.send("Please provide text to convert to speech.")
            return
        if len(prompt) > 15000:
            await ctx.send("Text is too long. Maximum 15,000 characters.")
            return

        api_key = xai_api_key()
        if not api_key:
            await ctx.send("XAI API key not configured.")
            return

        async with acknowledge(ctx):
            try:
                await self._ensure_message_row(ctx, content=prompt)
                response_text = await self._run_grok_turn(
                    prompt,
                    user_id=ctx.author.id,
                    message_id=ctx.message.id,
                )
                if not response_text:
                    await _send_ask_answer(
                        ctx,
                        prompt,
                        "Sorry, I couldn't generate a spoken response. Please try again.",
                    )
                    return

                bot_session: aiohttp.ClientSession | None = getattr(
                    self.bot, "http_session", None
                )
                owns_session = bot_session is None or bot_session.closed
                active_session = (
                    aiohttp.ClientSession() if owns_session else bot_session
                )
                assert active_session is not None
                try:
                    audio_bytes = await synthesize_speech(
                        active_session, response_text, api_key=api_key
                    )
                finally:
                    if owns_session:
                        await active_session.close()

                audio_file = discord.File(io.BytesIO(audio_bytes), filename="voice.mp3")
                if ctx.interaction is not None:
                    await _send_ask_answer(ctx, prompt, response_text, file=audio_file)
                else:
                    await ctx.send(file=audio_file)

            except (aiohttp.ClientError, TimeoutError):
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AI(bot))
