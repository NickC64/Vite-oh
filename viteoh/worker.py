import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from viteoh.commands import HELP_TEXT, command_path_and_options
from viteoh.components import link_button, modal_values, parse_component_id
from viteoh.config import Settings
from viteoh.discord_api import DiscordAPIError, DiscordClient
from viteoh.domain import (
    GuildConfig,
    Proposal,
    ProposalStatus,
    ProposalType,
    utcnow,
)
from viteoh.proposal_types import validate_type_fields
from viteoh.repository import Repository, normalize_title
from viteoh.tasks import TaskDispatcher
from viteoh.workspace_security import SignedTokenCodec

logger = logging.getLogger(__name__)
CURRENT_RENDER_VERSION = 1

ADMINISTRATOR = 1 << 3
MANAGE_GUILD = 1 << 5
MIN_DURATION_MINUTES = 1
MAX_DURATION_MINUTES = 10080


@dataclass(frozen=True, slots=True)
class InteractionReply:
    content: str
    components: list[dict[str, object]] | None = None


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
        self.workspace_codec = SignedTokenCodec(settings.workspace_signing_secret)

    async def process(self, payload: dict[str, Any]) -> None:
        interaction_type = int(payload.get("type", 0))
        token = str(payload.get("token", ""))
        user_id = _user_id(payload)
        guild_id = str(payload.get("guild_id", ""))
        try:
            if interaction_type == 2:
                result = await self._command(payload, guild_id, user_id)
            elif interaction_type == 3:
                result = await self._component(payload, guild_id, user_id)
            elif interaction_type == 5:
                result = await self._modal(payload, guild_id, user_id)
            else:
                result = "Unsupported interaction."
            reply = (
                result
                if isinstance(result, InteractionReply)
                else InteractionReply(result)
            )
            parsed_component = (
                parse_component_id(
                    str((payload.get("data") or {}).get("custom_id", ""))
                )
                if interaction_type == 3
                else None
            )
            await self.discord.edit_interaction_response(
                token,
                reply.content,
                clear_components=bool(
                    parsed_component and parsed_component[1] == "confirm-delete"
                ),
                components=reply.components,
            )
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
    ) -> str | InteractionReply:
        path, options = command_path_and_options(payload.get("data") or {})
        if path == ("proposal",):
            if not guild_id:
                return await self._dm_workspace_reply(user_id)
            return await self._workspace_reply(user_id, guild_id)
        if path == ("proposal", "configure"):
            return await self._setup(payload, guild_id, user_id, options)
        if path == ("proposal", "help"):
            return HELP_TEXT

        config = await self.repository.get_guild_config(guild_id)
        if not config:
            return (
                "This server has not been configured. A member with Manage Server "
                "must run `/proposal configure` first."
            )
        if path == ("proposal", "list"):
            proposals = await self.repository.list_active(guild_id)
            if not proposals:
                return "There are no active proposals."
            ordered = sorted(proposals, key=lambda proposal: proposal.deadline_at)
            return "**Active proposals**\n" + "\n".join(
                (
                    f"• [{proposal.title}]({_proposal_link(proposal)}) "
                    + (f"· {proposal.type_name} " if proposal.type_name else "")
                    + "· "
                    f"<t:{int(proposal.deadline_at.timestamp())}:R>"
                )
                for proposal in ordered
            )
        if path == ("proposal", "create"):
            type_id = str(options.get("type") or "")
            proposal_type = (
                await self.repository.get_type(guild_id, type_id) if type_id else None
            )
            if type_id and not proposal_type:
                return "Select a valid proposal type from this server."
            title = str(options.get("title", ""))
            context = str(options.get("context", "")).strip()
            return await self._new(
                str(payload["id"]), title, context, proposal_type, config
            )
        if path == ("proposal", "preferences"):
            new_value = options.get("new_proposals")
            nudge_value = options.get("nudges")
            if new_value is not None:
                await self.repository.set_guild_subscription(
                    guild_id, user_id, bool(new_value)
                )
            if nudge_value is not None:
                await self.repository.set_nudges_enabled(
                    guild_id, user_id, bool(nudge_value)
                )
            new_enabled = await self.repository.get_guild_subscription(
                guild_id, user_id
            )
            nudges_enabled = await self.repository.get_nudges_enabled(guild_id, user_id)
            return (
                "**Your proposal preferences in this server**\n"
                f"New proposal DMs: **{'on' if new_enabled else 'off'}**\n"
                f"Anonymous nudges: **{'on' if nudges_enabled else 'off'}**"
            )
        if path == ("proposal", "nudge"):
            return await self._nudge(
                guild_id,
                user_id,
                str(options.get("proposal", "")),
                str(options.get("user", "")),
            )
        if path == ("proposal", "type", "list"):
            proposal_types = await self.repository.list_types(guild_id)
            return "**Available proposal types**\n" + "\n".join(
                f"• **{proposal_type.name}** — {proposal_type.description}"
                + (" *(built-in)*" if proposal_type.builtin else "")
                for proposal_type in proposal_types
            )
        return "Unknown command."

    async def _modal(self, payload: dict[str, Any], guild_id: str, user_id: str) -> str:
        data = payload.get("data") or {}
        custom_id = str(data.get("custom_id", ""))
        values = modal_values(data)
        if custom_id.startswith("proposal-veto|"):
            proposal_id = custom_id.removeprefix("proposal-veto|")
            reason = values.get("reason", "").strip()
            if len(reason) > 500:
                return "Veto reasons cannot exceed 500 characters."
            proposal = await self._active_proposal(guild_id, proposal_id)
            if not proposal:
                return "This proposal is no longer active."
            return await self._veto(proposal, reason)
        if custom_id.startswith(("type-create|", "type-edit|")):
            if not self._is_admin(payload, user_id):
                return "You need Manage Server permission to manage proposal types."
            if not await self.repository.get_guild_config(guild_id):
                return "Run `/proposal configure` before managing proposal types."
            parts = custom_id.split("|")
            if len(parts) != 2:
                return "This proposal type form is no longer valid."
            action, type_id = parts
            editing_id = type_id if action == "type-edit" else None
            if editing_id:
                existing = await self.repository.get_type(guild_id, editing_id)
                if not existing or existing.builtin:
                    return "That custom proposal type no longer exists."
            try:
                (
                    name,
                    normalized_name,
                    description,
                ) = validate_type_fields(
                    values.get("name", ""),
                    values.get("description", ""),
                )
            except ValueError as exc:
                return str(exc)
            result = await self.repository.save_type(
                guild_id,
                editing_id,
                name,
                normalized_name,
                description,
                user_id,
                utcnow(),
            )
            if result.reason == "duplicate_name":
                return "A custom proposal type with that name already exists."
            if result.reason == "limit":
                return (
                    "This server already has the maximum of 20 custom proposal types."
                )
            if not result.changed or not result.proposal_type:
                return "The proposal type could not be saved."
            return (
                f"Proposal type **{result.proposal_type.name}** "
                f"has been {result.reason}."
            )
        return "This form is no longer valid."

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
        self,
        interaction_id: str,
        title: str,
        context: str,
        proposal_type: ProposalType | None,
        config: GuildConfig,
    ) -> str:
        title = " ".join(title.split())
        context = context.strip()
        try:
            normalized = normalize_title(title)
        except ValueError as exc:
            return str(exc)
        if len(context) > 1000:
            return "Proposal context cannot exceed 1,000 characters."
        now = utcnow()
        deadline = now + timedelta(seconds=config.proposal_timeout_seconds)
        result = await self.repository.create_proposal(
            interaction_id,
            config.guild_id,
            config.guild_name,
            config.output_channel_id,
            title,
            normalized,
            context,
            proposal_type.id if proposal_type else "",
            proposal_type.name if proposal_type else "",
            now,
            deadline,
        )
        if result.duplicate_name or not result.proposal:
            return f"An active proposal titled '{title}' already exists."
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
                failure_message = (
                    "I could not post in the configured proposal channel. "
                    "Run `/proposal configure` after fixing my channel permissions."
                )
                try:
                    await self.discord.validate_output_channel(
                        proposal.guild_id, proposal.output_channel_id
                    )
                except DiscordAPIError as validation_error:
                    if not validation_error.retryable:
                        failure_message = str(validation_error)
                transition_result = await self.repository.transition(
                    proposal.id, ProposalStatus.DELETED, utcnow()
                )
                await self.tasks.delete(proposal.deadline_task_name)
                if transition_result.proposal:
                    await self.repository.mark_announcement_synced(proposal.id)
                    await self.repository.mark_effects_complete(proposal.id)
                return failure_message
            proposal = (
                await self.repository.set_message_id(proposal.id, message_id)
                or proposal
            )
            proposal = (
                await self.repository.mark_rendered(proposal.id, CURRENT_RENDER_VERSION)
                or proposal
            )
        await self._notify_created(proposal)
        return (
            f"Proposal created successfully. It will pass "
            f"<t:{int(proposal.deadline_at.timestamp())}:R> unless vetoed."
        )

    async def _delete(self, guild_id: str, proposal_id: str) -> str:
        proposal = await self._active_proposal(guild_id, proposal_id)
        if not proposal:
            return "Select a valid active proposal from this server."
        result = await self.repository.transition(
            proposal.id, ProposalStatus.DELETED, utcnow()
        )
        if result.proposal:
            await self.tasks.delete(result.proposal.deadline_task_name)
            await self.sync_terminal_effects(result.proposal)
        return f"Proposal '{proposal.title}' has been deleted."

    async def _nudge(
        self,
        guild_id: str,
        user_id: str,
        proposal_id: str,
        target_user_id: str,
    ) -> str:
        proposal = await self._active_proposal(guild_id, proposal_id)
        if not proposal:
            return "Select a valid active proposal from this server."
        if target_user_id == user_id:
            return "You cannot nudge yourself."
        try:
            member = await self.discord.get_guild_member(guild_id, target_user_id)
        except DiscordAPIError as exc:
            if exc.retryable:
                raise
            return "That user is not a member of this server."
        target_user = member.get("user")
        if isinstance(target_user, dict) and bool(target_user.get("bot")):
            return "Bots cannot receive proposal nudges."
        reserved = await self.repository.reserve_nudge(
            proposal.id, target_user_id, utcnow()
        )
        if reserved.reason == "nudges_disabled":
            return "That member has disabled anonymous nudges in this server."
        if reserved.reason == "nudge_limit":
            return "This proposal has reached its limit of 10 nudged members."
        if reserved.reason in {"nudge_delivered", "nudge_failed"}:
            return "This member has already been nudged for this proposal."
        if reserved.reason in {
            "not_found",
            "already_terminal",
            "deadline_elapsed",
        }:
            return "This proposal is no longer active."
        if reserved.reason != "nudge_pending" or not reserved.proposal:
            return "This member has already been nudged for this proposal."

        proposal = reserved.proposal
        try:
            await self.discord.send_dm(
                target_user_id,
                _nudge_text(proposal),
                event_key=f"{proposal.id}:nudge:{target_user_id}",
            )
        except DiscordAPIError as exc:
            if exc.retryable:
                raise
            await self.repository.mark_nudge_state(
                proposal.id, target_user_id, "failed"
            )
            return (
                "I could not deliver that nudge, likely because their DMs are closed."
            )
        await self.repository.mark_nudge_state(proposal.id, target_user_id, "delivered")
        return "Nudge sent. The recipient was not told who requested it."

    async def _component(
        self, payload: dict[str, Any], guild_id: str, user_id: str
    ) -> str | InteractionReply:
        parsed = parse_component_id(
            str((payload.get("data") or {}).get("custom_id", ""))
        )
        if not parsed:
            return "This control is no longer valid."
        scope, action, resource_id = parsed
        if scope == "proposal" and action == "workspace":
            proposal = await self.repository.get_proposal(resource_id)
            if not proposal or proposal.guild_id != guild_id:
                return "This control belongs to a proposal in another server."
            return await self._workspace_reply(
                user_id, guild_id, proposal_id=proposal.id
            )
        if action == "confirm-delete":
            if not self._is_admin(payload, user_id):
                return "You need Manage Server permission to delete this."
            if scope == "proposal":
                return await self._delete(guild_id, resource_id)
            proposal_type = await self.repository.get_type(guild_id, resource_id)
            if not proposal_type or proposal_type.builtin:
                return "That custom proposal type no longer exists."
            result = await self.repository.delete_type(guild_id, resource_id)
            return (
                f"Proposal type **{proposal_type.name}** has been deleted."
                if result.changed
                else "That custom proposal type no longer exists."
            )
        if scope != "proposal":
            return "This control is no longer valid."
        proposal_id = resource_id
        proposal = await self.repository.get_proposal(proposal_id)
        if not proposal or proposal.status is not ProposalStatus.ACTIVE:
            return "This proposal is no longer active."
        if proposal.guild_id != guild_id:
            return "This control belongs to a proposal in another server."
        if action == "nudge-select":
            values = (payload.get("data") or {}).get("values") or []
            target_user_id = str(values[0]) if values else ""
            if not target_user_id:
                return "Choose a server member to nudge."
            return await self._nudge(guild_id, user_id, proposal_id, target_user_id)
        if action == "subscribe":
            added = await self.repository.add_proposal_subscription(
                proposal_id, user_id
            )
            return (
                "You have subscribed to updates for this proposal."
                if added
                else "You are already subscribed to this proposal."
            )
        if action == "acknowledge":
            acknowledgement = await self.repository.acknowledge(
                proposal_id, user_id, utcnow()
            )
            if acknowledgement.reason == "already_acknowledged":
                return "You have already acknowledged seeing this proposal."
            if not acknowledgement.changed or not acknowledgement.proposal:
                return "This proposal is no longer active."
            await self._sync_announcement(proposal_id)
            return (
                "Acknowledged. This records only that you saw the proposal; "
                "you may still veto it before the deadline."
            )
        if action == "confirm-veto":
            return await self._veto(proposal, "")
        return "This control is no longer valid."

    async def _workspace_reply(
        self,
        user_id: str,
        guild_id: str,
        *,
        proposal_id: str | None = None,
        guild_ids: list[str] | None = None,
    ) -> InteractionReply:
        available_guild_ids = list(
            dict.fromkeys(guild_ids or ([guild_id] if guild_id else []))
        )
        if not guild_id or not available_guild_ids:
            return InteractionReply(
                "I could not find a configured server you can access. Add the bot "
                "to a server, configure it there, then try `/proposal` again."
            )
        code = secrets.token_urlsafe(32)
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        await self.repository.create_workspace_launch(
            code_hash,
            user_id,
            guild_id,
            available_guild_ids,
            proposal_id,
            utcnow(),
            self.settings.workspace_launch_ttl_seconds,
        )
        url = f"{self.settings.workspace_url}/launch?{urlencode({'code': code})}"
        return InteractionReply(
            "Your private workspace link expires in five minutes and can be used once.",
            link_button("Open workspace", url),
        )

    async def _dm_workspace_reply(self, user_id: str) -> InteractionReply:
        guild_ids: list[str] = []
        discord_unavailable = False
        for config in await self.repository.list_all_guild_configs():
            try:
                await self.workspace_access(config.guild_id, user_id)
            except DiscordAPIError as exc:
                if exc.retryable:
                    discord_unavailable = True
                continue
            guild_ids.append(config.guild_id)
        if not guild_ids:
            if discord_unavailable:
                raise DiscordAPIError(
                    503, "Discord membership lookup is temporarily unavailable."
                )
            return InteractionReply(
                "I could not find any configured mutual servers. Run `/proposal` "
                "inside a server first, or ask a server manager to configure Vite-oh."
            )
        reply = await self._workspace_reply(
            user_id,
            guild_ids[0],
            guild_ids=guild_ids,
        )
        return InteractionReply(
            (
                "Open your private workspace to switch between "
                f"{len(guild_ids)} available server"
                f"{'s' if len(guild_ids) != 1 else ''}. "
                "This link expires in five minutes and can be used once."
            ),
            reply.components,
        )

    async def exchange_workspace_launch(self, code: str) -> dict[str, Any] | None:
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        launch = await self.repository.consume_workspace_launch(code_hash, utcnow())
        if not launch:
            return None
        return {
            "user_id": launch.user_id,
            "guild_id": launch.guild_id,
            "guild_ids": list(launch.guild_ids),
            "proposal_id": launch.proposal_id,
            "display_name": "Discord member",
        }

    async def workspace_access(self, guild_id: str, user_id: str) -> dict[str, Any]:
        access = await self.discord.get_workspace_access(guild_id, user_id)
        access["can_manage"] = bool(
            access.get("can_manage")
            or (
                self.settings.discord_owner_user_id
                and user_id == self.settings.discord_owner_user_id
            )
        )
        return access

    async def workspace_guild_summaries(
        self, guild_ids: list[str], user_id: str
    ) -> dict[str, Any]:
        summaries: list[dict[str, Any]] = []
        for guild_id in dict.fromkeys(guild_ids):
            try:
                access = await self.workspace_access(guild_id, user_id)
            except DiscordAPIError:
                logger.info(
                    "Omitting inaccessible workspace guild",
                    extra={"guild_id": guild_id},
                )
                continue
            config = await self.repository.get_guild_config(guild_id)
            summaries.append(
                {
                    "guild_id": guild_id,
                    "guild_name": str(access.get("guild_name") or guild_id),
                    "guild_icon_hash": str(access.get("guild_icon_hash") or ""),
                    "configured": config is not None,
                    "can_manage": bool(access.get("can_manage")),
                }
            )
        summaries.sort(key=lambda item: str(item["guild_name"]).casefold())
        return {"guilds": summaries}

    async def process_workspace(self, payload: dict[str, Any]) -> None:
        job_id = str(payload.get("id", ""))
        guild_id = str(payload.get("guild_id", ""))
        user_id = str(payload.get("actor_user_id", ""))
        action = str(payload.get("action", ""))
        data = payload.get("data") or {}
        requester_hash = self.workspace_codec.fingerprint(user_id)
        existing_job = await self.repository.get_workspace_job(job_id)
        if existing_job and existing_job.status in {"succeeded", "failed"}:
            return
        now = utcnow()
        await self.repository.set_workspace_job(
            job_id,
            guild_id,
            requester_hash,
            action,
            "processing",
            "",
            None,
            now,
            self.settings.workspace_job_ttl_seconds,
        )
        proposal_id: str | None = None
        try:
            access = await self.workspace_access(guild_id, user_id)
            message, proposal_id = await self._workspace_action(
                job_id,
                action,
                data,
                guild_id,
                user_id,
                access,
                retrying=existing_job is not None,
            )
            status = "succeeded"
        except DiscordAPIError as exc:
            if exc.retryable:
                raise
            status = "failed"
            message = "Discord no longer permits that action."
        except (ValueError, PermissionError) as exc:
            status = "failed"
            message = str(exc)
        await self.repository.set_workspace_job(
            job_id,
            guild_id,
            requester_hash,
            action,
            status,
            message,
            proposal_id,
            utcnow(),
            self.settings.workspace_job_ttl_seconds,
        )

    async def _workspace_action(
        self,
        job_id: str,
        action: str,
        data: dict[str, Any],
        guild_id: str,
        user_id: str,
        access: dict[str, Any],
        *,
        retrying: bool = False,
    ) -> tuple[str, str | None]:
        config = await self.repository.get_guild_config(guild_id)
        is_admin = bool(access.get("can_manage"))
        visible_channels = {
            str(item) for item in access.get("visible_channel_ids") or []
        }
        if action == "create":
            if not config:
                raise ValueError("This server must be configured first.")
            if config.output_channel_id not in visible_channels:
                raise PermissionError("You cannot view this server's proposal channel.")
            type_id = str(data.get("type_id") or "")
            proposal_type = (
                await self.repository.get_type(guild_id, type_id) if type_id else None
            )
            if type_id and not proposal_type:
                raise ValueError("That proposal type no longer exists.")
            message = await self._new(
                f"web:{job_id}",
                str(data.get("title", "")),
                str(data.get("context", "")),
                proposal_type,
                config,
            )
            proposal = await self.repository.get_proposal_for_interaction(
                f"web:{job_id}"
            )
            if not proposal or proposal.status is not ProposalStatus.ACTIVE:
                raise ValueError(message)
            return message, proposal.id
        if action == "preferences":
            await self.repository.set_guild_subscription(
                guild_id, user_id, bool(data.get("new_proposals"))
            )
            await self.repository.set_nudges_enabled(
                guild_id, user_id, bool(data.get("nudges"))
            )
            return "Your preferences were saved.", None
        if not is_admin:
            raise PermissionError("You need Manage Server permission.")
        if action == "configure":
            channel_id = str(data.get("channel_id", ""))
            duration_minutes = int(data.get("duration_minutes", 0))
            if not MIN_DURATION_MINUTES <= duration_minutes <= MAX_DURATION_MINUTES:
                raise ValueError("Duration must be between 1 and 10,080 minutes.")
            ready_channels = {
                str(channel["id"])
                for channel in access.get("output_channels") or []
                if channel.get("bot_ready")
            }
            if channel_id not in ready_channels:
                raise PermissionError(
                    "Choose a visible text channel where the bot can post."
                )
            guild_name = await self.discord.validate_output_channel(
                guild_id, channel_id
            )
            await self.repository.set_guild_config(
                guild_id,
                guild_name,
                channel_id,
                duration_minutes * 60,
                user_id,
                utcnow(),
            )
            return "Server settings were saved.", None
        if action == "delete":
            proposal_id = str(data.get("proposal_id", ""))
            proposal = await self.repository.get_proposal(proposal_id)
            if not proposal or proposal.guild_id != guild_id:
                raise ValueError("That proposal no longer exists.")
            if proposal.output_channel_id not in visible_channels:
                raise PermissionError("You cannot view that proposal's channel.")
            return await self._delete(guild_id, proposal_id), proposal_id
        if action == "archive":
            proposal_id = str(data.get("proposal_id", ""))
            proposal = await self.repository.get_proposal(proposal_id)
            if not proposal or proposal.guild_id != guild_id:
                raise ValueError("That proposal no longer exists.")
            if proposal.output_channel_id not in visible_channels:
                raise PermissionError("You cannot view that proposal's channel.")
            archive_result = await self.repository.archive_proposal(
                proposal_id, guild_id, user_id, utcnow()
            )
            if archive_result.reason == "active":
                raise ValueError("Active proposals cannot be archived.")
            if archive_result.reason == "effects_pending":
                raise ValueError(
                    "This proposal is still finishing its Discord notifications. "
                    "Try again shortly."
                )
            if (
                not archive_result.changed
                and archive_result.reason != "already_archived"
            ):
                raise ValueError("That proposal could not be archived.")
            return "The proposal was moved to the archive.", proposal_id
        if action == "purge":
            proposal_id = str(data.get("proposal_id", ""))
            proposal = await self.repository.get_proposal(proposal_id)
            if not proposal and retrying:
                return "The archived proposal was permanently deleted.", None
            if not proposal or proposal.guild_id != guild_id:
                raise ValueError("That proposal no longer exists.")
            if proposal.output_channel_id not in visible_channels:
                raise PermissionError("You cannot view that proposal's channel.")
            if not proposal.archived:
                raise ValueError("Archive this proposal before deleting it.")
            await self.discord.delete_proposal_history_messages(proposal)
            purge_result = await self.repository.purge_archived_proposal(
                proposal_id, guild_id
            )
            if not purge_result.changed:
                raise ValueError("That archived proposal could not be deleted.")
            return "The archived proposal was permanently deleted.", None
        if action == "restore":
            proposal_id = str(data.get("proposal_id", ""))
            proposal = await self.repository.get_proposal(proposal_id)
            if not proposal or proposal.guild_id != guild_id:
                raise ValueError("That proposal no longer exists.")
            if proposal.output_channel_id not in visible_channels:
                raise PermissionError("You cannot view that proposal's channel.")
            restore_result = await self.repository.restore_archived_proposal(
                proposal_id, guild_id
            )
            if not restore_result.changed:
                raise ValueError("That proposal is not archived.")
            return "The proposal was restored to the main history.", proposal_id
        if action == "type_save":
            submitted_type_id = str(data.get("type_id") or "") or None
            if submitted_type_id:
                existing = await self.repository.get_type(guild_id, submitted_type_id)
                if not existing or existing.builtin:
                    raise ValueError("That custom proposal type no longer exists.")
            type_id = submitted_type_id or job_id
            name, normalized_name, description = validate_type_fields(
                str(data.get("name", "")),
                str(data.get("description", "")),
            )
            type_result = await self.repository.save_type(
                guild_id,
                type_id,
                name,
                normalized_name,
                description,
                user_id,
                utcnow(),
            )
            if not type_result.changed or not type_result.proposal_type:
                reason = {
                    "duplicate_name": "A proposal type with that name exists.",
                    "limit": "This server already has 20 custom proposal types.",
                }.get(type_result.reason, "The proposal type could not be saved.")
                raise ValueError(reason)
            return (
                f"Proposal type “{type_result.proposal_type.name}” was saved.",
                None,
            )
        if action == "type_delete":
            type_id = str(data.get("type_id", ""))
            existing = await self.repository.get_type(guild_id, type_id)
            if not existing or existing.builtin:
                raise ValueError("That custom proposal type no longer exists.")
            delete_type_result = await self.repository.delete_type(guild_id, type_id)
            if not delete_type_result.changed:
                raise ValueError("That custom proposal type no longer exists.")
            return f"Proposal type “{existing.name}” was deleted.", None
        raise ValueError("This workspace action is not supported.")

    async def _veto(self, proposal: Proposal, reason: str) -> str:
        result = await self.repository.transition(
            proposal.id, ProposalStatus.VETOED, utcnow(), reason
        )
        if result.reason == "deadline_elapsed":
            return "The deadline has elapsed; this proposal can no longer be vetoed."
        if not result.changed or not result.proposal:
            return "This proposal is no longer active."
        await self.tasks.delete(result.proposal.deadline_task_name)
        await self.sync_terminal_effects(result.proposal)
        return "You have vetoed the proposal anonymously."

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
        rendered = 0
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
            if proposal.render_version < CURRENT_RENDER_VERSION:
                await self._sync_announcement(proposal.id)
                rendered += 1
        for proposal in await self.repository.list_pending_terminal_effects():
            await self.sync_terminal_effects(proposal)
            effects_retried += 1
        result = {
            "active": len(active),
            "finalized": finalized,
            "repaired": repaired,
            "rendered": rendered,
            "effects_retried": effects_retried,
        }
        if finalized or repaired or rendered or effects_retried:
            logger.warning("Reconciliation repaired proposal state", extra=result)
        return result

    async def sync_terminal_effects(self, proposal: Proposal) -> None:
        if not proposal.announcement_synced or not proposal.outcome_message_id:
            proposal = await self._sync_announcement(proposal.id) or proposal
            await self.repository.mark_announcement_synced(proposal.id)
        if not proposal.outcome_message_id:
            outcome_message_id = await self.discord.create_outcome_reply(proposal)
            proposal = (
                await self.repository.set_outcome_message_id(
                    proposal.id, outcome_message_id
                )
                or proposal
            )
        subscribers = await self.repository.proposal_subscribers(proposal.id)
        event = proposal.status.value
        outcome = {
            ProposalStatus.PASSED: "passed",
            ProposalStatus.VETOED: "been vetoed",
            ProposalStatus.DELETED: "been deleted by an admin",
        }[proposal.status]
        text = (
            f"In **{proposal.guild_name}**, this proposal has {outcome}:\n"
            f"{_proposal_details(proposal)}\n{_proposal_link(proposal)}"
        )
        await self._notify(proposal, event, subscribers, text)
        await self.repository.mark_effects_complete(proposal.id)

    async def _notify_created(self, proposal: Proposal) -> None:
        subscribers = await self.repository.guild_subscribers(proposal.guild_id)
        await self._notify(
            proposal,
            "created",
            subscribers,
            _created_text(proposal),
        )

    async def _sync_announcement(self, proposal_id: str) -> Proposal | None:
        latest: Proposal | None = None
        for _ in range(4):
            latest = await self.repository.get_proposal(proposal_id)
            if not latest:
                return None
            version = latest.announcement_version
            message_id = await self.discord.sync_proposal_announcement(latest)
            if message_id != latest.message_id:
                latest = (
                    await self.repository.set_message_id(proposal_id, message_id)
                    or latest
                )
            current = await self.repository.get_proposal(proposal_id)
            if not current or current.announcement_version == version:
                stable = current or latest
                return (
                    await self.repository.mark_rendered(
                        proposal_id, CURRENT_RENDER_VERSION
                    )
                    or stable
                )
        return latest

    async def _active_proposal(
        self, guild_id: str, proposal_id: str
    ) -> Proposal | None:
        try:
            if str(UUID(proposal_id)) != proposal_id:
                return None
        except ValueError:
            return None
        proposal = await self.repository.get_proposal(proposal_id)
        if (
            not proposal
            or proposal.guild_id != guild_id
            or proposal.status is not ProposalStatus.ACTIVE
        ):
            return None
        return proposal

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


def _proposal_details(proposal: Proposal) -> str:
    context = f"\n{proposal.context}" if proposal.context else ""
    proposal_type = f" · {proposal.type_name}" if proposal.type_name else ""
    reason = (
        f"\nAnonymous veto reason: {proposal.veto_reason}"
        if proposal.status is ProposalStatus.VETOED and proposal.veto_reason
        else ""
    )
    return (
        f"**{proposal.title}**{proposal_type}{context}{reason}\n"
        f"Deadline: <t:{int(proposal.deadline_at.timestamp())}:F>"
    )


def _created_text(proposal: Proposal) -> str:
    return (
        f"A new proposal was created in **{proposal.guild_name}**:\n"
        f"{_proposal_details(proposal)}\n{_proposal_link(proposal)}"
    )


def _nudge_text(proposal: Proposal) -> str:
    return (
        f"Someone in **{proposal.guild_name}** wants to make sure you saw this "
        f"proposal. This does not imply that they support or oppose it:\n"
        f"{_proposal_details(proposal)}\n{_proposal_link(proposal)}"
    )
