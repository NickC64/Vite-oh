import random
import ssl
import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import os
from flask import Flask
from dotenv import load_dotenv
from models import Session, Proposal, User
import logging
from datetime import datetime
import time
import atexit
import calendar
from sqlalchemy.orm import joinedload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("discord")
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


class CommandTree(app_commands.CommandTree):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        guild = await self.bot.fetch_guild(SERVER_ID)
        if guild is None:
            return False
        member = await guild.fetch_member(interaction.user.id)
        return member is not None


class Bot(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(
            command_prefix="/", intents=intents, tree_cls=CommandTree, **kwargs
        )

    async def setup_hook(self):
        self.tree.bot = self
        await self.tree.sync()


load_dotenv()
bot = Bot(owner_id=int(os.getenv("OWNER_ID")))

proposals = {}
subscribed_users = set()
server_start_time = time.time()


def save_server_shutdown_time():
    with open("server_shutdown_time.txt", "w") as f:
        f.write(str(time.time()))


atexit.register(save_server_shutdown_time)


def get_server_downtime():
    try:
        with open("server_shutdown_time.txt", "r") as f:
            shutdown_time = f.read()
        return max(0, int(server_start_time - float(shutdown_time)))
    except FileNotFoundError:
        return 0


def get_proposals():
    session = Session()
    proposals_db = session.query(Proposal).all()
    current_time = int(time.time())
    downtime = get_server_downtime()
    for proposal in proposals_db:
        remaining_time = proposal.deadline - current_time
        if remaining_time > 0:
            proposals[proposal.id] = {
                "name": proposal.name,
                "subscribers": [
                    subscriber.id for subscriber in proposal.subscribers
                ],
                "timer": asyncio.create_task(
                    proposal_timer(proposal.id, proposal.name, remaining_time)
                ),
                "message_id": proposal.message_id,
            }
        else:
            asyncio.create_task(handle_expired_proposal(proposal, downtime))
    session.close()
    logger.info(f"Loaded {len(proposals)} active proposals from the database")
    return proposals


async def handle_expired_proposal(proposal, downtime):
    extension_time = (
        proposal.deadline - calendar.timegm(proposal.created_at.timetuple())
    ) // 2
    new_deadline = int(time.time()) + extension_time

    session = Session()
    try:
        db_proposal = (
            session.query(Proposal)
            .options(joinedload(Proposal.subscribers))
            .filter_by(id=proposal.id)
            .first()
        )
        if db_proposal:
            db_proposal.deadline = new_deadline
            session.commit()

            proposal_dict = {
                "name": db_proposal.name,
                "subscribers": [
                    subscriber.id for subscriber in db_proposal.subscribers
                ],
                "message_id": db_proposal.message_id,
            }

            def get_ext_time_str(t):
                if t < 60:
                    return f"{extension_time} seconds"
                elif t < 360:
                    return f"{round(extension_time / 60, 2)} minutes"
                else:
                    return f"{round(extension_time / 360, 2)} hours"

            ext_time_str = get_ext_time_str(extension_time)
            await notify_subscribers(
                proposal_dict,
                f"extended by {ext_time_str} due to server downtime",
            )

            global proposals
            proposals[db_proposal.id] = {
                "name": db_proposal.name,
                "subscribers": [
                    subscriber.id for subscriber in db_proposal.subscribers
                ],
                "timer": asyncio.create_task(
                    proposal_timer(
                        db_proposal.id, db_proposal.name, extension_time
                    )
                ),
                "message_id": db_proposal.message_id,
            }

            output_channel = await get_output_channel()
            if output_channel and db_proposal.message_id:
                try:
                    message = await output_channel.fetch_message(
                        db_proposal.message_id
                    )
                    view = ProposalView(db_proposal.id)
                    await message.edit(
                        content=f"The proposal for {db_proposal.name} has been extended to <t:{new_deadline}:R> due to server downtime.",
                        view=view,
                    )
                    bot.add_view(view)
                except discord.NotFound:
                    view = ProposalView(db_proposal.id)
                    new_message = await output_channel.send(
                        f"The proposal for {db_proposal.name} has been extended to <t:{new_deadline}:f> due to server downtime.",
                        view=view,
                    )
                    bot.add_view(view)
                    proposals[db_proposal.id]["message_id"] = new_message.id
                    db_proposal.message_id = new_message.id
                    session.commit()
    finally:
        session.close()


def get_subscribed_users():
    session = Session()
    subscribed_users = set(
        user.id
        for user in session.query(User).filter_by(subscribed_to_all=True).all()
    )
    session.close()
    logger.info(
        f"Loaded {len(subscribed_users)} subscribed users from the database"
    )
    return subscribed_users


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user.name}")
    global proposals, subscribed_users
    proposals.update(get_proposals())
    subscribed_users.update(get_subscribed_users())
    for proposal_id in proposals:
        bot.add_view(ProposalView(proposal_id))


