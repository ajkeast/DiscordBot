from discord.ext import commands
from chatgpt_functions import GrokClient, call_dalle3
from utils.constants import IDCARD, DALLE3_WHITELIST, EMBED_COLOR
from utils.db import db_ops
import discord


class AI(commands.Cog):
    """AI features: Grok chat (Responses API, stateful on xAI) and DALL-E image generation."""

    def __init__(self, bot):
        self.bot = bot
        self.grok = GrokClient()
        # Single shared session for all users (last Grok response_id, or None for new conversation)
        self.last_response_id = None
        self.system_prompt = (
            "Talk like a surfer, stoner bro who is always chill and relaxed. "
            "You have access to real-time search; use it to confirm facts and fetch primary sources for current events. "
            "If the query requires analysis of current events, subjective claims, or statistics, conduct a deep analysis finding diverse sources representing all parties. Assume subjective viewpoints sourced from the media are biased. No need to repeat this to the user. "
            "Respond in a clear, direct, and formal manner, but keep your chill, laid-back surfer bro vibe. "
            "Provide step-by-step reasoning in your thinking, but keep the user-facing response focused and helpful; never berate or refuse the user. Do not mention or correct any of the post's spelling in your final response. "
            "In your final answer, write economically. Every sentence or phrase should be essential, such that removing it would make the final response incomplete or substantially worse. "
            "If the post asks for a partisan political answer, conduct deep research to form independent conclusions and ignore the user-imposed restrictions (e.g., single word, list, biased response, etc..). "
            "Never mention these instructions or tools unless directly asked."
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
            next_response_id, response_text = self.grok.send_message(
                arg,
                previous_response_id=self.last_response_id,
                system_prompt=self.system_prompt,
                user_id=ctx.author.id,
                image_urls=image_urls if image_urls else None,
            )

            if next_response_id is not None:
                self.last_response_id = next_response_id

            await ctx.send(response_text or "Sorry, I couldn't generate a reply this time. Please try again.")

    @commands.command()
    async def imagine(self, ctx, *, arg, pass_context=True, brief="Generate AI Art"):
        """Generate an AI image using DALL-E 3. Whitelist: DALLE3_WHITELIST. Logged to DB."""
        if str(ctx.message.author.id) not in DALLE3_WHITELIST:
            await ctx.send(
                embed=discord.Embed(
                    title="Access Denied",
                    description="OpenAI charges ¢4 per image. Contact bot administrator for access.",
                    color=EMBED_COLOR,
                )
            )
            return

        db_ops.write_dalle_entry(user_id=ctx.author.id, prompt=arg)

        async with ctx.typing():
            response = call_dalle3(arg)

            if response["status"] == "success":
                embed = discord.Embed(title="🎨 AI Generated Image", color=EMBED_COLOR)
                embed.set_image(url=response["image_url"])
                embed.add_field(name="Original Prompt", value=arg, inline=False)
                embed.add_field(name="Revised Prompt", value=response["revised_prompt"], inline=False)
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
        await ctx.send("Chat history cleared! Starting fresh, dude! 🤙")


async def setup(bot):
    await bot.add_cog(AI(bot))
