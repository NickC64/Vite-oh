import logging
from datetime import timedelta
from typing import Any

from viteoh.components import parse_component_id
from viteoh.config import Settings
from viteoh.discord_api import DiscordAPIError, DiscordClient
from viteoh.domain import Proposal, ProposalStatus, utcnow
from viteoh.repository import Repository, normalize_name
from viteoh.tasks import TaskDispatcher

logger = logging.getLogger(__name__)


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
        try:
            if interaction_type == 2:
                content = await self._command(payload, user_id)
            elif interaction_type == 3:
                content = await self._component(payload, user_id)
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

    async def _command(self, payload: dict[str, Any], user_id: str) -> str:
        data = payload.get("data") or {}
        command = data.get("name")
        options = {
            option["name"]: option.get("value") for option in data.get("options", [])
        }
        if command == "new":
            return await self._new(str(payload["id"]), str(options.get("name", "")))
        if command == "sub":
            changed = await self.repository.set_global_subscription(user_id, True)
            return (
                "You have subscribed to new proposal notifications."
                if changed
                else "You are already subscribed to new proposal notifications."
            )
        if command == "unsub":
            changed = await self.repository.set_global_subscription(user_id, False)
            return (
                "You have unsubscribed from new proposal notifications."
                if changed
                else "You are not currently subscribed to new proposal notifications."
            )
        if command == "view":
            proposals = await self.repository.list_active()
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
            if user_id != self.settings.discord_owner_user_id:
                return "Only the bot owner can use this command."
            return await self._delete(str(options.get("name", "")))
        return "Unknown command."

    async def _new(self, interaction_id: str, display_name: str) -> str:
        display_name = " ".join(display_name.split())
        try:
            normalized = normalize_name(display_name)
        except ValueError as exc:
            return str(exc)
        now = utcnow()
        deadline = now + timedelta(seconds=self.settings.proposal_timeout_seconds)
        result = await self.repository.create_proposal(
            interaction_id, display_name, normalized, now, deadline
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
            message_id = await self.discord.create_proposal_announcement(proposal)
            proposal = (
                await self.repository.set_message_id(proposal.id, message_id)
                or proposal
            )
        await self._notify_created(proposal)
        return (
            f"Proposal created successfully. It will pass "
            f"<t:{int(proposal.deadline_at.timestamp())}:R> unless vetoed."
        )

    async def _delete(self, display_name: str) -> str:
        try:
            normalized = normalize_name(display_name)
        except ValueError as exc:
            return str(exc)
        proposal = next(
            (
                item
                for item in await self.repository.list_active()
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

    async def _component(self, payload: dict[str, Any], user_id: str) -> str:
        parsed = parse_component_id(
            str((payload.get("data") or {}).get("custom_id", ""))
        )
        if not parsed:
            return "This control is no longer valid."
        action, proposal_id = parsed
        proposal = await self.repository.get_proposal(proposal_id)
        if not proposal or proposal.status is not ProposalStatus.ACTIVE:
            return "This proposal is no longer active."
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
        text = f"The proposal for {proposal.display_name} has {outcome}."
        await self._notify(proposal, event, subscribers, text)
        await self.repository.mark_effects_complete(proposal.id)

    async def _notify_created(self, proposal: Proposal) -> None:
        subscribers = await self.repository.global_subscribers()
        link = (
            f"https://discord.com/channels/{self.settings.discord_guild_id}/"
            f"{self.settings.discord_output_channel_id}/{proposal.message_id}"
        )
        await self._notify(
            proposal,
            "created",
            subscribers,
            f"A new proposal for {proposal.display_name} has been created: {link}",
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


def _user_id(payload: dict[str, Any]) -> str:
    member = payload.get("member") or {}
    user = member.get("user") or payload.get("user") or {}
    return str(user.get("id", ""))