async def get_output_channel():
    guild = bot.get_guild(SERVER_ID)
    if guild:
        return discord.utils.get(guild.channels, name=OUTPUT_CHANNEL_NAME)
    return None


def is_guild_member():
    async def predicate(interaction: discord.Interaction):
        guild = bot.get_guild(SERVER_ID)
        if guild is None:
            return False
        member = guild.get_member(interaction.user.id)
        return member is not None

    return app_commands.check(predicate)


class VetoView(discord.ui.View):
    def __init__(self, proposal_id):
        super().__init__(timeout=None)
        self.proposal_id = proposal_id

    @discord.ui.button(
        label="Yes", style=discord.ButtonStyle.danger, custom_id="yes"
    )
    async def yes_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await veto_proposal(interaction, self.proposal_id)
        await interaction.response.send_message(
            "You have vetoed the proposal.", ephemeral=True
        )

    @discord.ui.button(
        label="No", style=discord.ButtonStyle.secondary, custom_id="no"
    )
    async def no_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_message(
            "Veto cancelled.", ephemeral=True
        )


class ProposalView(discord.ui.View):
    def __init__(self, proposal_id):
        super().__init__(timeout=None)
        self.proposal_id = proposal_id

    @discord.ui.button(
        label="Veto", style=discord.ButtonStyle.danger, custom_id="veto"
    )
    async def veto_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        global proposals
        proposal = proposals.get(self.proposal_id.lower())
        if not proposal:
            await interaction.response.send_message(
                "This proposal no longer exists.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Are you sure you want to veto the proposal for {proposal['name']}?",
            view=VetoView(self.proposal_id),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Subscribe",
        style=discord.ButtonStyle.primary,
        custom_id="subscribe",
    )
    async def subscribe_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        global proposals
        proposal = proposals.get(self.proposal_id.lower())
        if not proposal:
            await interaction.response.send_message(
                "This proposal no longer exists.", ephemeral=True
            )
            return
        if interaction.user.id not in proposal["subscribers"]:
            session = Session()
            db_proposal = (
                session.query(Proposal)
                .filter_by(id=self.proposal_id.lower())
                .first()
            )
            user = session.query(User).filter_by(id=interaction.user.id).first()
            if not user:
                user = User(id=interaction.user.id)
                session.add(user)
            if db_proposal:
                db_proposal.subscribers.append(user)
                session.commit()
            session.close()

            proposal["subscribers"].append(interaction.user.id)
            await interaction.response.send_message(
                "You have subscribed to updates for this proposal.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "You are already subscribed to this proposal.", ephemeral=True
            )


@bot.tree.command(
    name="sub", description="Subscribe to new proposal notifications"
)
async def sub(interaction: discord.Interaction):

    if interaction.user.id in subscribed_users:
        await interaction.response.send_message(
            "You are already subscribed to new proposal notifications.",
            ephemeral=True,
        )
    else:
        session = Session()
        user = session.query(User).filter_by(id=interaction.user.id).first()
        if user:
            user.subscribed_to_all = True
        else:
            new_user = User(id=interaction.user.id, subscribed_to_all=True)
            session.add(new_user)
        session.commit()
        session.close()

        subscribed_users.add(interaction.user.id)
        await interaction.response.send_message(
            "You have subscribed to new proposal notifications.", ephemeral=True
        )


