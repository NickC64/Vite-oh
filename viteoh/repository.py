import hashlib
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from viteoh.domain import (
    CreateProposalResult,
    Proposal,
    ProposalStatus,
    TransitionResult,
)


def normalize_name(value: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if not normalized or len(normalized) > 100:
        raise ValueError("Proposal names must contain 1 to 100 characters")
    return normalized


def reservation_id(normalized_name: str) -> str:
    return hashlib.sha256(normalized_name.encode()).hexdigest()


class Repository(Protocol):
    async def create_proposal(
        self,
        interaction_id: str,
        display_name: str,
        normalized_name: str,
        created_at: datetime,
        deadline_at: datetime,
    ) -> CreateProposalResult: ...

    async def get_proposal(self, proposal_id: str) -> Proposal | None: ...

    async def list_active(self) -> Sequence[Proposal]: ...

    async def list_pending_terminal_effects(self) -> Sequence[Proposal]: ...

    async def mark_task_scheduled(
        self, proposal_id: str, task_name: str
    ) -> Proposal | None: ...

    async def set_message_id(
        self, proposal_id: str, message_id: str
    ) -> Proposal | None: ...

    async def transition(
        self,
        proposal_id: str,
        status: ProposalStatus,
        now: datetime,
    ) -> TransitionResult: ...

    async def set_global_subscription(self, user_id: str, enabled: bool) -> bool: ...

    async def global_subscribers(self) -> Sequence[str]: ...

    async def add_proposal_subscription(
        self, proposal_id: str, user_id: str
    ) -> bool: ...

    async def proposal_subscribers(self, proposal_id: str) -> Sequence[str]: ...

    async def notification_delivered(
        self, proposal_id: str, event: str, user_id: str
    ) -> bool: ...

    async def mark_notification_delivered(
        self, proposal_id: str, event: str, user_id: str
    ) -> None: ...

    async def mark_announcement_synced(self, proposal_id: str) -> None: ...

    async def mark_effects_complete(self, proposal_id: str) -> None: ...


class FirestoreRepository:
    def __init__(self, client: firestore.AsyncClient) -> None:
        self.client = client

    async def create_proposal(
        self,
        interaction_id: str,
        display_name: str,
        normalized_name: str,
        created_at: datetime,
        deadline_at: datetime,
    ) -> CreateProposalResult:
        proposal_id = str(uuid.uuid4())
        reserve_id = reservation_id(normalized_name)
        proposal_ref = self.client.collection("proposals").document(proposal_id)
        reservation_ref = self.client.collection("active_names").document(reserve_id)
        interaction_ref = self.client.collection("interactions").document(
            interaction_id
        )
        transaction = self.client.transaction()

        @firestore.async_transactional
        async def create_in_transaction(transaction: Any) -> CreateProposalResult:
            previous = await interaction_ref.get(transaction=transaction)
            if previous.exists:
                data = previous.to_dict() or {}
                previous_id = data.get("proposal_id")
                if previous_id:
                    snapshot = (
                        await self.client.collection("proposals")
                        .document(previous_id)
                        .get(transaction=transaction)
                    )
                    if snapshot.exists:
                        return CreateProposalResult(
                            Proposal.from_document(
                                snapshot.id, snapshot.to_dict() or {}
                            )
                        )
                return CreateProposalResult(None, bool(data.get("duplicate_name")))

            reservation = await reservation_ref.get(transaction=transaction)
            if reservation.exists:
                transaction.set(
                    interaction_ref,
                    {
                        "kind": "new",
                        "duplicate_name": True,
                        "processed_at": firestore.SERVER_TIMESTAMP,
                    },
                )
                return CreateProposalResult(None, True)

            proposal_data = {
                "display_name": display_name,
                "normalized_name": normalized_name,
                "reservation_id": reserve_id,
                "status": ProposalStatus.ACTIVE.value,
                "created_at": created_at,
                "deadline_at": deadline_at,
                "message_id": None,
                "deadline_task_name": None,
                "task_scheduled": False,
                "terminal_at": None,
                "announcement_synced": False,
                "effects_complete": False,
            }
            transaction.create(proposal_ref, proposal_data)
            transaction.create(
                reservation_ref,
                {
                    "proposal_id": proposal_id,
                    "normalized_name": normalized_name,
                    "created_at": created_at,
                },
            )
            transaction.create(
                interaction_ref,
                {
                    "kind": "new",
                    "proposal_id": proposal_id,
                    "processed_at": created_at,
                },
            )
            return CreateProposalResult(
                Proposal.from_document(proposal_id, proposal_data)
            )

        return await create_in_transaction(transaction)

    async def get_proposal(self, proposal_id: str) -> Proposal | None:
        snapshot = await self.client.collection("proposals").document(proposal_id).get()
        if not snapshot.exists:
            return None
        return Proposal.from_document(snapshot.id, snapshot.to_dict() or {})

    async def list_active(self) -> Sequence[Proposal]:
        query = self.client.collection("proposals").where(
            filter=FieldFilter("status", "==", ProposalStatus.ACTIVE.value)
        )
        return [
            Proposal.from_document(snapshot.id, snapshot.to_dict() or {})
            async for snapshot in query.stream()
        ]

    async def list_pending_terminal_effects(self) -> Sequence[Proposal]:
        query = self.client.collection("proposals").where(
            filter=FieldFilter("status", "!=", ProposalStatus.ACTIVE.value)
        )
        proposals = [
            Proposal.from_document(snapshot.id, snapshot.to_dict() or {})
            async for snapshot in query.stream()
        ]
        return [proposal for proposal in proposals if not proposal.effects_complete]

    async def mark_task_scheduled(
        self, proposal_id: str, task_name: str
    ) -> Proposal | None:
        ref = self.client.collection("proposals").document(proposal_id)
        await ref.update({"deadline_task_name": task_name, "task_scheduled": True})
        return await self.get_proposal(proposal_id)

    async def set_message_id(
        self, proposal_id: str, message_id: str
    ) -> Proposal | None:
        ref = self.client.collection("proposals").document(proposal_id)
        await ref.update({"message_id": message_id})
        return await self.get_proposal(proposal_id)

    async def transition(
        self,
        proposal_id: str,
        status: ProposalStatus,
        now: datetime,
    ) -> TransitionResult:
        proposal_ref = self.client.collection("proposals").document(proposal_id)
        transaction = self.client.transaction()

        @firestore.async_transactional
        async def transition_in_transaction(transaction: Any) -> TransitionResult:
            snapshot = await proposal_ref.get(transaction=transaction)
            if not snapshot.exists:
                return TransitionResult(None, False, "not_found")
            proposal = Proposal.from_document(snapshot.id, snapshot.to_dict() or {})
            if proposal.status is not ProposalStatus.ACTIVE:
                return TransitionResult(proposal, False, "already_terminal")
            if status is ProposalStatus.PASSED and now < proposal.deadline_at:
                return TransitionResult(proposal, False, "not_due")
            if status is ProposalStatus.VETOED and now >= proposal.deadline_at:
                return TransitionResult(proposal, False, "deadline_elapsed")

            reserve_ref = self.client.collection("active_names").document(
                proposal.reservation_id
            )
            reserve = await reserve_ref.get(transaction=transaction)
            transaction.update(
                proposal_ref,
                {
                    "status": status.value,
                    "terminal_at": now,
                    "announcement_synced": False,
                    "effects_complete": False,
                },
            )
            if (
                reserve.exists
                and (reserve.to_dict() or {}).get("proposal_id") == proposal_id
            ):
                transaction.delete(reserve_ref)
            terminal = replace(
                proposal,
                status=status,
                terminal_at=now,
                announcement_synced=False,
                effects_complete=False,
            )
            return TransitionResult(terminal, True, "transitioned")

        return await transition_in_transaction(transaction)

    async def set_global_subscription(self, user_id: str, enabled: bool) -> bool:
        ref = self.client.collection("users").document(user_id)
        previous = await ref.get()
        was_enabled = bool(
            previous.exists and (previous.to_dict() or {}).get("subscribed_to_all")
        )
        await ref.set(
            {
                "subscribed_to_all": enabled,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        return was_enabled != enabled

    async def global_subscribers(self) -> Sequence[str]:
        query = self.client.collection("users").where(
            filter=FieldFilter("subscribed_to_all", "==", True)
        )
        return [snapshot.id async for snapshot in query.stream()]

    async def add_proposal_subscription(self, proposal_id: str, user_id: str) -> bool:
        ref = (
            self.client.collection("proposals")
            .document(proposal_id)
            .collection("subscribers")
            .document(user_id)
        )
        snapshot = await ref.get()
        if snapshot.exists:
            return False
        await ref.create({"created_at": firestore.SERVER_TIMESTAMP})
        return True

    async def proposal_subscribers(self, proposal_id: str) -> Sequence[str]:
        query = (
            self.client.collection("proposals")
            .document(proposal_id)
            .collection("subscribers")
        )
        return [snapshot.id async for snapshot in query.stream()]

    def _notification_ref(self, proposal_id: str, event: str, user_id: str) -> Any:
        key = hashlib.sha256(f"{event}:{user_id}".encode()).hexdigest()
        return (
            self.client.collection("proposals")
            .document(proposal_id)
            .collection("notifications")
            .document(key)
        )

    async def notification_delivered(
        self, proposal_id: str, event: str, user_id: str
    ) -> bool:
        snapshot = await self._notification_ref(proposal_id, event, user_id).get()
        return bool(snapshot.exists and (snapshot.to_dict() or {}).get("delivered"))

    async def mark_notification_delivered(
        self, proposal_id: str, event: str, user_id: str
    ) -> None:
        await self._notification_ref(proposal_id, event, user_id).set(
            {
                "event": event,
                "user_id": user_id,
                "delivered": True,
                "delivered_at": firestore.SERVER_TIMESTAMP,
            }
        )

    async def mark_announcement_synced(self, proposal_id: str) -> None:
        await (
            self.client.collection("proposals")
            .document(proposal_id)
            .update({"announcement_synced": True})
        )

    async def mark_effects_complete(self, proposal_id: str) -> None:
        await (
            self.client.collection("proposals")
            .document(proposal_id)
            .update({"effects_complete": True})
        )
