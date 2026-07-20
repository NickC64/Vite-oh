import logging
from datetime import timedelta
from typing import Any

from viteoh.commands import HELP_TEXT
from viteoh.components import parse_component_id
from viteoh.config import Settings
from viteoh.discord_api import DiscordAPIError, DiscordClient
from viteoh.domain import GuildConfig, Proposal, ProposalStatus, utcnow
from viteoh.repository import Repository, normalize_name
from viteoh.tasks import TaskDispatcher

logger = logging.getLogger(__name__)

ADMINISTRATOR = 1 << 3
MANAGE_GUILD = 1 << 5
MIN_DURATION_MINUTES = 1
MAX_DURATION_MINUTES = 10080


class InteractionProcessor:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        tasks: TaskDispatcher,
        discord: DiscordClient,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.tasks = tasks
        self.discord = discord

    async def process(self, payload: dict[str, Any]) -> None:
        interaction_type = int(payload.get("type", 0))
        token = str(payload.get("token", ""))
        user_id = _user_id(payload)
        guild_id = str(payload.get("guild_id", ""))
        try:
            if interaction_type == 2:
                content = await self._command(payload, guild_id, user_id)
            elif interaction_type == 3:
                content = await self._component(payload, guild_id, user_id)
            else:
                content = "Unsupported interaction."
            await self.discord.edit_interaction_response(token, content)
        except DiscordAPIError as exc:
            if exc.retryable:
                raise
            logger.warning(
                "Discord rejected an interaction response",
                extra={
                    "status_code": exc.status_code,
                    "interaction_id": payload.get("id"),
                },
            )

    async def _command(
        self, payload: dict[str, Any], guild_id: str, user_id: str
    ) -> str:
        data = payload.get("data") or {}
        command = data.get("name")
        options = {
            option["name"]: option.get("value") for option in data.get("options", [])
        }
        if command == "setup":
            return await self._setup(payload, guild_id, user_id, options)
        if command == "help":
            return HELP_TEXT

        config = await self.repository.get_guild_config(guild_id)
        if not config:
            return (
                "This server has not been configured. A member with Manage Server "
                "must run `/setup` first."
            )
        if command == "new":
            return await self._new(
                str(payload["id"]), str(options.get("name", "")), config
            )
        if command == "sub":
            changed = await self.repository.set_guild_subscription(
                guild_id, user_id, True
            )
            return (
                "You have subscribed to new proposal notifications."
                if changed
                else "You are already subscribed to new proposal notifications."
            )
        if command == "unsub":
            changed = await self.repository.set_guild_subscription(
                guild_id, user_id, False
            )
            return (
                "You have unsubscribed from new proposal notifications."
                if changed
                else "You are not currently subscribed to new proposal notifications."
            )
        if command == "view":
            proposals = await self.repository.list_active(guild_id)
            if not proposals:
                return "There are no active proposals."
            ordered = sorted(proposals, key=lambda proposal: proposal.deadline_at)
            return "Current proposals:\n" + "\n".join(
                (
                    f"• {proposal.display_name} — "
                    f"<t:{int(proposal.deadline_at.timestamp())}:R>"
                )
                for proposal in ordered
            )
        if command == "delete":
            if not self._is_admin(payload, user_id):
                return "You need Manage Server permission to use this command."
            return await self._delete(guild_id, str(options.get("name", "")))
        return "Unknown command."

    async def _setup(
        self,
        payload: dict[str, Any],
        guild_id: str,
        user_id: str,
        options: dict[str, Any],
    ) -> str:
        if not self._is_admin(payload, user_id):
            return "You need Manage Server permission to configure this server."
        current = await self.repository.get_guild_config(guild_id)
        channel_value = options.get("channel")
        duration_value = options.get("duration_minutes")
        if channel_value is None and duration_value is None:
            if not current:
                return (
                    "This server is not configured. Provide both `channel` and "
                    "`duration_minutes`."
                )
            return _format_config(current)
        if not current and (channel_value is None or duration_value is None):
            return "First-time setup requires both `channel` and `duration_minutes`."
        if channel_value is None:
            assert current is not None
            channel_id = current.output_channel_id
        else:
            channel_id = str(channel_value)
        if duration_value is None:
            assert current is not None
            duration_minutes = current.proposal_timeout_seconds // 60
        else:
            duration_minutes = int(duration_value)
        if not MIN_DURATION_MINUTES <= duration_minutes <= MAX_DURATION_MINUTES:
            return "Voting duration must be between 1 and 10,080 minutes."

        guild_name = current.guild_name if current else guild_id
        if channel_value is not None:
            try:
                guild_name = await self.discord.validate_output_channel(
                    guild_id, channel_id
                )
            except DiscordAPIError as exc:
                if exc.retryable:
                    raise
                return str(exc)
        config = await self.repository.set_guild_config(
            guild_id,
            guild_name,
            channel_id,
            duration_minutes * 60,
            user_id,
            utcnow(),
        )
        return "Configuration saved.\n" + _format_config(config)

    async def _new(
        self, interaction_id: str, display_name: str, config: GuildConfig
    ) -> str:
        display_name = " ".join(display_name.split())
        try:
            normalized = normalize_name(display_name)
        except ValueError as exc:
            return str(exc)
        now = utcnow()
        deadline = now + timedelta(seconds=config.proposal_timeout_seconds)
        result = await self.repository.create_proposal(
            interaction_id,
            config.guild_id,
            config.guild_name,
            config.output_channel_id,
            display_name,
            normalized,
            now,
            deadline,
        )
        if result.duplicate_name or not result.proposal:
            return f"A proposal for '{display_name}' already exists."
        proposal = result.proposal

        task_name = await self.tasks.ensure_deadline(proposal.id, proposal.deadline_at)
        proposal = (
            await self.repository.mark_task_scheduled(proposal.id, task_name)
            or proposal
        )
        if not proposal.message_id:
            try:
                message_id = await self.discord.create_proposal_announcement(proposal)
            except DiscordAPIError as exc:
                if exc.retryable:
                    raise
                transition_result = await self.repository.transition(
                    proposal.id, ProposalStatus.DELETED, utcnow()
                )
                await self.tasks.delete(proposal.deadline_task_name)
                if transition_result.proposal:
                    await self.repository.mark_announcement_synced(proposal.id)
                    await self.repository.mark_effects_complete(proposal.id)
                return (
                    "I could not post in the configured proposal channel. "
                    "Run `/setup` after fixing my channel permissions."
                )
            proposal = (
                await self.repository.set_message_id(proposal.id, message_id)
                or proposal
            )
        await self._notify_created(proposal)
        return (
            f"Proposal created successfully. It will pass "
            f"<t:{int(proposal.deadline_at.timestamp())}:R> unless vetoed."
        )

    async def _delete(self, guild_id: str, display_name: str) -> str:
        try:
            normalized = normalize_name(display_name)
        except ValueError as exc:
            return str(exc)
        proposal = next(
            (
                item
                for item in await self.repository.list_active(guild_id)
                if item.normalized_name == normalized
            ),
            None,
        )
        if not proposal:
            return f"No active proposal found for '{display_name}'."
        result = await self.repository.transition(
            proposal.id, ProposalStatus.DELETED, utcnow()
        )
        if result.proposal:
            await self.tasks.delete(result.proposal.deadline_task_name)
            await self.sync_terminal_effects(result.proposal)
        return f"Proposal for '{proposal.display_name}' has been deleted."

    async def _component(
        self, payload: dict[str, Any], guild_id: str, user_id: str
    ) -> str:
        parsed = parse_component_id(
            str((payload.get("data") or {}).get("custom_id", ""))
        )
        if not parsed:
            return "This control is no longer valid."
        action, proposal_id = parsed
        proposal = await self.repository.get_proposal(proposal_id)
        if not proposal or proposal.status is not ProposalStatus.ACTIVE:
            return "This proposal is no longer active."
        if proposal.guild_id != guild_id:
            return "This control belongs to a proposal in another server."
        if action == "subscribe":
            added = await self.repository.add_proposal_subscription(
                proposal_id, user_id
            )
            return (
                "You have subscribed to updates for this proposal."
                if added
                else "You are already subscribed to this proposal."
            )
        if action == "confirm-veto":
            result = await self.repository.transition(
                proposal_id, ProposalStatus.VETOED, utcnow()
            )
            if result.reason == "deadline_elapsed":
                return (
                    "The deadline has elapsed; this proposal can no longer be vetoed."
                )
            if not result.changed or not result.proposal:
                return "This proposal is no longer active."
            await self.tasks.delete(result.proposal.deadline_task_name)
            await self.sync_terminal_effects(result.proposal)
            return "You have vetoed the proposal anonymously."
        return "This control is no longer valid."

    async def finalize(self, proposal_id: str) -> str:
        result = await self.repository.transition(
            proposal_id, ProposalStatus.PASSED, utcnow()
        )
        if result.reason == "not_due":
            return "not_due"
        if result.proposal and (result.changed or not result.proposal.effects_complete):
            await self.sync_terminal_effects(result.proposal)
        return result.reason

    async def reconcile(self) -> dict[str, int]:
        active = await self.repository.list_active()
        now = utcnow()
        finalized = 0
        repaired = 0
        effects_retried = 0
        for proposal in active:
            if now >= proposal.deadline_at:
                await self.finalize(proposal.id)
                finalized += 1
                continue
            task_exists = bool(
                proposal.deadline_task_name
                and await self.tasks.exists(proposal.deadline_task_name)
            )
            if not task_exists:
                repair_suffix = str(int(now.timestamp()) // 300)
                task_name = await self.tasks.ensure_deadline(
                    proposal.id,
                    proposal.deadline_at,
                    repair_suffix=repair_suffix,
                )
                await self.repository.mark_task_scheduled(proposal.id, task_name)
                repaired += 1
        for proposal in await self.repository.list_pending_terminal_effects():
            await self.sync_terminal_effects(proposal)
            effects_retried += 1
        result = {
            "active": len(active),
            "finalized": finalized,
            "repaired": repaired,
            "effects_retried": effects_retried,
        }
        if finalized or repaired or effects_retried:
            logger.warning("Reconciliation repaired proposal state", extra=result)
        return result

    async def sync_terminal_effects(self, proposal: Proposal) -> None:
        if not proposal.announcement_synced:
            await self.discord.sync_terminal_announcement(proposal)
            await self.repository.mark_announcement_synced(proposal.id)
        subscribers = await self.repository.proposal_subscribers(proposal.id)
        event = proposal.status.value
        outcome = {
            ProposalStatus.PASSED: "passed",
            ProposalStatus.VETOED: "been vetoed",
            ProposalStatus.DELETED: "been deleted by an admin",
        }[proposal.status]
        link = _proposal_link(proposal)
        text = (
            f"In **{proposal.guild_name}**, the proposal for "
            f"{proposal.display_name} has {outcome}: {link}"
        )
        await self._notify(proposal, event, subscribers, text)
        await self.repository.mark_effects_complete(proposal.id)

    async def _notify_created(self, proposal: Proposal) -> None:
        subscribers = await self.repository.guild_subscribers(proposal.guild_id)
        link = _proposal_link(proposal)
        await self._notify(
            proposal,
            "created",
            subscribers,
            f"In **{proposal.guild_name}**, a new proposal for "
            f"{proposal.display_name} has been created: {link}",
        )

    async def _notify(
        self,
        proposal: Proposal,
        event: str,
        subscribers: Any,
        text: str,
    ) -> None:
        retryable_error: DiscordAPIError | None = None
        for user_id in subscribers:
            if await self.repository.notification_delivered(
                proposal.id, event, str(user_id)
            ):
                continue
            try:
                await self.discord.send_dm(
                    str(user_id),
                    text,
                    event_key=f"{proposal.id}:{event}:{user_id}",
                )
                await self.repository.mark_notification_delivered(
                    proposal.id, event, str(user_id)
                )
            except DiscordAPIError as exc:
                if exc.retryable:
                    retryable_error = exc
                else:
                    logger.info(
                        "Skipping undeliverable Discord DM",
                        extra={"status_code": exc.status_code, "event": event},
                    )
                    await self.repository.mark_notification_delivered(
                        proposal.id, event, str(user_id)
                    )
        if retryable_error:
            raise retryable_error

    def _is_admin(self, payload: dict[str, Any], user_id: str) -> bool:
        if user_id == self.settings.discord_owner_user_id:
            return True
        raw_permissions = str((payload.get("member") or {}).get("permissions", "0"))
        try:
            permissions = int(raw_permissions)
        except ValueError:
            return False
        return bool(permissions & (ADMINISTRATOR | MANAGE_GUILD))


def _user_id(payload: dict[str, Any]) -> str:
    member = payload.get("member") or {}
    user = member.get("user") or payload.get("user") or {}
    return str(user.get("id", ""))


def _format_config(config: GuildConfig) -> str:
    minutes = config.proposal_timeout_seconds // 60
    return (
        f"Proposal channel: <#{config.output_channel_id}>\n"
        f"Voting duration: {minutes:,} minute{'s' if minutes != 1 else ''}"
    )


def _proposal_link(proposal: Proposal) -> str:
    return (
        f"https://discord.com/channels/{proposal.guild_id}/"
        f"{proposal.output_channel_id}/{proposal.message_id}"
    )
