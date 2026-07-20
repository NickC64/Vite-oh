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
    TransitionResult,
)
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
        self.lock = asyncio.Lock()
        self.next_id = "12345678-1234-1234-1234-123456789abc"

    async def get_guild_config(self, guild_id: str) -> GuildConfig | None:
        return self.guilds.get(guild_id)

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
            )
            self.proposals[proposal.id] = proposal
            self.interactions[interaction_id] = proposal.id
            return CreateProposalResult(proposal)

    async def get_proposal(self, proposal_id: str) -> Proposal | None:
        return self.proposals.get(proposal_id)

    async def list_active(self, guild_id: str | None = None) -> list[Proposal]:
        return [
            proposal
            for proposal in self.proposals.values()
            if proposal.status is ProposalStatus.ACTIVE
            and (guild_id is None or proposal.guild_id == guild_id)
        ]

    async def list_pending_terminal_effects(self) -> list[Proposal]:
        return [
            proposal
            for proposal in self.proposals.values()
            if proposal.status is not ProposalStatus.ACTIVE
            and not proposal.effects_complete
        ]

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

    async def transition(
        self, proposal_id: str, status: ProposalStatus, now: datetime
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

    async def guild_subscribers(self, guild_id: str) -> list[str]:
        return sorted(self.guild_users.get(guild_id, set()))

    async def add_proposal_subscription(self, proposal_id: str, user_id: str) -> bool:
        users = self.subscribers.setdefault(proposal_id, set())
        before = len(users)
        users.add(user_id)
        return len(users) != before

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


class FakeTasks:
    def __init__(self) -> None:
        self.interactions: list[dict[str, Any]] = []
        self.deadlines: dict[str, datetime] = {}
        self.deleted: list[str] = []
        self.missing: set[str] = set()

    async def enqueue_interaction(self, payload: dict[str, Any]) -> str:
        self.interactions.append(payload)
        return f"tasks/interaction-{payload['id']}"

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
        self.announcements: list[Proposal] = []
        self.synced: list[Proposal] = []
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

    async def edit_interaction_response(self, token: str, content: str) -> None:
        self.responses.append(content)

    async def create_proposal_announcement(self, proposal: Proposal) -> str:
        self.announcements.append(proposal)
        return "999"

    async def sync_proposal_announcement(self, proposal: Proposal) -> None:
        self.synced.append(proposal)

    async def get_guild_member(self, guild_id: str, user_id: str) -> dict[str, object]:
        from viteoh.discord_api import DiscordAPIError

        member = self.members.get((guild_id, user_id))
        if not member:
            raise DiscordAPIError(404, "member not found")
        return member

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
