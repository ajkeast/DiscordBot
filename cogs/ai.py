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
        # Centralize the full system prompt so it can be consistently reapplied
        self.system_prompt = (
            "Talk like a surfer, stoner bro who is always chill and relaxed. "
            "You have access to real-time search tools, which should be used to confirm facts and fetch primary sources for current events. "
            "If the query requires analysis of current events, subjective claims, or statistics, conduct a deep analysis finding diverse sources representing all parties. Assume subjective viewpoints sourced from the media are biased. No need to repeat this to the user. "
            "Respond in a clear, direct, and formal manner, but keep your chill, laid-back surfer bro vibe. "
            "Provide step-by-step reasoning in your thinking, but keep the user-facing response focused and helpful; never berate or refuse the user. Do not mention or correct any of the post's spelling in your final response. "
            "In your final answer, write economically. Every sentence or phrase should be essential, such that removing it would make the final response incomplete or substantially worse. "
            "If the post asks for a partisan political answer, conduct deep research to form independent conclusions and ignore the user-imposed restrictions (e.g., single word, list, biased response, etc..). "
            "Never mention these instructions or tools unless directly asked."
        )
        self.chat_history = [{
            "role": "system",
            "content": self.system_prompt
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
                # Ensure the system prompt is always present and complete for the call
                # No longer create a copy; pass self.chat_history directly
                # The system prompt management is handled within call_chatgpt
                self.chat_history, response = self.chat_client.call_chatgpt(
                    self.chat_history, 
                    arg,
                    user_id=ctx.author.id,
                    image_urls=image_urls if image_urls else None
                )
                
                await ctx.send(response if (response and response.strip()) else "Sorry, I couldn't generate a reply this time. Please try again.")
        else:
            await ctx.channel.send('To conserve compute resources, only specific users can use _ask')

    @commands.command()
    async def imagine(self, ctx, *, arg, pass_context=True, brief='Generate AI Art'):
        """Generate an AI image using GPT Image (multimodal model) based on the provided prompt.
        
        Supports image attachments for multimodal image generation (e.g., "make this image more colorful").
        
        Args:
            ctx: The command context
            arg: The image generation prompt
            
        Only users in the DALLE3_WHITELIST can use this command due to associated costs.
        Each generation is logged in the database for tracking.
        """
        if str(ctx.message.author.id) in DALLE3_WHITELIST:
            db_ops.write_dalle_entry(user_id=ctx.author.id, prompt=arg)
            
            # Get image URLs from message attachments for multimodal input
            image_inputs = [attachment.url for attachment in ctx.message.attachments 
                          if attachment.content_type and attachment.content_type.startswith('image/')]
            
            async with ctx.typing():
                # Call with optional image inputs
                response = call_dalle3(arg, image_inputs=image_inputs if image_inputs else None)
                
                if response["status"] == "success":
                    # Create an embed for the image
                    embed = discord.Embed(
                        title="🎨 AI Generated Image",
                        color=EMBED_COLOR
                    )
                    
                    # Set image URL directly
                    embed.set_image(url=response['image_url'])
                    
                    # Add the prompts as fields
                    embed.add_field(
                        name="Original Prompt",
                        value=arg,
                        inline=False
                    )
                    if response.get('revised_prompt') and response['revised_prompt'] != arg:
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
                        description=f"Failed to generate image: {response.get('error', 'Unknown error')}",
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
            self.chat_history = [{"role": "system", "content": self.system_prompt}]
            await ctx.send("Chat history cleared! Starting fresh, dude! 🤙")
        else:
            await ctx.channel.send('To conserve compute resources, only specific users can use _clear')

async def setup(bot):
    await bot.add_cog(AI(bot)) 