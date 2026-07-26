import hashlib
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Protocol

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from viteoh.domain import (
    CreateProposalResult,
    GuildConfig,
    Proposal,
    ProposalActionResult,
    ProposalStatus,
    ProposalTemplate,
    TemplateMutationResult,
    TransitionResult,
    WorkspaceJob,
    WorkspaceLaunch,
)
from viteoh.templates import BUILTIN_TEMPLATES, MAX_CUSTOM_TEMPLATES, builtin_template


def normalize_title(value: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if not normalized or len(normalized) > 100:
        raise ValueError("Proposal titles must contain 1 to 100 characters")
    return normalized


def reservation_id(guild_id: str, normalized_title: str) -> str:
    return hashlib.sha256(f"{guild_id}\0{normalized_title}".encode()).hexdigest()


class Repository(Protocol):
    async def get_guild_config(self, guild_id: str) -> GuildConfig | None: ...

    async def list_guild_configs(
        self, guild_ids: Sequence[str]
    ) -> Sequence[GuildConfig]: ...

    async def set_guild_config(
        self,
        guild_id: str,
        guild_name: str,
        output_channel_id: str,
        proposal_timeout_seconds: int,
        configured_by: str,
        now: datetime,
    ) -> GuildConfig: ...

    async def create_proposal(
        self,
        interaction_id: str,
        guild_id: str,
        guild_name: str,
        output_channel_id: str,
        title: str,
        normalized_title: str,
        context: str,
        template_id: str,
        template_name: str,
        created_at: datetime,
        deadline_at: datetime,
    ) -> CreateProposalResult: ...

    async def get_proposal(self, proposal_id: str) -> Proposal | None: ...

    async def get_proposal_for_interaction(
        self, interaction_id: str
    ) -> Proposal | None: ...

    async def list_active(self, guild_id: str | None = None) -> Sequence[Proposal]: ...

    async def list_guild_proposals(
        self,
        guild_id: str,
        *,
        limit: int = 50,
        before: datetime | None = None,
    ) -> Sequence[Proposal]: ...

    async def list_pending_terminal_effects(self) -> Sequence[Proposal]: ...

    async def mark_task_scheduled(
        self, proposal_id: str, task_name: str
    ) -> Proposal | None: ...

    async def set_message_id(
        self, proposal_id: str, message_id: str
    ) -> Proposal | None: ...

    async def mark_rendered(
        self, proposal_id: str, render_version: int
    ) -> Proposal | None: ...

    async def set_outcome_message_id(
        self, proposal_id: str, message_id: str
    ) -> Proposal | None: ...

    async def transition(
        self,
        proposal_id: str,
        status: ProposalStatus,
        now: datetime,
        veto_reason: str = "",
    ) -> TransitionResult: ...

    async def get_template(
        self, guild_id: str, template_id: str
    ) -> ProposalTemplate | None: ...

    async def list_templates(self, guild_id: str) -> Sequence[ProposalTemplate]: ...

    async def save_template(
        self,
        guild_id: str,
        template_id: str | None,
        name: str,
        normalized_name: str,
        description: str,
        subject_label: str,
        context_label: str,
        title_format: str,
        context_required: bool,
        user_id: str,
        now: datetime,
    ) -> TemplateMutationResult: ...

    async def delete_template(
        self, guild_id: str, template_id: str
    ) -> TemplateMutationResult: ...

    async def acknowledge(
        self, proposal_id: str, user_id: str, now: datetime
    ) -> ProposalActionResult: ...

    async def reserve_nudge(
        self, proposal_id: str, target_user_id: str, now: datetime
    ) -> ProposalActionResult: ...

    async def mark_nudge_state(
        self, proposal_id: str, target_user_id: str, state: str
    ) -> None: ...

    async def get_nudges_enabled(self, guild_id: str, user_id: str) -> bool: ...

    async def set_nudges_enabled(
        self, guild_id: str, user_id: str, enabled: bool
    ) -> None: ...

    async def set_guild_subscription(
        self, guild_id: str, user_id: str, enabled: bool
    ) -> bool: ...

    async def get_guild_subscription(self, guild_id: str, user_id: str) -> bool: ...

    async def guild_subscribers(self, guild_id: str) -> Sequence[str]: ...

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
    ) -> WorkspaceJob: ...

    async def get_workspace_job(self, job_id: str) -> WorkspaceJob | None: ...

    async def create_workspace_launch(
        self,
        code_hash: str,
        user_id: str,
        guild_id: str,
        proposal_id: str | None,
        now: datetime,
        ttl_seconds: int,
    ) -> WorkspaceLaunch: ...

    async def consume_workspace_launch(
        self, code_hash: str, now: datetime
    ) -> WorkspaceLaunch | None: ...


