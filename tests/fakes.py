import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Any

from viteoh.domain import (
    CreateProposalResult,
    GuildConfig,
    Proposal,
    ProposalActionResult,
    ProposalStatus,
    ProposalType,
    TransitionResult,
    TypeMutationResult,
    WorkspaceJob,
    WorkspaceLaunch,
)
from viteoh.proposal_types import BUILTIN_TYPES, MAX_CUSTOM_TYPES, builtin_type
from viteoh.repository import reservation_id


class FakeRepository:
    def __init__(self) -> None:
        self.proposals: dict[str, Proposal] = {}
        self.guilds: dict[str, GuildConfig] = {}
        self.interactions: dict[str, str] = {}
        self.guild_users: dict[str, set[str]] = {}
        self.subscribers: dict[str, set[str]] = {}
        self.delivered: set[tuple[str, str, str]] = set()
        self.acknowledgements: dict[str, set[str]] = {}
        self.nudges: dict[str, dict[str, str]] = {}
        self.nudge_preferences: dict[tuple[str, str], bool] = {}
        self.types: dict[tuple[str, str], ProposalType] = {}
        self.workspace_jobs: dict[str, WorkspaceJob] = {}
        self.workspace_launches: dict[str, WorkspaceLaunch] = {}
        self.lock = asyncio.Lock()
        self.next_id = "12345678-1234-1234-1234-123456789abc"

    async def get_guild_config(self, guild_id: str) -> GuildConfig | None:
        return self.guilds.get(guild_id)

    async def list_guild_configs(self, guild_ids: list[str]) -> list[GuildConfig]:
        return sorted(
            (config for guild_id in guild_ids if (config := self.guilds.get(guild_id))),
            key=lambda item: item.guild_name.casefold(),
        )

    async def list_all_guild_configs(self) -> list[GuildConfig]:
        return sorted(
            self.guilds.values(),
            key=lambda item: item.guild_name.casefold(),
        )

    async def set_guild_config(
        self,
        guild_id: str,
        guild_name: str,
        output_channel_id: str,
        proposal_timeout_seconds: int,
        configured_by: str,
        now: datetime,
    ) -> GuildConfig:
        previous = self.guilds.get(guild_id)
        config = GuildConfig(
            guild_id=guild_id,
            guild_name=guild_name,
            output_channel_id=output_channel_id,
            proposal_timeout_seconds=proposal_timeout_seconds,
            configured_by=configured_by,
            created_at=previous.created_at if previous else now,
            updated_at=now,
        )
        self.guilds[guild_id] = config
        return config

    async def create_proposal(
        self,
        interaction_id: str,
        guild_id: str,
        guild_name: str,
        output_channel_id: str,
        title: str,
        normalized_title: str,
        context: str,
        type_id: str,
        type_name: str,
        created_at: datetime,
        deadline_at: datetime,
    ) -> CreateProposalResult:
        async with self.lock:
            if interaction_id in self.interactions:
                return CreateProposalResult(
                    self.proposals[self.interactions[interaction_id]]
                )
            if any(
                item.status is ProposalStatus.ACTIVE
                and item.guild_id == guild_id
                and item.normalized_title == normalized_title
                for item in self.proposals.values()
            ):
                return CreateProposalResult(None, True)
            proposal = Proposal(
                id=self.next_id,
                guild_id=guild_id,
                guild_name=guild_name,
                output_channel_id=output_channel_id,
                title=title,
                normalized_title=normalized_title,
                context=context,
                reservation_id=reservation_id(guild_id, normalized_title),
                status=ProposalStatus.ACTIVE,
                created_at=created_at,
                deadline_at=deadline_at,
                type_id=type_id,
                type_name=type_name,
            )
            self.proposals[proposal.id] = proposal
            self.interactions[interaction_id] = proposal.id
            return CreateProposalResult(proposal)

    async def get_proposal(self, proposal_id: str) -> Proposal | None:
        return self.proposals.get(proposal_id)

    async def get_proposal_for_interaction(
        self, interaction_id: str
    ) -> Proposal | None:
        proposal_id = self.interactions.get(interaction_id)
        return self.proposals.get(proposal_id) if proposal_id else None

    async def list_active(self, guild_id: str | None = None) -> list[Proposal]:
        return [
            proposal
            for proposal in self.proposals.values()
            if proposal.status is ProposalStatus.ACTIVE
            and (guild_id is None or proposal.guild_id == guild_id)
        ]

    async def list_guild_proposals(
        self,
        guild_id: str,
        *,
        archived: bool = False,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[Proposal]:
        return sorted(
            (
                proposal
                for proposal in self.proposals.values()
                if proposal.guild_id == guild_id
                and proposal.archived is archived
                and (before is None or proposal.created_at < before)
            ),
            key=lambda proposal: proposal.created_at,
            reverse=True,
        )[:limit]

    async def archive_proposal(
        self, proposal_id: str, guild_id: str, user_id: str, now: datetime
    ) -> ProposalActionResult:
        proposal = self.proposals.get(proposal_id)
        if not proposal or proposal.guild_id != guild_id:
            return ProposalActionResult(None, False, "not_found")
        if proposal.status is ProposalStatus.ACTIVE:
            return ProposalActionResult(proposal, False, "active")
        if not proposal.effects_complete:
            return ProposalActionResult(proposal, False, "effects_pending")
        if proposal.archived:
            return ProposalActionResult(proposal, False, "already_archived")
        proposal = replace(
            proposal,
            archived=True,
            archived_at=now,
            archived_by=user_id,
        )
        self.proposals[proposal_id] = proposal
        return ProposalActionResult(proposal, True, "archived")

    async def purge_archived_proposal(
        self, proposal_id: str, guild_id: str
    ) -> ProposalActionResult:
        proposal = self.proposals.get(proposal_id)
        if not proposal or proposal.guild_id != guild_id:
            return ProposalActionResult(None, False, "not_found")
        if not proposal.archived or proposal.status is ProposalStatus.ACTIVE:
            return ProposalActionResult(proposal, False, "not_archived")
        if not proposal.effects_complete:
            return ProposalActionResult(proposal, False, "effects_pending")
        del self.proposals[proposal_id]
        self.acknowledgements.pop(proposal_id, None)
        self.nudges.pop(proposal_id, None)
        self.subscribers.pop(proposal_id, None)
        self.delivered = {item for item in self.delivered if item[0] != proposal_id}
        self.interactions = {
            interaction_id: item_proposal_id
            for interaction_id, item_proposal_id in self.interactions.items()
            if item_proposal_id != proposal_id
        }
        return ProposalActionResult(proposal, True, "purged")

    async def restore_archived_proposal(
        self, proposal_id: str, guild_id: str
    ) -> ProposalActionResult:
        proposal = self.proposals.get(proposal_id)
        if not proposal or proposal.guild_id != guild_id:
            return ProposalActionResult(None, False, "not_found")
        if not proposal.archived:
            return ProposalActionResult(proposal, False, "not_archived")
        proposal = replace(
            proposal,
            archived=False,
            archived_at=None,
            archived_by="",
        )
        self.proposals[proposal_id] = proposal
        return ProposalActionResult(proposal, True, "restored")

    async def list_pending_terminal_effects(self) -> list[Proposal]:
        return [
            proposal
            for proposal in self.proposals.values()
            if proposal.status is not ProposalStatus.ACTIVE
            and not proposal.effects_complete
        ]

    async def get_type(self, guild_id: str, type_id: str) -> ProposalType | None:
        builtin = builtin_type(type_id)
        return (
            replace(builtin, guild_id=guild_id)
            if builtin
            else self.types.get((guild_id, type_id))
        )

    async def list_types(self, guild_id: str) -> list[ProposalType]:
        return [
            *(
                replace(proposal_type, guild_id=guild_id)
                for proposal_type in BUILTIN_TYPES
            ),
            *sorted(
                (
                    proposal_type
                    for (item_guild, _), proposal_type in self.types.items()
                    if item_guild == guild_id
                ),
                key=lambda proposal_type: proposal_type.normalized_name,
            ),
        ]

    async def save_type(
        self,
        guild_id: str,
        type_id: str | None,
        name: str,
        normalized_name: str,
        description: str,
        user_id: str,
        now: datetime,
    ) -> TypeMutationResult:
        existing = self.types.get((guild_id, type_id or ""))
        custom = [
            item
            for (item_guild, _), item in self.types.items()
            if item_guild == guild_id
        ]
        if not existing and len(custom) >= MAX_CUSTOM_TYPES:
            return TypeMutationResult(None, False, "limit")
        if any(
            item.normalized_name == normalized_name
            and (not existing or item.id != existing.id)
            for item in custom
        ):
            return TypeMutationResult(None, False, "duplicate_name")
        type_id = type_id or f"87654321-4321-4321-4321-{len(custom):012d}"
        proposal_type = ProposalType(
            id=type_id,
            guild_id=guild_id,
            name=name,
            normalized_name=normalized_name,
            description=description,
            created_by=existing.created_by if existing else user_id,
            updated_by=user_id,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self.types[(guild_id, type_id)] = proposal_type
        return TypeMutationResult(
            proposal_type, True, "updated" if existing else "created"
        )

    async def delete_type(self, guild_id: str, type_id: str) -> TypeMutationResult:
        proposal_type = self.types.pop((guild_id, type_id), None)
        return TypeMutationResult(
            proposal_type,
            bool(proposal_type),
            "deleted" if proposal_type else "not_found",
        )

    async def mark_task_scheduled(
        self, proposal_id: str, task_name: str
    ) -> Proposal | None:
        proposal = self.proposals[proposal_id]
        updated = replace(proposal, deadline_task_name=task_name, task_scheduled=True)
        self.proposals[proposal_id] = updated
        return updated

    async def set_message_id(
        self, proposal_id: str, message_id: str
    ) -> Proposal | None:
        updated = replace(self.proposals[proposal_id], message_id=message_id)
        self.proposals[proposal_id] = updated
        return updated

    async def mark_rendered(
        self, proposal_id: str, render_version: int
    ) -> Proposal | None:
        updated = replace(self.proposals[proposal_id], render_version=render_version)
        self.proposals[proposal_id] = updated
        return updated

    async def set_outcome_message_id(
        self, proposal_id: str, message_id: str
    ) -> Proposal | None:
        updated = replace(self.proposals[proposal_id], outcome_message_id=message_id)
        self.proposals[proposal_id] = updated
        return updated

    async def transition(
        self,
        proposal_id: str,
        status: ProposalStatus,
        now: datetime,
        veto_reason: str = "",
    ) -> TransitionResult:
        async with self.lock:
            proposal = self.proposals.get(proposal_id)
            if not proposal:
                return TransitionResult(None, False, "not_found")
            if proposal.status is not ProposalStatus.ACTIVE:
                return TransitionResult(proposal, False, "already_terminal")
            if status is ProposalStatus.PASSED and now < proposal.deadline_at:
                return TransitionResult(proposal, False, "not_due")
            if status is ProposalStatus.VETOED and now >= proposal.deadline_at:
                return TransitionResult(proposal, False, "deadline_elapsed")
            updated = replace(
                proposal,
                status=status,
                terminal_at=now,
                announcement_synced=False,
                effects_complete=False,
                announcement_version=proposal.announcement_version + 1,
                veto_reason=veto_reason if status is ProposalStatus.VETOED else "",
            )
            self.proposals[proposal_id] = updated
            return TransitionResult(updated, True, "transitioned")

    async def acknowledge(
        self, proposal_id: str, user_id: str, now: datetime
    ) -> ProposalActionResult:
        async with self.lock:
            proposal = self.proposals.get(proposal_id)
            if not proposal:
                return ProposalActionResult(None, False, "not_found")
            if proposal.status is not ProposalStatus.ACTIVE:
                return ProposalActionResult(proposal, False, "already_terminal")
            if now >= proposal.deadline_at:
                return ProposalActionResult(proposal, False, "deadline_elapsed")
            users = self.acknowledgements.setdefault(proposal_id, set())
            if user_id in users:
                return ProposalActionResult(proposal, False, "already_acknowledged")
            users.add(user_id)
            updated = replace(
                proposal,
                acknowledgement_count=proposal.acknowledgement_count + 1,
                announcement_version=proposal.announcement_version + 1,
            )
            self.proposals[proposal_id] = updated
            return ProposalActionResult(updated, True, "acknowledged")

    async def has_acknowledged(self, proposal_id: str, user_id: str) -> bool:
        return user_id in self.acknowledgements.get(proposal_id, set())

    async def reserve_nudge(
        self, proposal_id: str, target_user_id: str, now: datetime
    ) -> ProposalActionResult:
        async with self.lock:
            proposal = self.proposals.get(proposal_id)
            if not proposal:
                return ProposalActionResult(None, False, "not_found")
            if proposal.status is not ProposalStatus.ACTIVE:
                return ProposalActionResult(proposal, False, "already_terminal")
            if now >= proposal.deadline_at:
                return ProposalActionResult(proposal, False, "deadline_elapsed")
            if not self.nudge_preferences.get(
                (proposal.guild_id, target_user_id), True
            ):
                return ProposalActionResult(proposal, False, "nudges_disabled")
            nudges = self.nudges.setdefault(proposal_id, {})
            if target_user_id in nudges:
                return ProposalActionResult(
                    proposal, False, f"nudge_{nudges[target_user_id]}"
                )
            if proposal.nudge_count >= 10:
                return ProposalActionResult(proposal, False, "nudge_limit")
            nudges[target_user_id] = "pending"
            updated = replace(proposal, nudge_count=proposal.nudge_count + 1)
            self.proposals[proposal_id] = updated
            return ProposalActionResult(updated, True, "nudge_pending")

    async def mark_nudge_state(
        self, proposal_id: str, target_user_id: str, state: str
    ) -> None:
        self.nudges[proposal_id][target_user_id] = state

    async def get_nudges_enabled(self, guild_id: str, user_id: str) -> bool:
        return self.nudge_preferences.get((guild_id, user_id), True)

    async def set_nudges_enabled(
        self, guild_id: str, user_id: str, enabled: bool
    ) -> None:
        self.nudge_preferences[(guild_id, user_id)] = enabled

    async def set_guild_subscription(
        self, guild_id: str, user_id: str, enabled: bool
    ) -> bool:
        users = self.guild_users.setdefault(guild_id, set())
        before = user_id in users
        if enabled:
            users.add(user_id)
        else:
            users.discard(user_id)
        return before != enabled

    async def get_guild_subscription(self, guild_id: str, user_id: str) -> bool:
        return user_id in self.guild_users.get(guild_id, set())

    async def guild_subscribers(self, guild_id: str) -> list[str]:
        return sorted(self.guild_users.get(guild_id, set()))

    async def add_proposal_subscription(self, proposal_id: str, user_id: str) -> bool:
        return await self.set_proposal_subscription(proposal_id, user_id, True)

    async def set_proposal_subscription(
        self, proposal_id: str, user_id: str, enabled: bool
    ) -> bool:
        users = self.subscribers.setdefault(proposal_id, set())
        was_enabled = user_id in users
        if enabled:
            users.add(user_id)
        else:
            users.discard(user_id)
        return was_enabled != enabled

    async def get_proposal_subscription(self, proposal_id: str, user_id: str) -> bool:
        return user_id in self.subscribers.get(proposal_id, set())

    async def proposal_subscribers(self, proposal_id: str) -> list[str]:
        return sorted(self.subscribers.get(proposal_id, set()))

    async def notification_delivered(
        self, proposal_id: str, event: str, user_id: str
    ) -> bool:
        return (proposal_id, event, user_id) in self.delivered

    async def mark_notification_delivered(
        self, proposal_id: str, event: str, user_id: str
    ) -> None:
        self.delivered.add((proposal_id, event, user_id))

    async def mark_announcement_synced(self, proposal_id: str) -> None:
        self.proposals[proposal_id] = replace(
            self.proposals[proposal_id], announcement_synced=True
        )

    async def mark_effects_complete(self, proposal_id: str) -> None:
        self.proposals[proposal_id] = replace(
            self.proposals[proposal_id], effects_complete=True
        )

    async def set_workspace_job(
        self,
        job_id: str,
        guild_id: str,
        requester_hash: str,
        action: str,
        status: str,
        message: str,
        proposal_id: str | None,
        now: datetime,
        ttl_seconds: int,
    ) -> WorkspaceJob:
        from datetime import timedelta

        previous = self.workspace_jobs.get(job_id)
        job = WorkspaceJob(
            id=job_id,
            guild_id=guild_id,
            requester_hash=requester_hash,
            action=action,
            status=status,
            message=message,
            proposal_id=proposal_id,
            created_at=previous.created_at if previous else now,
            updated_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self.workspace_jobs[job_id] = job
        return job

    async def get_workspace_job(self, job_id: str) -> WorkspaceJob | None:
        return self.workspace_jobs.get(job_id)

    async def create_workspace_launch(
        self,
        code_hash: str,
        user_id: str,
        guild_id: str,
        guild_ids: list[str],
        proposal_id: str | None,
        now: datetime,
        ttl_seconds: int,
    ) -> WorkspaceLaunch:
        from datetime import timedelta

        launch = WorkspaceLaunch(
            user_id=user_id,
            guild_id=guild_id,
            guild_ids=tuple(dict.fromkeys(guild_ids)),
            proposal_id=proposal_id,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self.workspace_launches[code_hash] = launch
        return launch

    async def consume_workspace_launch(
        self, code_hash: str, now: datetime
    ) -> WorkspaceLaunch | None:
        launch = self.workspace_launches.get(code_hash)
        if not launch or launch.consumed_at or now >= launch.expires_at:
            return None
        consumed = replace(launch, consumed_at=now)
        self.workspace_launches[code_hash] = consumed
        return consumed


class FakeTasks:
    def __init__(self) -> None:
        self.interactions: list[dict[str, Any]] = []
        self.deadlines: dict[str, datetime] = {}
        self.deleted: list[str] = []
        self.missing: set[str] = set()
        self.workspace: list[dict[str, Any]] = []

    async def enqueue_interaction(self, payload: dict[str, Any]) -> str:
        self.interactions.append(payload)
        return f"tasks/interaction-{payload['id']}"

    async def enqueue_workspace(self, payload: dict[str, Any]) -> str:
        self.workspace.append(payload)
        return f"tasks/workspace-{payload['id']}"

    async def ensure_deadline(
        self,
        proposal_id: str,
        deadline_at: datetime,
        *,
        repair_suffix: str | None = None,
    ) -> str:
        name = f"tasks/finalize-{proposal_id}"
        if repair_suffix:
            name += f"-repair-{repair_suffix}"
        self.deadlines[name] = deadline_at
        return name

    async def exists(self, task_name: str) -> bool:
        return task_name in self.deadlines and task_name not in self.missing

    async def delete(self, task_name: str | None) -> None:
        if task_name:
            self.deleted.append(task_name)
            self.deadlines.pop(task_name, None)


class FakeDiscord:
    def __init__(self) -> None:
        self.responses: list[str] = []
        self.response_components: list[list[dict[str, object]] | None] = []
        self.announcements: list[Proposal] = []
        self.synced: list[Proposal] = []
        self.outcomes: list[Proposal] = []
        self.deleted_history: list[Proposal] = []
        self.dms: list[tuple[str, str]] = []
        self.dm_failures: set[str] = set()
        self.channels: dict[str, tuple[str, str]] = {
            "channel": ("guild", "Test Guild"),
            "channel-2": ("guild-2", "Second Guild"),
        }
        self.members: dict[tuple[str, str], dict[str, object]] = {
            ("guild", "target"): {"user": {"id": "target", "bot": False}},
            ("guild", "bot"): {"user": {"id": "bot", "bot": True}},
        }

    async def edit_interaction_response(
        self,
        token: str,
        content: str,
        *,
        clear_components: bool = False,
        components: list[dict[str, object]] | None = None,
    ) -> None:
        self.responses.append(content)
        self.response_components.append(components)

    async def create_proposal_announcement(self, proposal: Proposal) -> str:
        self.announcements.append(proposal)
        return "999"

    async def sync_proposal_announcement(self, proposal: Proposal) -> str:
        self.synced.append(proposal)
        return proposal.message_id or "999"

    async def create_outcome_reply(self, proposal: Proposal) -> str:
        self.outcomes.append(proposal)
        return "outcome-999"

    async def delete_proposal_history_messages(self, proposal: Proposal) -> None:
        self.deleted_history.append(proposal)

    async def get_guild_member(self, guild_id: str, user_id: str) -> dict[str, object]:
        from viteoh.discord_api import DiscordAPIError

        member = self.members.get((guild_id, user_id))
        if not member:
            raise DiscordAPIError(404, "member not found")
        return member

    async def search_guild_members(
        self, guild_id: str, query: str, *, limit: int = 8
    ) -> list[dict[str, object]]:
        normalized = query.casefold()
        return [
            member
            for (member_guild, _), member in self.members.items()
            if member_guild == guild_id
            and isinstance(member.get("user"), dict)
            and normalized
            in str(
                (member.get("user") or {}).get("global_name")
                or (member.get("user") or {}).get("username")
                or (member.get("user") or {}).get("id")
            ).casefold()
        ][:limit]

    async def get_workspace_access(
        self, guild_id: str, user_id: str
    ) -> dict[str, object]:
        if guild_id not in {"guild", "guild-2"}:
            from viteoh.discord_api import DiscordAPIError

            raise DiscordAPIError(404, "guild not found")
        return {
            "guild_id": guild_id,
            "guild_name": ("Test Guild" if guild_id == "guild" else "Second Guild"),
            "display_name": user_id,
            "is_member": True,
            "can_manage": user_id in {"admin", "owner"},
            "visible_channel_ids": ["channel" if guild_id == "guild" else "channel-2"],
            "output_channels": [
                {
                    "id": "channel" if guild_id == "guild" else "channel-2",
                    "name": "proposals",
                    "bot_ready": True,
                }
            ],
        }

    async def validate_output_channel(self, guild_id: str, channel_id: str) -> str:
        channel = self.channels.get(channel_id)
        if not channel or channel[0] != guild_id:
            from viteoh.discord_api import DiscordAPIError

            raise DiscordAPIError(
                400, "The selected channel must belong to this server."
            )
        return channel[1]

    async def send_dm(self, user_id: str, content: str, *, event_key: str) -> None:
        if user_id in self.dm_failures:
            from viteoh.discord_api import DiscordAPIError

            raise DiscordAPIError(403, "DMs closed")
        self.dms.append((user_id, content))
