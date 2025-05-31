import discord
from discord.ext import commands
from chatgpt_functions import call_chatGPT, call_dalle3, IDCARD, DALLE3_WHITELIST
from utils.db import write_to_db

class AI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.chat_history = [{"role": "system", "content": "Talk like a surfer, stoner bro who is always chill and relaxed"}]

    @commands.command()
    async def ask(self, ctx, *, arg, pass_context=True, brief='Ask ChatGPT'):
        # Passes prompt to ChatGPT API and returns response
        if str(ctx.message.author.id) in IDCARD:
            async with ctx.typing():
                self.chat_history, response = call_chatGPT(self.chat_history, arg)
            await ctx.send(response)
        else:
            await ctx.channel.send('To conserve compute resources, only specific users can use _ask')

    @commands.command()
    async def imagine(self, ctx, *, arg, pass_context=True, brief='Generate AI Art'):
        if str(ctx.message.author.id) in DALLE3_WHITELIST:
            write_to_db(table_name='dalle_3_prompts',user_id=ctx.author.id, prompt=arg)
            async with ctx.typing():
                response = call_dalle3(arg)
            await ctx.channel.send(str(response))
        else:
            await ctx.channel.send('OpenAI charges ¢4 per image. Contact bot administrator for access.')

async def setup(bot):
    await bot.add_cog(AI(bot)) 