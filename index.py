import ssl
import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import os
import time
from flask import Flask
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)

app = Flask(__name__)

ssl._create_default_https_context = ssl._create_unverified_context

SERVER_ID = 977606746317144154
OUTPUT_CHANNEL_NAME = "fedex"
TIMEOUT_SECONDS = 172800;

intents = discord.Intents.default()
intents.guilds = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='/', intents=intents)

    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

proposals = {}


@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user.name}')

async def get_output_channel():
    guild = bot.get_guild(SERVER_ID)
    if guild:
        return discord.utils.get(guild.channels, name=OUTPUT_CHANNEL_NAME)
    return None

class VetoView(discord.ui.View):
    def __init__(self, proposal_id):
        super().__init__()
        self.proposal_id = proposal_id

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await veto_proposal(interaction, self.proposal_id)
        await interaction.response.send_message("You have vetoed the proposal.", ephemeral=True)

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Veto cancelled.", ephemeral=True)

class ProposalView(discord.ui.View):
    def __init__(self, proposal_id):
        super().__init__()
        self.proposal_id = proposal_id

    @discord.ui.button(label="Veto", style=discord.ButtonStyle.danger)
    async def veto_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        proposal = proposals.get(self.proposal_id.lower())
        if not proposal:
            await interaction.response.send_message("This proposal no longer exists.", ephemeral=True)
            return
        await interaction.user.send(f"Are you sure you want to veto the proposal for {proposal['name']}?", view=VetoView(self.proposal_id))
        await interaction.response.send_message("Check your DMs for the veto confirmation.", ephemeral=True)

    @discord.ui.button(label="Subscribe", style=discord.ButtonStyle.primary)
    async def subscribe_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        proposal = proposals.get(self.proposal_id.lower())
        if not proposal:
            await interaction.response.send_message("This proposal no longer exists.", ephemeral=True)
            return
        if interaction.user.id not in proposal['subscribers']:
            proposal['subscribers'].append(interaction.user.id)
            await interaction.response.send_message("You have subscribed to updates for this proposal.", ephemeral=True)
        else:
            await interaction.response.send_message("You are already subscribed to this proposal.", ephemeral=True)

@bot.tree.command(name="new", description="Propose a new member")
@app_commands.describe(name="Name of the proposed member")
async def new(interaction: discord.Interaction, name: str):
    proposal_id = name.lower()
    if proposal_id in proposals:
        await interaction.response.send_message(f"A proposal for '{name}' already exists.", ephemeral=True)
        return

    deadline = int(time.time()) + TIMEOUT_SECONDS
    proposals[proposal_id] = {
        'name': name,
        'subscribers': [],
        'timer': asyncio.create_task(proposal_timer(proposal_id, name))
    }

    response_message = f"A member proposal for {name} was added, set to pass <t:{deadline}:R>"

    await interaction.response.send_message("Proposal created successfully.", ephemeral=True)

    output_channel = await get_output_channel()
    if output_channel:
        await output_channel.send(response_message, view=ProposalView(proposal_id))
    else:
        await interaction.followup.send(f"Warning: Couldn't find the '{OUTPUT_CHANNEL_NAME}' channel to announce the proposal.", ephemeral=True)

async def veto_proposal(interaction: discord.Interaction, proposal_id: str):
    proposal = proposals.get(proposal_id.lower())
    if not proposal:
        await interaction.response.send_message("This proposal no longer exists.", ephemeral=True)
        return

    proposal['timer'].cancel()
    await notify_subscribers(proposal, "vetoed")
    del proposals[proposal_id.lower()]

    output_channel = await get_output_channel()
    if output_channel:
        await output_channel.send(f"The proposal for {proposal['name']} has been vetoed.")
    else:
        await interaction.followup.send(f"Error: Couldn't find the '{OUTPUT_CHANNEL_NAME}' channel.", ephemeral=True)

async def notify_subscribers(proposal, status):
    for user_id in proposal['subscribers']:
        user = await bot.fetch_user(user_id)
        if user:
            await user.send(f"The proposal for {proposal['name']} has been {status}.")

async def proposal_timer(proposal_id, name):
    await asyncio.sleep(TIMEOUT_SECONDS)
    proposal = proposals.get(proposal_id.lower())
    if proposal:
        await notify_subscribers(proposal, "passed")
        output_channel = await get_output_channel()
        if output_channel:
            await output_channel.send(f"The proposal for {name} has passed.")
        del proposals[proposal_id.lower()]

# Load token from environment variable
if __name__ == "__main__":
    # Load token from environment variable

    def is_running_on_gcp():
        return os.environ.get('GAE_ENV', '').startswith('standard')

    TOKEN = '';
    if is_running_on_gcp():
        TOKEN = os.environ.get("DISCORD_TOKEN", "Specified environment variable is not set.")
    else:
        load_dotenv()
        TOKEN = os.getenv("DISCORD_TOKEN")
        OUTPUT_CHANNEL_NAME = os.getenv("OUTPUT_CHANNEL_NAME")
        SERVER_ID = int(os.getenv("SERVER_ID"))
        TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS"))

    import threading

    def run_bot():
        bot.run(TOKEN)

    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()

    app.run(host='0.0.0.0', port=8080)
