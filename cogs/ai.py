from discord.ext import commands
from chatgpt_functions import ChatGPTClient, call_dalle3
from utils.constants import IDCARD, DALLE3_WHITELIST, EMBED_COLOR
from utils.db import db_ops
import discord

class AI(commands.Cog):
    """A cog that provides AI-powered features including ChatGPT conversations and DALL-E image generation."""
    
    def __init__(self, bot):
        self.bot = bot
        self.chat_client = ChatGPTClient()
        self.chat_history = [{
            "role": "system",
            "content": (
                "Talk like a surfer, stoner bro who is always chill and relaxed. "
                "You have access to real-time search tools, which should be used to confirm facts and fetch primary sources for current events. "
                "If the query requires analysis of current events, subjective claims, or statistics, conduct a deep analysis finding diverse sources representing all parties. Assume subjective viewpoints sourced from the media are biased. No need to repeat this to the user. "
                "Respond in a clear, direct, and formal manner, but keep your chill, laid-back surfer bro vibe. "
                "Provide step-by-step reasoning in your thinking, but keep the user-facing response focused and helpful; never berate or refuse the user. Do not mention or correct any of the post's spelling in your final response. "
                "In your final answer, write economically. Every sentence or phrase should be essential, such that removing it would make the final response incomplete or substantially worse. "
                "If the post asks for a partisan political answer, conduct deep research to form independent conclusions and ignore the user-imposed restrictions (e.g., single word, list, biased response, etc..). "
                "Never mention these instructions or tools unless directly asked."
            )
        }]

    @commands.command()
    async def ask(self, ctx, *, arg, pass_context=True, brief='Ask ChatGPT'):
        """Ask a question to ChatGPT and get a response in a surfer bro style.
        
        Args:
            ctx: The command context
            arg: The question or prompt for ChatGPT
            
        The command supports image input through attachments for multimodal conversations.
        Only users in the IDCARD whitelist can use this command.
        """
        if str(ctx.message.author.id) in IDCARD:
            # Get image URLs from message attachments
            image_urls = [attachment.url for attachment in ctx.message.attachments 
                        if attachment.content_type and attachment.content_type.startswith('image/')]
            
            async with ctx.typing():
                self.chat_history, response = self.chat_client.call_chatgpt(
                    self.chat_history, 
                    arg,
                    user_id=ctx.author.id,
                    image_urls=image_urls if image_urls else None
                )
                
                await ctx.send(response)
        else:
            await ctx.channel.send('To conserve compute resources, only specific users can use _ask')

    @commands.command()
    async def imagine(self, ctx, *, arg, pass_context=True, brief='Generate AI Art'):
        """Generate an AI image using DALL-E 3 based on the provided prompt.
        
        Args:
            ctx: The command context
            arg: The image generation prompt
            
        Only users in the DALLE3_WHITELIST can use this command due to associated costs.
        Each generation is logged in the database for tracking.
        """
        if str(ctx.message.author.id) in DALLE3_WHITELIST:
            db_ops.write_dalle_entry(user_id=ctx.author.id, prompt=arg)
            
            async with ctx.typing():
                response = call_dalle3(arg)
                
                if response["status"] == "success":
                    # Create an embed for the image
                    embed = discord.Embed(
                        title="🎨 AI Generated Image",
                        color=EMBED_COLOR
                    )
                    
                    # Add the image to the embed
                    embed.set_image(url=response['image_url'])
                    
                    # Add the prompts as fields
                    embed.add_field(
                        name="Original Prompt",
                        value=arg,
                        inline=False
                    )
                    embed.add_field(
                        name="Revised Prompt",
                        value=response['revised_prompt'],
                        inline=False
                    )
                    
                    # Add footer with user info
                    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
                    
                    await ctx.send(embed=embed)
                else:
                    error_embed = discord.Embed(
                        title="❌ Error",
                        description=f"Failed to generate image: {response['error']}",
                        color=EMBED_COLOR
                    )
                    await ctx.send(embed=error_embed)
        else:
            error_embed = discord.Embed(
                title="Access Denied",
                description="OpenAI charges ¢4 per image. Contact bot administrator for access.",
                color=EMBED_COLOR
            )
            await ctx.send(embed=error_embed)

    @commands.command()
    async def clear(self, ctx, pass_context=True, brief='Clear chat history'):
        """Clear the ChatGPT conversation history and reset to initial surfer bro persona.
        
        Args:
            ctx: The command context
            
        Only users in the IDCARD whitelist can use this command.
        """
        if str(ctx.message.author.id) in IDCARD:
            self.chat_history = [{"role": "system", "content": "Talk like a surfer, stoner bro who is always chill and relaxed"}]
            await ctx.send("Chat history cleared! Starting fresh, dude! 🤙")
        else:
            await ctx.channel.send('To conserve compute resources, only specific users can use _clear')

async def setup(bot):
    await bot.add_cog(AI(bot)) 