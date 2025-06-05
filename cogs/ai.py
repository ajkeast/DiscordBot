from discord.ext import commands
from chatgpt_functions import ChatGPTClient
from utils.constants import IDCARD, DALLE3_WHITELIST
from utils.db import db_ops
import base64
import io
import discord

class AI(commands.Cog):
    """A cog that provides AI-powered features including ChatGPT conversations and image generation."""
    
    def __init__(self, bot):
        self.bot = bot
        self.chat_client = ChatGPTClient()
        self.chat_history = [{"role": "system", "content": "Talk like a surfer, stoner bro who is always chill and relaxed"}]

    @commands.command()
    async def ask(self, ctx, *, arg, pass_context=True, brief='Ask ChatGPT'):
        """Ask a question to ChatGPT and get a response in a surfer bro style.
        
        Args:
            ctx: The command context
            arg: The question or prompt for ChatGPT
            
        The command supports:
        - Image input through attachments for multimodal conversations
        - Image generation when the model decides it's appropriate
        - Function calling for various utilities
        Only users in the IDCARD whitelist can use this command.
        """
        if str(ctx.message.author.id) in IDCARD:
            # Get image URLs from message attachments
            image_urls = [attachment.url for attachment in ctx.message.attachments 
                        if attachment.content_type and attachment.content_type.startswith('image/')]
            
            async with ctx.typing():
                self.chat_history, response, image_data, revised_prompt = self.chat_client.call_chatgpt(
                    self.chat_history, 
                    arg,
                    user_id=ctx.author.id,
                    image_urls=image_urls if image_urls else None
                )
                
                # If there were images in the input, add a note
                if image_urls:
                    response = "I've analyzed the attached image(s)!\n\n" + response
                
                # If an image was generated, send it along with the response
                if image_data:
                    # Convert base64 image data to bytes
                    image_bytes = base64.b64decode(image_data)
                    
                    # Create a Discord file from the image bytes
                    image_file = discord.File(
                        io.BytesIO(image_bytes),
                        filename="generated_image.png"
                    )
                    
                    # Add the revised prompt to the response if available
                    if revised_prompt:
                        response += f"\n\n**Generated Image Prompt:** {revised_prompt}"
                    
                    await ctx.send(response, file=image_file)
                else:
                    await ctx.send(response)
        else:
            await ctx.channel.send('To conserve compute resources, only specific users can use _ask')

    @commands.command()
    async def imagine(self, ctx, *, arg, pass_context=True, brief='Generate AI Art'):
        """Generate an AI image using GPT-4.1-mini's built-in image generation.
        
        Args:
            ctx: The command context
            arg: The image generation prompt
            
        Only users in the DALLE3_WHITELIST can use this command due to associated costs.
        Each generation is logged in the database for tracking.
        """
        if str(ctx.message.author.id) in DALLE3_WHITELIST:
            db_ops.write_dalle_entry(user_id=ctx.author.id, prompt=arg)
            
            async with ctx.typing():
                response = self.chat_client.generate_image(arg)
                
                if response["status"] == "success":
                    # Convert base64 image data to bytes
                    image_bytes = base64.b64decode(response["image_data"])
                    
                    # Create a Discord file from the image bytes
                    image_file = discord.File(
                        io.BytesIO(image_bytes),
                        filename="generated_image.png"
                    )
                    
                    # Send the image and prompts
                    message = f"🎨 **Generated Image**\n\n**Original Prompt:** {arg}\n**Revised Prompt:** {response['revised_prompt']}"
                    await ctx.send(message, file=image_file)
                else:
                    await ctx.send(f"❌ Error generating image: {response['error']}")
        else:
            await ctx.channel.send('OpenAI charges ¢4 per image. Contact bot administrator for access.')

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