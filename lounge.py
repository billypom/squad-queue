import discord
from discord.ext import commands
import json
import logging
import DBA
print('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
logging.basicConfig(level=logging.INFO)

intents = discord.Intents(message_content=True, messages=True, members=True, guilds=True)
bot = commands.Bot(intents=intents, activity=discord.Game(str('hg')), command_prefix='!', case_insensitive=True)

initial_extensions = ['cogs.Mogi', 'cogs.Sheet']

for extension in initial_extensions:
    print('loading extension')
    bot.load_extension(extension)

with open('./config.json', 'r') as cjson:
    config = json.load(cjson)

@bot.event
async def on_ready():
    print("Logged in as {0.user}".format(bot))


@bot.event
async def on_command_error(ctx, error):
    print('im error')
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await(await ctx.send("Your command is missing an argument: `%s`" %
                       str(error.param))).delete(delay=10)
        return
    if isinstance(error, commands.CommandOnCooldown):
        await(await ctx.send("This command is on cooldown; try again in %.0fs"
                       % error.retry_after)).delete(delay=5)
        return
    if isinstance(error, commands.MissingAnyRole):
        await(await ctx.send("You need one of the following roles to use this command: `%s`"
                             % (", ".join(error.missing_roles)))
              ).delete(delay=10)
        return
    if isinstance(error, commands.BadArgument):
        await(await ctx.send("BadArgument Error: `%s`" % error.args)).delete(delay=10)
        return
    if isinstance(error, commands.BotMissingPermissions):
        await(await ctx.send("I need the following permissions to use this command: %s"
                       % ", ".join(error.missing_perms))).delete(delay=10)
        return
    if isinstance(error, commands.NoPrivateMessage):
        await(await ctx.send("You can't use this command in DMs!")).delete(delay=5)
        return
    raise error

bot.run(config["token"])