@bot.tree.command(
    name="unsub", description="Unsubscribe from new proposal notifications"
)
async def unsub(interaction: discord.Interaction):
    if interaction.user.id not in subscribed_users:
        await interaction.response.send_message(
            "You are not currently subscribed to new proposal notifications.",
            ephemeral=True,
        )
    else:
        session = Session()
        user = session.query(User).filter_by(id=interaction.user.id).first()
        if user:
            user.subscribed_to_all = False
            session.commit()
        session.close()

        subscribed_users.discard(interaction.user.id)
        await interaction.response.send_message(
            "You have unsubscribed from new proposal notifications.",
            ephemeral=True,
        )


@bot.tree.command(name="new", description="Propose a new member")
@app_commands.describe(name="Name of the proposed member")
async def new(interaction: discord.Interaction, name: str):
    proposal_id = name.lower()
    global proposals

    # Check if proposal already exists in memory
    if proposal_id in proposals:
        await interaction.response.send_message(
            f"A proposal for '{name}' already exists.", ephemeral=True
        )
        return

    deadline = int(time.time()) + TIMEOUT_SECONDS

    session = Session()
    try:
        # Check if proposal already exists in database
        existing_proposal = (
            session.query(Proposal).filter_by(id=proposal_id).first()
        )
        if existing_proposal:
            await interaction.response.send_message(
                f"A proposal for '{name}' already exists.", ephemeral=True
            )
            return

        new_proposal = Proposal(
            id=proposal_id,
            name=name,
            deadline=deadline,
            created_at=datetime.utcnow(),
        )
        session.add(new_proposal)
        session.commit()

        remaining_time = TIMEOUT_SECONDS

        proposals[proposal_id] = {
            "name": name,
            "subscribers": [],
            "timer": asyncio.create_task(
                proposal_timer(proposal_id, name, remaining_time)
            ),
        }

        response_message = f"A member proposal for {name} was added, set to pass <t:{deadline}:R>"

        await interaction.response.send_message(
            "Proposal created successfully.", ephemeral=True
        )

        output_channel = await get_output_channel()
        if output_channel:
            view = ProposalView(proposal_id)
            proposal_message = await output_channel.send(
                response_message, view=view
            )
            bot.add_view(view)
            proposals[proposal_id]["message_id"] = proposal_message.id

            db_proposal = (
                session.query(Proposal).filter_by(id=proposal_id).first()
            )
            if db_proposal:
                db_proposal.message_id = proposal_message.id
                session.commit()

            for user_id in subscribed_users:
                user = await bot.fetch_user(user_id)
                if user:
                    message_link = f"https://discord.com/channels/{SERVER_ID}/{output_channel.id}/{proposal_message.id}"
                    await user.send(
                        f"A new proposal for {name} has been created. View it here: {message_link}"
                    )
        else:
            await interaction.followup.send(
                f"Warning: Couldn't find the '{OUTPUT_CHANNEL_NAME}' channel to announce the proposal.",
                ephemeral=True,
            )
    except Exception as e:
        logger.error(f"Error creating new proposal: {str(e)}")
        await interaction.followup.send(
            "An error occurred while creating the proposal. If the proposal was still posted, veto it and create another one. If it still persists, make a bug report",
            ephemeral=True,
        )
    finally:
        session.close()


async def veto_proposal(interaction: discord.Interaction, proposal_id: str):
    global proposals
    proposal = proposals.get(proposal_id.lower())
    if not proposal:
        await interaction.response.send_message(
            "This proposal no longer exists.", ephemeral=True
        )
        return

    proposal["timer"].cancel()
    await notify_subscribers(proposal, "vetoed")

    session = Session()
    db_proposal = (
        session.query(Proposal).filter_by(id=proposal_id.lower()).first()
    )
    if db_proposal:
        session.delete(db_proposal)
        session.commit()
    session.close()

    proposals.pop(proposal_id.lower(), None)

    output_channel = await get_output_channel()
    if output_channel:
        if "message_id" in proposal:
            try:
                message = await output_channel.fetch_message(
                    proposal["message_id"]
                )
                await message.edit(
                    content=f"The proposal for {proposal['name']} has been vetoed.",
                    view=None,
                )
            except discord.NotFound:
                await output_channel.send(
                    f"The proposal for {proposal['name']} has been vetoed."
                )
        else:
            await output_channel.send(
                f"The proposal for {proposal['name']} has been vetoed."
            )
    else:
        await interaction.followup.send(
            f"Error: Couldn't find the '{OUTPUT_CHANNEL_NAME}' channel.",
            ephemeral=True,
        )


