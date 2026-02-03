from discord.ext import commands
from chatgpt_functions import GrokClient, call_grok_imagine
from utils.constants import IDCARD, DALLE3_WHITELIST, EMBED_COLOR, MAX_GROK_SESSION_TURNS
from utils.db import db_ops
import discord


class AI(commands.Cog):
    """AI features: Grok chat (Responses API, stateful on xAI) and Grok Imagine image generation."""

    def __init__(self, bot):
        self.bot = bot
        self.grok = GrokClient()
        # Single shared session for all users (last Grok response_id, or None for new conversation)
        self.last_response_id = None
        self._session_turns = 0  # turns in current session; reset when starting fresh or hitting limit
        self.system_prompt = (
            "Talk like a surfer, stoner bro who is always chill and relaxed. "
            "You have access to real-time search; use it to confirm facts and fetch primary sources for current events. "
            "In your final answer, write economically. A single sentence should often be enough."
            "Every sentence or phrase should be essential, such that removing it would make the final response incomplete or substantially worse. "
        )

    @commands.command()
    async def ask(self, ctx, *, arg, pass_context=True, brief="Ask Grok"):
        """Ask Grok (stateful conversation, native search). Whitelist: IDCARD. Supports image attachments."""
        if str(ctx.message.author.id) not in IDCARD:
            await ctx.channel.send("To conserve compute resources, only specific users can use _ask")
            return

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

            next_response_id, response_text = self.grok.send_message(
                arg,
                previous_response_id=self.last_response_id,
                system_prompt=self.system_prompt,
                user_id=ctx.author.id,
                image_urls=image_urls if image_urls else None,
            )

            if next_response_id is not None:
                self.last_response_id = next_response_id
                self._session_turns += 1

            await ctx.send(response_text or "Sorry, I couldn't generate a reply this time. Please try again.")

    @commands.command()
    async def imagine(self, ctx, *, arg, pass_context=True, brief="Generate AI Art"):
        """Generate an AI image using Grok Imagine (xAI). Whitelist: DALLE3_WHITELIST. Logged to DB."""
        if str(ctx.message.author.id) not in DALLE3_WHITELIST:
            await ctx.send(
                embed=discord.Embed(
                    title="Access Denied",
                    description="Image generation is restricted. Contact bot administrator for access.",
                    color=EMBED_COLOR,
                )
            )
            return

        db_ops.write_dalle_entry(user_id=ctx.author.id, prompt=arg)

        async with ctx.typing():
            response = call_grok_imagine(arg)

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
        """Clear the shared Grok conversation session so the next _ask starts fresh. Whitelist: IDCARD."""
        if str(ctx.message.author.id) not in IDCARD:
            await ctx.channel.send("To conserve compute resources, only specific users can use _clear")
            return

        self.last_response_id = None
        self._session_turns = 0
        await ctx.send("Chat history cleared! Starting fresh, dude! 🤙")


async def setup(bot):
    await bot.add_cog(AI(bot))
