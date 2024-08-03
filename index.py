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

def create_app():
    app = Flask(__name__)
    return app

ssl._create_default_https_context = ssl._create_unverified_context

SERVER_ID = 977606746317144154
OUTPUT_CHANNEL_NAME = "fedex"
TIMEOUT_SECONDS = 172800

intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='/', intents=intents)

    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

subscribed_users = set()
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


@bot.tree.command(name="sub", description="Subscribe to new proposal notifications")
async def sub(interaction: discord.Interaction):
    if interaction.user.id in subscribed_users:
        await interaction.response.send_message("You are already subscribed to new proposal notifications.", ephemeral=True)
    else:
        subscribed_users.add(interaction.user.id)
        await interaction.response.send_message("You have subscribed to new proposal notifications.", ephemeral=True)

@bot.tree.command(name="unsub", description="Unsubscribe from new proposal notifications")
async def unsub(interaction: discord.Interaction):
    if interaction.user.id not in subscribed_users:
        await interaction.response.send_message("You are not currently subscribed to new proposal notifications.", ephemeral=True)
    else:
        subscribed_users.discard(interaction.user.id)
        await interaction.response.send_message("You have unsubscribed from new proposal notifications.", ephemeral=True)

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
        proposal_message = await output_channel.send(response_message, view=ProposalView(proposal_id))
        proposals[proposal_id]['message_id'] = proposal_message.id  # Store the message ID

        # Notify subscribed users
        for user_id in subscribed_users:
            user = await bot.fetch_user(user_id)
            if user:
                message_link = f"https://discord.com/channels/{SERVER_ID}/{output_channel.id}/{proposal_message.id}"
                await user.send(f"A new proposal for {name} has been created. View it here: {message_link}")
    else:
        await interaction.followup.send(f"Warning: Couldn't find the '{OUTPUT_CHANNEL_NAME}' channel to announce the proposal.", ephemeral=True)

async def veto_proposal(interaction: discord.Interaction, proposal_id: str):
    proposal = proposals.get(proposal_id.lower())
    if not proposal:
        await interaction.response.send_message("This proposal no longer exists.", ephemeral=True)
        return

    proposal['timer'].cancel()
    await notify_subscribers(proposal, "vetoed")

    output_channel = await get_output_channel()
    if output_channel:
        if 'message_id' in proposal:
            try:
                message = await output_channel.fetch_message(proposal['message_id'])
                await message.edit(content=f"The proposal for {proposal['name']} has been vetoed.", view=None)
            except discord.NotFound:
                await output_channel.send(f"The proposal for {proposal['name']} has been vetoed.")
        else:
            await output_channel.send(f"The proposal for {proposal['name']} has been vetoed.")
    else:
        await interaction.followup.send(f"Error: Couldn't find the '{OUTPUT_CHANNEL_NAME}' channel.", ephemeral=True)

@bot.tree.command(name="help", description="Get information about available commands")
async def help_command(interaction: discord.Interaction):
    help_text = """
**Available Commands:**

1. `/new <name>` - Propose a new member
   Usage: `/new John Doe`
   Description: Creates a new member proposal that will pass after 48 hours unless vetoed.

2. `/sub` - Subscribe to new proposal notifications
   Usage: `/sub`
   Description: You'll receive a DM whenever a new proposal is created.

3. `/unsub` - Unsubscribe from new proposal notifications
   Usage: `/unsub`
   Description: Stop receiving DMs about new proposals.

4. `/help` - Display this help message
   Usage: `/help`
   Description: Shows information about all available commands.

**Additional Features:**
- Use the "Veto" button on a proposal message to veto it.
- Use the "Subscribe" button on a proposal message to receive updates about that specific proposal.
"""
    await interaction.response.send_message(help_text, ephemeral=True)

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
            if 'message_id' in proposal:
                try:
                    message = await output_channel.fetch_message(proposal['message_id'])
                    await message.edit(content=f"The proposal for {name} has passed.", view=None)
                except discord.NotFound:
                    await output_channel.send(f"The proposal for {name} has passed.")
            else:
                await output_channel.send(f"The proposal for {name} has passed.")
        del proposals[proposal_id.lower()]


def setup_bot():
    import threading
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    global OUTPUT_CHANNEL_NAME
    OUTPUT_CHANNEL_NAME = os.getenv("OUTPUT_CHANNEL_NAME")
    global SERVER_ID
    SERVER_ID = int(os.getenv("SERVER_ID"))
    global TIMEOUT_SECONDS
    TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS"))

    bot_thread = threading.Thread(target=lambda: bot.run(token))
    bot_thread.start()


if __name__ == "__main__":
    app = create_app()
    setup_bot()
    app.run(host='0.0.0.0', port=8080, debug=True)

