import json
import logging
from typing import Any

from viteoh.commands import HELP_TEXT, command_path_and_options, focused_option
from viteoh.components import (
    delete_confirmation_buttons,
    modal,
    parse_component_id,
    text_input,
)
from viteoh.config import Settings
from viteoh.domain import ProposalStatus, ProposalTemplate
from viteoh.repository import Repository
from viteoh.security import SignatureVerifier
from viteoh.tasks import TaskDispatcher
from viteoh.templates import subject_max_length

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
            ("proposal", "create"),
            ("proposal", "delete"),
            ("proposal", "template", "create"),
            ("proposal", "template", "edit"),
            ("proposal", "template", "delete"),
        }:
            return None
        if self.repository is None:
            return _message("The bot could not load this server's proposal settings.")

        guild_id = str(payload["guild_id"])
        if path == ("proposal", "create"):
            if not await self.repository.get_guild_config(guild_id):
                return _message(
                    "This server is not configured. A member with Manage Server "
                    "must run `/proposal configure` first."
                )
            template_id = str(options.get("template") or "builtin:general")
            template = await self.repository.get_template(guild_id, template_id)
            if not template:
                return _message("Select a valid proposal template from this server.")
            return _proposal_modal(template)

        if not _is_admin(payload, self.settings.discord_owner_user_id):
            return _message("You need Manage Server permission to use this command.")
        if not await self.repository.get_guild_config(guild_id):
            return _message("Run `/proposal configure` before managing templates.")

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

        if path == ("proposal", "template", "create"):
            required = bool(options.get("context_required", False))
            return _template_modal(None, required)

        template = await self.repository.get_template(
            guild_id, str(options.get("template", ""))
        )
        if not template or template.builtin:
            return _message("Select a valid custom template from this server.")
        if path == ("proposal", "template", "edit"):
            required = bool(options.get("context_required", template.context_required))
            return _template_modal(template, required)
        return _message(
            f"Delete template **{template.name}**? "
            "Existing proposals will be unchanged.",
            delete_confirmation_buttons("template", template.id),
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
                            "name": f"{proposal.title} · {proposal.template_name}"[
                                :100
                            ],
                            "value": proposal.id,
                        }
                        for proposal in proposal_matches
                    ]
                )
            if (path == ("proposal", "create") and name == "template") or (
                path
                in {
                    ("proposal", "template", "edit"),
                    ("proposal", "template", "delete"),
                }
                and name == "template"
            ):
                templates = await self.repository.list_templates(guild_id)
                if path != ("proposal", "create"):
                    templates = [item for item in templates if not item.builtin]
                template_matches = [
                    template
                    for template in templates
                    if not needle
                    or needle in template.normalized_name
                    or needle in template.description.casefold()
                ][:25]
                return _choices(
                    [
                        {
                            "name": f"{item.name} — {item.description}"[:100],
                            "value": item.id,
                        }
                        for item in template_matches
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


def _proposal_modal(template: ProposalTemplate) -> dict[str, Any]:
    return modal(
        f"Create · {template.name}",
        f"proposal-create|{template.id}",
        text_input(
            "subject",
            template.subject_label,
            required=True,
            max_length=subject_max_length(template),
        ),
        text_input(
            "context",
            template.context_label,
            required=template.context_required,
            max_length=1000,
            paragraph=True,
        ),
    )


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


def _template_modal(
    template: ProposalTemplate | None, context_required: bool
) -> dict[str, Any]:
    action = "edit" if template else "create"
    template_id = template.id if template else "new"
    return modal(
        f"{action.title()} proposal template",
        f"template-{action}|{template_id}|{int(context_required)}",
        text_input(
            "name",
            "Template name",
            required=True,
            max_length=50,
            value=template.name if template else None,
        ),
        text_input(
            "description",
            "Short description",
            required=True,
            max_length=100,
            value=template.description if template else None,
        ),
        text_input(
            "subject_label",
            "Subject prompt",
            required=True,
            max_length=45,
            value=template.subject_label if template else None,
        ),
        text_input(
            "context_label",
            "Context prompt",
            required=True,
            max_length=45,
            value=template.context_label if template else None,
        ),
        text_input(
            "title_format",
            "Title format (use {subject})",
            required=True,
            max_length=100,
            value=template.title_format if template else "{subject}",
        ),
    )
