import json
import logging
from typing import Any

from viteoh.commands import HELP_TEXT
from viteoh.components import parse_component_id, veto_confirmation_buttons
from viteoh.config import Settings
from viteoh.security import SignatureVerifier
from viteoh.tasks import TaskDispatcher

logger = logging.getLogger(__name__)

EPHEMERAL = 1 << 6


class InteractionReceiver:
    def __init__(
        self,
        settings: Settings,
        verifier: SignatureVerifier,
        tasks: TaskDispatcher,
    ) -> None:
        self.settings = settings
        self.verifier = verifier
        self.tasks = tasks

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
        if payload.get("type") == 2 and data.get("name") == "help":
            return 200, _message(HELP_TEXT)
        if payload.get("type") == 3:
            parsed = parse_component_id(str(data.get("custom_id", "")))
            if parsed:
                action, proposal_id = parsed
                if action == "veto":
                    return 200, {
                        "type": 4,
                        "data": {
                            "content": "Are you sure you want to veto this proposal?",
                            "components": veto_confirmation_buttons(proposal_id),
                            "flags": EPHEMERAL,
                        },
                    }
                if action == "cancel-veto":
                    return 200, {
                        "type": 7,
                        "data": {"content": "Veto cancelled.", "components": []},
                    }
        try:
            await self.tasks.enqueue_interaction(payload)
        except Exception:
            logger.exception("Could not enqueue Discord interaction")
            return 200, _message(
                "The bot could not accept this request. Please try again."
            )
        return 200, {"type": 5, "data": {"flags": EPHEMERAL}}


def _message(content: str) -> dict[str, Any]:
    return {
        "type": 4,
        "data": {
            "content": content,
            "flags": EPHEMERAL,
            "allowed_mentions": {"parse": []},
        },
    }
