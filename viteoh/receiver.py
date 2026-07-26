import json
import logging
from typing import Any

from viteoh.commands import HELP_TEXT, command_path_and_options, focused_option
from viteoh.components import (
    delete_confirmation_buttons,
    modal,
    parse_component_id,
    text_input,
    user_select,
)
from viteoh.config import Settings
from viteoh.domain import ProposalStatus, ProposalType
from viteoh.repository import Repository
from viteoh.security import SignatureVerifier
from viteoh.tasks import TaskDispatcher

logger = logging.getLogger(__name__)

EPHEMERAL = 1 << 6
ADMINISTRATOR = 1 << 3
MANAGE_GUILD = 1 << 5


class InteractionReceiver:
    def __init__(
        self,
        settings: Settings,
        verifier: SignatureVerifier,
        tasks: TaskDispatcher,
        repository: Repository | None = None,
    ) -> None:
        self.settings = settings
        self.verifier = verifier
        self.tasks = tasks
        self.repository = repository

    async def receive(
        self,
        body: bytes,
        signature: str | None,
        timestamp: str | None,
    ) -> tuple[int, dict[str, Any]]:
        if not self.verifier.verify(body, signature, timestamp):
            logger.warning("Rejected invalid Discord request signature")
            return 401, {"detail": "invalid request signature"}
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return 400, {"detail": "invalid JSON"}
        if payload.get("type") == 1:
            return 200, {"type": 1}
        if not payload.get("guild_id") or not (
            (payload.get("member") or {}).get("user")
        ):
            return 200, _message("This bot can only be used inside a server.")

        data = payload.get("data") or {}
        if payload.get("type") == 4:
            return 200, await self._autocomplete(payload)
        if payload.get("type") == 2:
            try:
                direct = await self._direct_command(payload)
            except Exception:
                logger.exception("Could not prepare immediate Discord interaction")
                return 200, _message(
                    "The bot could not load this request. Please try again."
                )
            if direct is not None:
                return 200, direct
        if payload.get("type") == 3:
            parsed = parse_component_id(str(data.get("custom_id", "")))
            if parsed:
                scope, action, resource_id = parsed
                if scope == "proposal" and action == "veto":
                    return 200, _veto_modal(resource_id)
                if scope == "proposal" and action == "nudge":
                    return 200, _message(
                        "Choose one member to notify anonymously.",
                        user_select(
                            f"proposal:nudge-select:{resource_id}",
                            "Choose a server member",
                        ),
                    )
                if action == "cancel-delete":
                    return 200, {
                        "type": 7,
                        "data": {"content": "Deletion cancelled.", "components": []},
                    }
                if action == "confirm-delete":
                    try:
                        await self.tasks.enqueue_interaction(payload)
                    except Exception:
                        logger.exception("Could not enqueue Discord interaction")
                        return 200, _message(
                            "The bot could not accept this request. Please try again."
                        )
                    return 200, {"type": 6}
        try:
            await self.tasks.enqueue_interaction(payload)
        except Exception:
            logger.exception("Could not enqueue Discord interaction")
            return 200, _message(
                "The bot could not accept this request. Please try again."
            )
        return 200, {"type": 5, "data": {"flags": EPHEMERAL}}

    async def _direct_command(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        data = payload.get("data") or {}
        path, options = command_path_and_options(data)
        if path == ("proposal", "help"):
            return _message(HELP_TEXT)
        if path not in {
            ("proposal", "delete"),
            ("proposal", "type", "create"),
            ("proposal", "type", "edit"),
            ("proposal", "type", "delete"),
        }:
            return None
        if self.repository is None:
            return _message("The bot could not load this server's proposal settings.")

        guild_id = str(payload["guild_id"])
        if not _is_admin(payload, self.settings.discord_owner_user_id):
            return _message("You need Manage Server permission to use this command.")
        if not await self.repository.get_guild_config(guild_id):
            return _message("Run `/proposal configure` before managing proposal types.")

        if path == ("proposal", "delete"):
            proposal = await self.repository.get_proposal(
                str(options.get("proposal", ""))
            )
            if (
                not proposal
                or proposal.guild_id != guild_id
                or proposal.status is not ProposalStatus.ACTIVE
            ):
                return _message("Select a valid active proposal from this server.")
            return _message(
                f"Delete proposal **{proposal.title}**? This cannot be undone.",
                delete_confirmation_buttons("proposal", proposal.id),
            )

        if path == ("proposal", "type", "create"):
            return _type_modal(None)

        proposal_type = await self.repository.get_type(
            guild_id, str(options.get("type", ""))
        )
        if not proposal_type or proposal_type.builtin:
            return _message("Select a valid custom proposal type from this server.")
        if path == ("proposal", "type", "edit"):
            return _type_modal(proposal_type)
        return _message(
            f"Delete proposal type **{proposal_type.name}**? "
            "Existing proposals will be unchanged.",
            delete_confirmation_buttons("type", proposal_type.id),
        )

    async def _autocomplete(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.repository is None:
            return _choices([])
        data = payload.get("data") or {}
        path, focused = focused_option(data)
        if not focused:
            return _choices([])
        name = str(focused.get("name", ""))
        needle = " ".join(str(focused.get("value", "")).split()).casefold()
        guild_id = str(payload["guild_id"])
        try:
            if (
                path
                in {
                    ("proposal", "delete"),
                    ("proposal", "nudge"),
                }
                and name == "proposal"
            ):
                proposals = await self.repository.list_active(guild_id)
                proposal_matches = sorted(
                    (
                        proposal
                        for proposal in proposals
                        if not needle or needle in proposal.normalized_title
                    ),
                    key=lambda proposal: proposal.deadline_at,
                )[:25]
                return _choices(
                    [
                        {
                            "name": (
                                f"{proposal.title} · {proposal.type_name}"
                                if proposal.type_name
                                else proposal.title
                            )[:100],
                            "value": proposal.id,
                        }
                        for proposal in proposal_matches
                    ]
                )
            if (path == ("proposal", "create") and name == "type") or (
                path
                in {
                    ("proposal", "type", "edit"),
                    ("proposal", "type", "delete"),
                }
                and name == "type"
            ):
                proposal_types = await self.repository.list_types(guild_id)
                if path != ("proposal", "create"):
                    proposal_types = [
                        item for item in proposal_types if not item.builtin
                    ]
                type_matches = [
                    proposal_type
                    for proposal_type in proposal_types
                    if not needle
                    or needle in proposal_type.normalized_name
                    or needle in proposal_type.description.casefold()
                ][:25]
                return _choices(
                    [
                        {
                            "name": f"{item.name} — {item.description}"[:100],
                            "value": item.id,
                        }
                        for item in type_matches
                    ]
                )
        except Exception:
            logger.exception("Could not load Discord autocomplete choices")
        return _choices([])


def _is_admin(payload: dict[str, Any], owner_user_id: str) -> bool:
    user_id = str(((payload.get("member") or {}).get("user") or {}).get("id", ""))
    permissions = int((payload.get("member") or {}).get("permissions", 0))
    return bool(permissions & (ADMINISTRATOR | MANAGE_GUILD)) or bool(
        owner_user_id and user_id == owner_user_id
    )


def _message(
    content: str, components: list[dict[str, object]] | None = None
) -> dict[str, Any]:
    return {
        "type": 4,
        "data": {
            "content": content,
            "flags": EPHEMERAL,
            "allowed_mentions": {"parse": []},
            **({"components": components} if components else {}),
        },
    }


def _choices(choices: list[dict[str, str]]) -> dict[str, Any]:
    return {"type": 8, "data": {"choices": choices}}


def _veto_modal(proposal_id: str) -> dict[str, Any]:
    return modal(
        "Submit anonymous veto",
        f"proposal-veto|{proposal_id}",
        text_input(
            "reason",
            "Public reason (optional)",
            required=False,
            max_length=500,
            paragraph=True,
        ),
    )


def _type_modal(proposal_type: ProposalType | None) -> dict[str, Any]:
    action = "edit" if proposal_type else "create"
    type_id = proposal_type.id if proposal_type else "new"
    return modal(
        f"{action.title()} proposal type",
        f"type-{action}|{type_id}",
        text_input(
            "name",
            "Type name",
            required=True,
            max_length=50,
            value=proposal_type.name if proposal_type else None,
        ),
        text_input(
            "description",
            "Short description",
            required=True,
            max_length=100,
            value=proposal_type.description if proposal_type else None,
        ),
    )
