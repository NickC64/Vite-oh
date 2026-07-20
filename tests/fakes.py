import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Any

from viteoh.domain import (
    CreateProposalResult,
    Proposal,
    ProposalStatus,
    TransitionResult,
)
from viteoh.repository import reservation_id


class FakeRepository:
    def __init__(self) -> None:
        self.proposals: dict[str, Proposal] = {}
        self.interactions: dict[str, str] = {}
        self.global_users: set[str] = set()
        self.subscribers: dict[str, set[str]] = {}
        self.delivered: set[tuple[str, str, str]] = set()
        self.lock = asyncio.Lock()
        self.next_id = "12345678-1234-1234-1234-123456789abc"

    async def create_proposal(
        self,
        interaction_id: str,
        display_name: str,
        normalized_name: str,
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
                and item.normalized_name == normalized_name
                for item in self.proposals.values()
            ):
                return CreateProposalResult(None, True)
            proposal = Proposal(
                id=self.next_id,
                display_name=display_name,
                normalized_name=normalized_name,
                reservation_id=reservation_id(normalized_name),
                status=ProposalStatus.ACTIVE,
                created_at=created_at,
                deadline_at=deadline_at,
            )
            self.proposals[proposal.id] = proposal
            self.interactions[interaction_id] = proposal.id
            return CreateProposalResult(proposal)

    async def get_proposal(self, proposal_id: str) -> Proposal | None:
        return self.proposals.get(proposal_id)

    async def list_active(self) -> list[Proposal]:
        return [
            proposal
            for proposal in self.proposals.values()
            if proposal.status is ProposalStatus.ACTIVE
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
            )
            self.proposals[proposal_id] = updated
            return TransitionResult(updated, True, "transitioned")

    async def set_global_subscription(self, user_id: str, enabled: bool) -> bool:
        before = user_id in self.global_users
        if enabled:
            self.global_users.add(user_id)
        else:
            self.global_users.discard(user_id)
        return before != enabled

    async def global_subscribers(self) -> list[str]:
        return sorted(self.global_users)

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

    async def edit_interaction_response(self, token: str, content: str) -> None:
        self.responses.append(content)

    async def create_proposal_announcement(self, proposal: Proposal) -> str:
        self.announcements.append(proposal)
        return "999"

    async def sync_terminal_announcement(self, proposal: Proposal) -> None:
        self.synced.append(proposal)

    async def send_dm(self, user_id: str, content: str, *, event_key: str) -> None:
        self.dms.append((user_id, content))