class FirestoreRepository:
    def __init__(self, client: firestore.AsyncClient) -> None:
        self.client = client

    async def get_guild_config(self, guild_id: str) -> GuildConfig | None:
        snapshot = await self.client.collection("guilds").document(guild_id).get()
        if not snapshot.exists:
            return None
        return GuildConfig.from_document(snapshot.id, snapshot.to_dict() or {})

    async def list_guild_configs(
        self, guild_ids: Sequence[str]
    ) -> Sequence[GuildConfig]:
        configs: list[GuildConfig] = []
        for guild_id in guild_ids:
            config = await self.get_guild_config(guild_id)
            if config:
                configs.append(config)
        return sorted(configs, key=lambda item: item.guild_name.casefold())

    async def set_guild_config(
        self,
        guild_id: str,
        guild_name: str,
        output_channel_id: str,
        proposal_timeout_seconds: int,
        configured_by: str,
        now: datetime,
    ) -> GuildConfig:
        ref = self.client.collection("guilds").document(guild_id)
        previous = await ref.get()
        previous_data = previous.to_dict() or {}
        created_at = previous_data.get("created_at", now)
        data = {
            "guild_name": guild_name,
            "output_channel_id": output_channel_id,
            "proposal_timeout_seconds": proposal_timeout_seconds,
            "configured_by": configured_by,
            "created_at": created_at,
            "updated_at": now,
            "custom_template_count": int(previous_data.get("custom_template_count", 0)),
        }
        await ref.set(data)
        return GuildConfig.from_document(guild_id, data)

    async def create_proposal(
        self,
        interaction_id: str,
        guild_id: str,
        guild_name: str,
        output_channel_id: str,
        title: str,
        normalized_title: str,
        context: str,
        template_id: str,
        template_name: str,
        created_at: datetime,
        deadline_at: datetime,
    ) -> CreateProposalResult:
        proposal_id = str(uuid.uuid4())
        reserve_id = reservation_id(guild_id, normalized_title)
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
                "guild_id": guild_id,
                "guild_name": guild_name,
                "output_channel_id": output_channel_id,
                "title": title,
                "normalized_title": normalized_title,
                "context": context,
                "template_id": template_id,
                "template_name": template_name,
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
                "acknowledgement_count": 0,
                "nudge_count": 0,
                "announcement_version": 0,
                "render_version": 0,
                "veto_reason": "",
                "outcome_message_id": None,
            }
            transaction.create(proposal_ref, proposal_data)
            transaction.create(
                reservation_ref,
                {
                    "proposal_id": proposal_id,
                    "guild_id": guild_id,
                    "normalized_title": normalized_title,
                    "created_at": created_at,
                },
            )
            transaction.create(
                interaction_ref,
                {
                    "kind": "new",
                    "guild_id": guild_id,
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

    async def get_proposal_for_interaction(
        self, interaction_id: str
    ) -> Proposal | None:
        snapshot = (
            await self.client.collection("interactions").document(interaction_id).get()
        )
        if not snapshot.exists:
            return None
        proposal_id = str((snapshot.to_dict() or {}).get("proposal_id", ""))
        return await self.get_proposal(proposal_id) if proposal_id else None

    async def list_active(self, guild_id: str | None = None) -> Sequence[Proposal]:
        query = self.client.collection("proposals").where(
            filter=FieldFilter("status", "==", ProposalStatus.ACTIVE.value)
        )
        if guild_id is not None:
            query = query.where(filter=FieldFilter("guild_id", "==", guild_id))
        return [
            Proposal.from_document(snapshot.id, snapshot.to_dict() or {})
            async for snapshot in query.stream()
        ]

    async def list_guild_proposals(
        self,
        guild_id: str,
        *,
        limit: int = 50,
        before: datetime | None = None,
    ) -> Sequence[Proposal]:
        query = (
            self.client.collection("proposals")
            .where(filter=FieldFilter("guild_id", "==", guild_id))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
        )
        if before is not None:
            query = query.start_after({"created_at": before})
        query = query.limit(max(1, min(limit, 100)))
        return [
            Proposal.from_document(snapshot.id, snapshot.to_dict() or {})
            async for snapshot in query.stream()
        ]

    async def get_template(
        self, guild_id: str, template_id: str
    ) -> ProposalTemplate | None:
        builtin = builtin_template(template_id)
        if builtin:
            return replace(builtin, guild_id=guild_id)
        snapshot = await (
            self.client.collection("guilds")
            .document(guild_id)
            .collection("templates")
            .document(template_id)
            .get()
        )
        if not snapshot.exists:
            return None
        return ProposalTemplate.from_document(
            guild_id, snapshot.id, snapshot.to_dict() or {}
        )

    async def list_templates(self, guild_id: str) -> Sequence[ProposalTemplate]:
        custom = [
            ProposalTemplate.from_document(
                guild_id, snapshot.id, snapshot.to_dict() or {}
            )
            async for snapshot in (
                self.client.collection("guilds")
                .document(guild_id)
                .collection("templates")
                .stream()
            )
        ]
        return [
            *(replace(template, guild_id=guild_id) for template in BUILTIN_TEMPLATES),
            *sorted(custom, key=lambda template: template.normalized_name),
        ]

    async def save_template(
        self,
        guild_id: str,
        template_id: str | None,
        name: str,
        normalized_name: str,
        description: str,
        subject_label: str,
        context_label: str,
        title_format: str,
        context_required: bool,
        user_id: str,
        now: datetime,
    ) -> TemplateMutationResult:
        if template_id and builtin_template(template_id):
            return TemplateMutationResult(None, False, "builtin")
        template_id = template_id or str(uuid.uuid4())
        guild_ref = self.client.collection("guilds").document(guild_id)
        template_ref = guild_ref.collection("templates").document(template_id)
        name_id = hashlib.sha256(normalized_name.encode()).hexdigest()
        name_ref = guild_ref.collection("template_names").document(name_id)
        transaction = self.client.transaction()

        @firestore.async_transactional
        async def save_in_transaction(transaction: Any) -> TemplateMutationResult:
            guild_snapshot = await guild_ref.get(transaction=transaction)
            existing = await template_ref.get(transaction=transaction)
            claimed_name = await name_ref.get(transaction=transaction)
            if not guild_snapshot.exists:
                return TemplateMutationResult(None, False, "unconfigured")
            existing_data = existing.to_dict() or {}
            old_normalized = str(existing_data.get("normalized_name", ""))
            old_name_ref = (
                guild_ref.collection("template_names").document(
                    hashlib.sha256(old_normalized.encode()).hexdigest()
                )
                if old_normalized and old_normalized != normalized_name
                else None
            )
            old_claim = (
                await old_name_ref.get(transaction=transaction)
                if old_name_ref is not None
                else None
            )
            count = int(
                (guild_snapshot.to_dict() or {}).get("custom_template_count", 0)
            )
            if not existing.exists and count >= MAX_CUSTOM_TEMPLATES:
                return TemplateMutationResult(None, False, "limit")
            if (
                claimed_name.exists
                and str((claimed_name.to_dict() or {}).get("template_id", ""))
                != template_id
            ):
                return TemplateMutationResult(None, False, "duplicate_name")

            created_at = existing_data.get("created_at", now)
            created_by = str(existing_data.get("created_by", user_id))
            data = {
                "name": name,
                "normalized_name": normalized_name,
                "description": description,
                "subject_label": subject_label,
                "context_label": context_label,
                "title_format": title_format,
                "context_required": context_required,
                "created_by": created_by,
                "updated_by": user_id,
                "created_at": created_at,
                "updated_at": now,
            }
            transaction.set(template_ref, data)
            transaction.set(name_ref, {"template_id": template_id})
            if old_name_ref is not None and old_claim and old_claim.exists:
                if (
                    str((old_claim.to_dict() or {}).get("template_id", ""))
                    == template_id
                ):
                    transaction.delete(old_name_ref)
            if not existing.exists:
                transaction.update(guild_ref, {"custom_template_count": count + 1})
            return TemplateMutationResult(
                ProposalTemplate.from_document(guild_id, template_id, data),
                True,
                "created" if not existing.exists else "updated",
            )

        return await save_in_transaction(transaction)

    async def delete_template(
        self, guild_id: str, template_id: str
    ) -> TemplateMutationResult:
        if builtin_template(template_id):
            return TemplateMutationResult(None, False, "builtin")
        guild_ref = self.client.collection("guilds").document(guild_id)
        template_ref = guild_ref.collection("templates").document(template_id)
        transaction = self.client.transaction()

        @firestore.async_transactional
        async def delete_in_transaction(transaction: Any) -> TemplateMutationResult:
            guild_snapshot = await guild_ref.get(transaction=transaction)
            snapshot = await template_ref.get(transaction=transaction)
            if not snapshot.exists:
                return TemplateMutationResult(None, False, "not_found")
            template = ProposalTemplate.from_document(
                guild_id, snapshot.id, snapshot.to_dict() or {}
            )
            name_ref = guild_ref.collection("template_names").document(
                hashlib.sha256(template.normalized_name.encode()).hexdigest()
            )
            name_snapshot = await name_ref.get(transaction=transaction)
            count = int(
                (guild_snapshot.to_dict() or {}).get("custom_template_count", 0)
            )
            transaction.delete(template_ref)
            if (
                name_snapshot.exists
                and str((name_snapshot.to_dict() or {}).get("template_id", ""))
                == template_id
            ):
                transaction.delete(name_ref)
            if guild_snapshot.exists:
                transaction.update(
                    guild_ref, {"custom_template_count": max(0, count - 1)}
                )
            return TemplateMutationResult(template, True, "deleted")

        return await delete_in_transaction(transaction)

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

    async def mark_rendered(
        self, proposal_id: str, render_version: int
    ) -> Proposal | None:
        ref = self.client.collection("proposals").document(proposal_id)
        await ref.update({"render_version": render_version})
        return await self.get_proposal(proposal_id)

    async def set_outcome_message_id(
        self, proposal_id: str, message_id: str
    ) -> Proposal | None:
        ref = self.client.collection("proposals").document(proposal_id)
        await ref.update({"outcome_message_id": message_id})
        return await self.get_proposal(proposal_id)

    async def transition(
        self,
        proposal_id: str,
        status: ProposalStatus,
        now: datetime,
        veto_reason: str = "",
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
                    "announcement_version": proposal.announcement_version + 1,
                    "veto_reason": (
                        veto_reason if status is ProposalStatus.VETOED else ""
                    ),
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
                announcement_version=proposal.announcement_version + 1,
                veto_reason=veto_reason if status is ProposalStatus.VETOED else "",
            )
            return TransitionResult(terminal, True, "transitioned")

        return await transition_in_transaction(transaction)

    async def acknowledge(
        self, proposal_id: str, user_id: str, now: datetime
    ) -> ProposalActionResult:
        proposal_ref = self.client.collection("proposals").document(proposal_id)
        acknowledgement_ref = proposal_ref.collection("acknowledgements").document(
            user_id
        )
        transaction = self.client.transaction()

        @firestore.async_transactional
        async def acknowledge_in_transaction(transaction: Any) -> ProposalActionResult:
            proposal_snapshot = await proposal_ref.get(transaction=transaction)
            acknowledgement = await acknowledgement_ref.get(transaction=transaction)
            if not proposal_snapshot.exists:
                return ProposalActionResult(None, False, "not_found")
            proposal = Proposal.from_document(
                proposal_snapshot.id, proposal_snapshot.to_dict() or {}
            )
            if proposal.status is not ProposalStatus.ACTIVE:
                return ProposalActionResult(proposal, False, "already_terminal")
            if now >= proposal.deadline_at:
                return ProposalActionResult(proposal, False, "deadline_elapsed")
            if acknowledgement.exists:
                return ProposalActionResult(proposal, False, "already_acknowledged")
            updated = replace(
                proposal,
                acknowledgement_count=proposal.acknowledgement_count + 1,
                announcement_version=proposal.announcement_version + 1,
            )
            transaction.create(acknowledgement_ref, {"created_at": now})
            transaction.update(
                proposal_ref,
                {
                    "acknowledgement_count": updated.acknowledgement_count,
                    "announcement_version": updated.announcement_version,
                },
            )
            return ProposalActionResult(updated, True, "acknowledged")

        return await acknowledge_in_transaction(transaction)

    async def reserve_nudge(
        self, proposal_id: str, target_user_id: str, now: datetime
    ) -> ProposalActionResult:
        proposal_ref = self.client.collection("proposals").document(proposal_id)
        nudge_ref = proposal_ref.collection("nudges").document(target_user_id)
        transaction = self.client.transaction()

        @firestore.async_transactional
        async def reserve_in_transaction(transaction: Any) -> ProposalActionResult:
            proposal_snapshot = await proposal_ref.get(transaction=transaction)
            nudge = await nudge_ref.get(transaction=transaction)
            if not proposal_snapshot.exists:
                return ProposalActionResult(None, False, "not_found")
            proposal = Proposal.from_document(
                proposal_snapshot.id, proposal_snapshot.to_dict() or {}
            )
            preference_ref = (
                self.client.collection("guilds")
                .document(proposal.guild_id)
                .collection("preferences")
                .document(target_user_id)
            )
            preference = await preference_ref.get(transaction=transaction)
            if proposal.status is not ProposalStatus.ACTIVE:
                return ProposalActionResult(proposal, False, "already_terminal")
            if now >= proposal.deadline_at:
                return ProposalActionResult(proposal, False, "deadline_elapsed")
            if preference.exists and not bool(
                (preference.to_dict() or {}).get("nudges_enabled", True)
            ):
                return ProposalActionResult(proposal, False, "nudges_disabled")
            if nudge.exists:
                state = str((nudge.to_dict() or {}).get("state", "pending"))
                return ProposalActionResult(proposal, False, f"nudge_{state}")
            if proposal.nudge_count >= 10:
                return ProposalActionResult(proposal, False, "nudge_limit")
            updated = replace(proposal, nudge_count=proposal.nudge_count + 1)
            transaction.create(
                nudge_ref,
                {
                    "user_id": target_user_id,
                    "state": "pending",
                    "created_at": now,
                },
            )
            transaction.update(proposal_ref, {"nudge_count": updated.nudge_count})
            return ProposalActionResult(updated, True, "nudge_pending")

        return await reserve_in_transaction(transaction)

    async def mark_nudge_state(
        self, proposal_id: str, target_user_id: str, state: str
    ) -> None:
        await (
            self.client.collection("proposals")
            .document(proposal_id)
            .collection("nudges")
            .document(target_user_id)
            .update(
                {
                    "state": state,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                }
            )
        )

    async def get_nudges_enabled(self, guild_id: str, user_id: str) -> bool:
        snapshot = await (
            self.client.collection("guilds")
            .document(guild_id)
            .collection("preferences")
            .document(user_id)
            .get()
        )
        return bool(
            not snapshot.exists
            or (snapshot.to_dict() or {}).get("nudges_enabled", True)
        )

    async def set_nudges_enabled(
        self, guild_id: str, user_id: str, enabled: bool
    ) -> None:
        await (
            self.client.collection("guilds")
            .document(guild_id)
            .collection("preferences")
            .document(user_id)
            .set(
                {
                    "nudges_enabled": enabled,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
        )

    async def set_guild_subscription(
        self, guild_id: str, user_id: str, enabled: bool
    ) -> bool:
        ref = (
            self.client.collection("guilds")
            .document(guild_id)
            .collection("subscribers")
            .document(user_id)
        )
        previous = await ref.get()
        was_enabled = bool(previous.exists)
        if not enabled:
            if previous.exists:
                await ref.delete()
            return was_enabled
        await ref.set(
            {
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
        )
        return not was_enabled

    async def get_guild_subscription(self, guild_id: str, user_id: str) -> bool:
        snapshot = await (
            self.client.collection("guilds")
            .document(guild_id)
            .collection("subscribers")
            .document(user_id)
            .get()
        )
        return bool(snapshot.exists)

    async def guild_subscribers(self, guild_id: str) -> Sequence[str]:
        query = (
            self.client.collection("guilds")
            .document(guild_id)
            .collection("subscribers")
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
        ref = self.client.collection("workspace_jobs").document(job_id)
        previous = await ref.get()
        previous_data = previous.to_dict() or {}
        data = {
            "guild_id": guild_id,
            "requester_hash": requester_hash,
            "action": action,
            "status": status,
            "message": message,
            "proposal_id": proposal_id,
            "created_at": previous_data.get("created_at", now),
            "updated_at": now,
            "expires_at": now + timedelta(seconds=ttl_seconds),
        }
        await ref.set(data)
        return WorkspaceJob.from_document(job_id, data)

    async def get_workspace_job(self, job_id: str) -> WorkspaceJob | None:
        snapshot = await self.client.collection("workspace_jobs").document(job_id).get()
        if not snapshot.exists:
            return None
        return WorkspaceJob.from_document(snapshot.id, snapshot.to_dict() or {})

    async def create_workspace_launch(
        self,
        code_hash: str,
        user_id: str,
        guild_id: str,
        proposal_id: str | None,
        now: datetime,
        ttl_seconds: int,
    ) -> WorkspaceLaunch:
        data = {
            "user_id": user_id,
            "guild_id": guild_id,
            "proposal_id": proposal_id,
            "created_at": now,
            "expires_at": now + timedelta(seconds=ttl_seconds),
            "consumed_at": None,
        }
        await (
            self.client.collection("workspace_launches")
            .document(code_hash)
            .create(data)
        )
        return WorkspaceLaunch.from_document(data)

    async def consume_workspace_launch(
        self, code_hash: str, now: datetime
    ) -> WorkspaceLaunch | None:
        ref = self.client.collection("workspace_launches").document(code_hash)
        transaction = self.client.transaction()

        @firestore.async_transactional
        async def consume(transaction: Any) -> WorkspaceLaunch | None:
            snapshot = await ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            launch = WorkspaceLaunch.from_document(snapshot.to_dict() or {})
            if launch.consumed_at or now >= launch.expires_at:
                return None
            transaction.update(ref, {"consumed_at": now})
            return replace(launch, consumed_at=now)

        return await consume(transaction)
