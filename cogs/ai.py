from discord.ext import commands, tasks
from chatgpt_functions import GrokClient, call_grok_imagine, GROK_IMAGINE_FILENAME
from utils.constants import (
    EMBED_COLOR,
    IMAGINE_RATE_LIMIT,
    IMAGINE_RATE_PERIOD_SECONDS,
    MAX_GROK_SESSION_TURNS,
    MAX_IMAGINE_INPUT_IMAGES,
)
from utils.db import db_ops
from datetime import datetime, timedelta
import asyncio
import discord
import pytz
import requests
import os
import io
import logging

logger = logging.getLogger(__name__)

EASTERN = pytz.timezone("US/Eastern")
DAILY_CLEAR_HOUR = 3  # 3am US/Eastern


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
            "You are not just a chat assistant: you run the daily _1st game, the DinkCoin (DINK) economy, "
            "image generation, and server stats. Commands use the '_' prefix. "
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
        self.daily_chat_clear.start()

    def cog_unload(self):
        self.daily_chat_clear.cancel()

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

    @commands.command(brief="Ask a question")
    async def ask(self, ctx, *, arg):
        """Ask a question. You can also attach images."""

        image_urls = [
            a.url
            for a in ctx.message.attachments
            if a.content_type and a.content_type.startswith("image/")
        ]

        async with ctx.typing():
            # Start a new session if we've hit the turn limit (keeps context/cost bounded)
            if self._session_turns >= MAX_GROK_SESSION_TURNS:
                self._reset_session()

            try:
                next_response_id, response_text = self.grok.send_message(
                    arg,
                    previous_response_id=self.last_response_id,
                    system_prompt=self._build_system_prompt(),
                    user_id=ctx.author.id,
                    message_id=ctx.message.id,
                    image_urls=image_urls if image_urls else None,
                )
            except Exception:
                logger.exception("_ask failed for user %s", ctx.author.id)
                await ctx.send("Something broke on my end, dude. Check the bot logs and try again.")
                return

            if next_response_id is not None:
                self.last_response_id = next_response_id
                self._session_turns += 1

            await ctx.send(response_text or "Sorry, I couldn't generate a reply this time. Please try again.")

    @commands.command(brief="Generate an AI image")
    @commands.cooldown(IMAGINE_RATE_LIMIT, IMAGINE_RATE_PERIOD_SECONDS, commands.BucketType.user)
    async def imagine(self, ctx, *, arg):
        """Generate an AI image based on a prompt and optional input images.

        Attach up to 3 images to edit or combine them; refer to them in the prompt
        as <IMAGE_0>, <IMAGE_1>, <IMAGE_2> (attachment order).
        """

        db_ops.write_dalle_entry(user_id=ctx.author.id, prompt=arg, message_id=ctx.message.id)

        input_image_urls = [
            a.url
            for a in ctx.message.attachments
            if a.content_type and a.content_type.startswith("image/")
        ]
        if len(input_image_urls) > MAX_IMAGINE_INPUT_IMAGES:
            await ctx.send(
                f"You can attach at most {MAX_IMAGINE_INPUT_IMAGES} images for `_imagine`."
            )
            return

        async with ctx.typing():
            response = call_grok_imagine(
                arg,
                input_image_urls=input_image_urls or None,
            )

            if response["status"] == "success":
                image_file = discord.File(
                    io.BytesIO(response["image_bytes"]),
                    filename=GROK_IMAGINE_FILENAME,
                )
                embed = discord.Embed(title="🎨 AI Generated Image", color=EMBED_COLOR)
                embed.set_image(url=f"attachment://{GROK_IMAGINE_FILENAME}")
                embed.add_field(name="Prompt", value=arg, inline=False)
                if input_image_urls:
                    count = len(input_image_urls)
                    embed.add_field(
                        name="Input images",
                        value=f"{count} attached",
                        inline=False,
                    )
                embed.set_footer(text=f"Requested by {ctx.author.display_name}")
                await ctx.send(embed=embed, file=image_file)
            else:
                logger.error("_imagine failed: %s", response.get("error"))
                await ctx.send(
                    embed=discord.Embed(
                        title="❌ Error",
                        description="Failed to generate image. Check the bot logs and try again.",
                        color=EMBED_COLOR,
                    )
                )

    @commands.command(brief="Clear the shared chat")
    async def clear(self, ctx):
        """Clear the shared chat so the next _ask starts fresh."""

        self._reset_session()
        await ctx.send("Chat history cleared! Starting fresh, dude! 🤙")

    @commands.command(brief="Answer a prompt out loud")
    async def voice(self, ctx, *, text):
        """Answer a prompt and read the reply aloud."""

        if not text:
            await ctx.send("Please provide text to convert to speech.")
            return

        if len(text) > 15000:
            await ctx.send("Text is too long. Maximum 15,000 characters.")
            return

        api_key = os.getenv('XAI_API_KEY')
        if not api_key:
            await ctx.send("XAI API key not configured.")
            return

        async with ctx.typing():
            try:
                if self._session_turns >= MAX_GROK_SESSION_TURNS:
                    self._reset_session()

                next_response_id, response_text = self.grok.send_message(
                    text,
                    previous_response_id=self.last_response_id,
                    system_prompt=self._build_system_prompt(),
                    user_id=ctx.author.id,
                    message_id=ctx.message.id,
                )

                if next_response_id is not None:
                    self.last_response_id = next_response_id
                    self._session_turns += 1

                if not response_text:
                    await ctx.send("Sorry, I couldn't generate a spoken response. Please try again.")
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

                await ctx.send(file=audio_file)

            except requests.exceptions.RequestException:
                logger.exception("_voice TTS request failed for user %s", ctx.author.id)
                await ctx.send("Something broke generating speech. Check the bot logs and try again.")
            except Exception:
                logger.exception("_voice failed for user %s", ctx.author.id)
                await ctx.send("Something broke on my end, dude. Check the bot logs and try again.")


async def setup(bot):
    await bot.add_cog(AI(bot))
