from discord.ext import commands
from chatgpt_functions import ChatGPTClient, call_dalle3
from utils.constants import IDCARD, DALLE3_WHITELIST
from utils.db import db_ops

class AI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.chat_client = ChatGPTClient()
        self.chat_history = [{"role": "system", "content": "Talk like a surfer, stoner bro who is always chill and relaxed"}]

    @commands.command()
    async def ask(self, ctx, *, arg, pass_context=True, brief='Ask ChatGPT'):
        # Passes prompt to ChatGPT API and returns response
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
        if str(ctx.message.author.id) in DALLE3_WHITELIST:
            db_ops.write_dalle_entry(user_id=ctx.author.id, prompt=arg)
            async with ctx.typing():
                response = call_dalle3(arg)
            await ctx.channel.send(str(response))
        else:
            await ctx.channel.send('OpenAI charges ¢4 per image. Contact bot administrator for access.')

    @commands.command()
    async def clear(self, ctx, pass_context=True, brief='Clear chat history'):
        """Clear the chat history and reset to initial state"""
        if str(ctx.message.author.id) in IDCARD:
            self.chat_history = [{"role": "system", "content": "Talk like a surfer, stoner bro who is always chill and relaxed"}]
            await ctx.send("Chat history cleared! Starting fresh, dude! 🤙")
        else:
            await ctx.channel.send('To conserve compute resources, only specific users can use _clear')

async def setup(bot):
    await bot.add_cog(AI(bot)) 