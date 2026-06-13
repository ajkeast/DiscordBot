from discord.ext import commands
from chatgpt_functions import GrokClient, call_grok_imagine
from utils.constants import EMBED_COLOR, MAX_GROK_SESSION_TURNS
from utils.db import db_ops
import discord
import requests
import os
import io
import logging

logger = logging.getLogger(__name__)


class AI(commands.Cog):
    """AI features: Grok chat (Responses API, stateful on xAI) and Grok Imagine image generation."""

    def __init__(self, bot):
        self.bot = bot
        self.grok = GrokClient()
        # Single shared session for all users (last Grok response_id, or None for new conversation)
        self.last_response_id = None
        self._session_turns = 0  # turns in current session; reset when starting fresh or hitting limit
        self.system_prompt = (
            "You are a coherent and helpful assistant in a guild on Discord. You think from first principles and reason step by step when applicable. "
            "You have access to real-time search; use it to confirm facts and fetch primary sources for current events. "
            "In your final answer, write economically. A single sentence should often be enough. "
            "Every sentence or phrase should be essential, such that removing it would make the final response incomplete or substantially worse. "
            "Do not use markdown bold (**text**) for whole responses or multiple sentences—especially after web search. "
        )

    @commands.command()
    async def ask(self, ctx, *, arg, pass_context=True, brief="Ask Grok"):
        """Ask Grok (stateful conversation, native search). Supports image attachments."""

        image_urls = [
            a.url
            for a in ctx.message.attachments
            if a.content_type and a.content_type.startswith("image/")
        ]

        async with ctx.typing():
            # Start a new session if we've hit the turn limit (keeps context/cost bounded)
            if self._session_turns >= MAX_GROK_SESSION_TURNS:
                self.last_response_id = None
                self._session_turns = 0

            try:
                next_response_id, response_text = self.grok.send_message(
                    arg,
                    previous_response_id=self.last_response_id,
                    system_prompt=self.system_prompt,
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

    @commands.command()
    async def imagine(self, ctx, *, arg, pass_context=True, brief="Generate AI Art"):
        """Generate an AI image using Grok Imagine (xAI). Logged to DB."""

        db_ops.write_dalle_entry(user_id=ctx.author.id, prompt=arg, message_id=ctx.message.id)

        # Optional: first image attachment URL used as input for editing
        input_image_url = None
        for a in ctx.message.attachments:
            if a.content_type and a.content_type.startswith("image/"):
                input_image_url = a.url
                break

        async with ctx.typing():
            response = call_grok_imagine(arg, input_image_url=input_image_url)

            if response["status"] == "success":
                embed = discord.Embed(title="🎨 AI Generated Image", color=EMBED_COLOR)
                embed.set_image(url=response["image_url"])
                embed.add_field(name="Prompt", value=arg, inline=False)
                embed.set_footer(text=f"Requested by {ctx.author.display_name}")
                await ctx.send(embed=embed)
            else:
                await ctx.send(
                    embed=discord.Embed(
                        title="❌ Error",
                        description=f"Failed to generate image: {response['error']}",
                        color=EMBED_COLOR,
                    )
                )

    @commands.command()
    async def clear(self, ctx, pass_context=True, brief="Clear chat session"):
        """Clear the shared Grok conversation session so the next _ask starts fresh."""

        self.last_response_id = None
        self._session_turns = 0
        await ctx.send("Chat history cleared! Starting fresh, dude! 🤙")

    @commands.command()
    async def voice(self, ctx, *, text, pass_context=True, brief="Text to Speech"):
        """Generate a Grok response to the prompt, then convert that reply to speech."""

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
                    self.last_response_id = None
                    self._session_turns = 0

                next_response_id, response_text = self.grok.send_message(
                    text,
                    previous_response_id=self.last_response_id,
                    system_prompt=self.system_prompt,
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

            except requests.exceptions.RequestException as e:
                await ctx.send(f"Error generating speech: {str(e)}")
            except Exception as e:
                await ctx.send(f"An error occurred: {str(e)}")


async def setup(bot):
    await bot.add_cog(AI(bot))
