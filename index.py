# This example requires the 'message_content' intent.

import ssl


ssl._create_default_https_context = ssl._create_unverified_context

import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import random
import string
import os

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='/', intents=intents)

    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

proposals = {}
OUTPUT_CHANNEL_NAME = "fedex"  # Easy to change output channel name

def generate_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')

async def get_output_channel(guild_id):
    guild = bot.get_guild(guild_id)
    if guild:
        return discord.utils.get(guild.channels, name=OUTPUT_CHANNEL_NAME)
    return None

@bot.tree.command(name="new", description="Propose a new member")
@app_commands.describe(name="Name of the proposed member")
async def new(interaction: discord.Interaction, name: str):
    proposal_id = generate_id()
    proposals[proposal_id] = {
        'name': name,
        'vetoed': False,
        'timer': asyncio.create_task(proposal_timer(interaction.guild_id, proposal_id, name))
    }

    response_message = f"Member proposal for {name} created, id: {proposal_id}"

    # Send response to user
    await interaction.response.send_message(response_message, ephemeral=True)

    # Send announcement to output channel
    output_channel = await get_output_channel(interaction.guild_id)
    if output_channel:
        await output_channel.send(response_message)
    else:
        await interaction.followup.send(f"Warning: Couldn't find the '{OUTPUT_CHANNEL_NAME}' channel to announce the proposal.", ephemeral=True)

@bot.tree.command(name="veto", description="Veto a member proposal")
@app_commands.describe(proposal_id="ID of the proposal to veto", time="When to apply the veto")
@app_commands.choices(time=[
    app_commands.Choice(name="Now", value="now"),
    app_commands.Choice(name="At deadline", value="deadline")
])
async def veto(interaction: discord.Interaction, proposal_id: str, time: str):
    if proposal_id not in proposals:
        await interaction.response.send_message("Invalid proposal ID.", ephemeral=True)
        return

    proposal = proposals[proposal_id]
    if time == 'now':
        proposal['vetoed'] = True
        proposal['timer'].cancel()

        output_channel = await get_output_channel(interaction.guild_id)

        if output_channel is None:
            await interaction.response.send_message(f"Error: Couldn't find the '{OUTPUT_CHANNEL_NAME}' channel.", ephemeral=True)
            return

        try:
            await output_channel.send(f"The proposal for {proposal['name']} has been vetoed.")
        except discord.errors.Forbidden:
            await interaction.response.send_message(f"Error: The bot doesn't have permission to send messages in the '{OUTPUT_CHANNEL_NAME}' channel.", ephemeral=True)
            return
    else:
        proposal['vetoed'] = True

    await interaction.response.send_message(f"Veto for proposal {proposal_id} recorded.", ephemeral=True)

async def proposal_timer(guild_id, proposal_id, name):
    await asyncio.sleep(172800)  # 48 hours
    output_channel = await get_output_channel(guild_id)
    if output_channel:
        if not proposals[proposal_id]['vetoed']:
            await output_channel.send(f"The proposal for {name} has passed.")
        else:
            await output_channel.send(f"The proposal for {name} has been vetoed.")
    del proposals[proposal_id]

# Load token from environment variable
token = os.getenv('DISCORD_TOKEN')
if not token:
    raise ValueError("No token found. Make sure to set the DISCORD_TOKEN environment variable.")

bot.run(token)
