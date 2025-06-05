from discord.ext import commands
from chatgpt_functions import ChatGPTClient, call_dalle3
from utils.constants import IDCARD, DALLE3_WHITELIST
from utils.db import db_ops

class AI(commands.Cog):
    """A cog that provides AI-powered features including ChatGPT conversations and DALL-E image generation."""
    
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
                
                # If there were images in the input, add a note
                if image_urls:
                    response = "I've analyzed the attached image(s)!\n\n" + response
                
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
                    # Send the image URL and prompts
                    message = f"🎨 **Generated Image**\n\n**Original Prompt:** {arg}\n**Revised Prompt:** {response['revised_prompt']}\n\n{response['image_url']}"
                    await ctx.send(message)
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