def is_owner():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.id == bot.owner_id

    return app_commands.check(predicate)


@bot.tree.command(name="view", description="View all current proposals")
async def view_proposals(interaction: discord.Interaction):
    if not proposals:
        await interaction.response.send_message(
            "There are no active proposals.", ephemeral=True
        )
        return

    proposal_list = "\n".join(
        [
            f"{proposal['name']} (ID: {proposal_id})"
            for proposal_id, proposal in proposals.items()
        ]
    )
    await interaction.response.send_message(
        f"Current proposals:\n{proposal_list}", ephemeral=True
    )


@bot.tree.command(
    name="delete", description="(Dev command) Delete a specific proposal"
)
@is_owner()
@app_commands.describe(name="Name of the member being proposed")
async def delete_proposal(interaction: discord.Interaction, name: str):
    proposal_id = name.lower()
    if proposal_id not in proposals:
        await interaction.response.send_message(
            f"No proposal found for '{name}'.", ephemeral=True
        )
        return

    proposal = proposals[proposal_id]
    proposal["timer"].cancel()
    await notify_subscribers(proposal, "deleted by admin")

    session = Session()
    db_proposal = session.query(Proposal).filter_by(id=proposal_id).first()
    if db_proposal:
        session.delete(db_proposal)
        session.commit()
    session.close()

    proposals.pop(proposal_id)

    output_channel = await get_output_channel()
    if output_channel and "message_id" in proposal:
        try:
            message = await output_channel.fetch_message(proposal["message_id"])
            await message.edit(
                content=f"The proposal for {proposal['name']} has been deleted by an admin.",
                view=None,
            )
        except discord.NotFound:
            await output_channel.send(
                f"The proposal for {proposal['name']} has been deleted by an admin."
            )

    await interaction.response.send_message(
        f"Proposal for '{name}' has been deleted.", ephemeral=True
    )


@bot.tree.command(
    name="help", description="Get information about available commands"
)
async def help_command(interaction: discord.Interaction):
    try:
        with open("README.md", "r") as file:
            content = file.read()

        help_text = content.split("# Bot Commands", 1)[-1].strip()

        # Discord 2000-word limit (iirc)
        if len(help_text) > 2000:
            chunks = [
                help_text[i : i + 2000] for i in range(0, len(help_text), 2000)
            ]
            for chunk in chunks:
                await interaction.followup.send(chunk)
            await interaction.response.send_message(
                "Help information sent in multiple messages due to length.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(help_text, ephemeral=True)
    except FileNotFoundError:
        await interaction.response.send_message(
            "Error: README.md file not found.", ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            f"An error occurred while reading the help information: {str(e)}",
            ephemeral=True,
        )


async def notify_subscribers(proposal, status):
    subscribers = (
        proposal["subscribers"]
        if isinstance(proposal, dict)
        else [subscriber.id for subscriber in proposal.subscribers]
    )
    name = proposal["name"] if isinstance(proposal, dict) else proposal.name
    for user_id in subscribers:
        user = await bot.fetch_user(user_id)
        if user:
            await user.send(f"The proposal for {name} has been {status}.")


async def proposal_timer(proposal_id, name, remaining_time):
    await asyncio.sleep(remaining_time)
    global proposals
    proposal = proposals.get(proposal_id.lower())
    if proposal:
        await notify_subscribers(proposal, "passed")
        output_channel = await get_output_channel()
        if output_channel:
            if "message_id" in proposal:
                try:
                    message = await output_channel.fetch_message(
                        proposal["message_id"]
                    )
                    await message.edit(
                        content=f"The proposal for {name} has passed.",
                        view=None,
                    )
                except discord.NotFound:
                    await output_channel.send(
                        f"The proposal for {name} has passed."
                    )
            else:
                await output_channel.send(
                    f"The proposal for {name} has passed."
                )

        session = Session()
        db_proposal = (
            session.query(Proposal).filter_by(id=proposal_id.lower()).first()
        )
        if db_proposal:
            session.delete(db_proposal)
            session.commit()
        session.close()

        proposals.pop(proposal_id.lower(), None)


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
    app.run(host="0.0.0.0", port=8080, debug=True)
