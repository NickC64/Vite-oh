import asyncio
import hashlib
import logging
from typing import Any

import httpx

from viteoh.components import proposal_buttons
from viteoh.config import Settings
from viteoh.domain import Proposal, ProposalStatus

logger = logging.getLogger(__name__)


class DiscordAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        return self.status_code == 429 or self.status_code >= 500


class DiscordClient:
    def __init__(
        self, settings: Settings, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        headers = {
            "Authorization": f"Bot {self.settings.discord_bot_token}",
            "User-Agent": "DiscordBot (https://github.com/NickC64/Vite-oh, 1.0)",
        }
        for attempt in range(3):
            response = await self.client.request(
                method,
                f"{self.settings.discord_api_base_url}{path}",
                headers=headers,
                json=json,
            )
            if response.status_code == 429:
                delay = float(response.json().get("retry_after", 1))
                if attempt < 2:
                    await asyncio.sleep(min(delay, 5))
                    continue
            if response.status_code >= 500 and attempt < 2:
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code >= 400:
                raise DiscordAPIError(response.status_code, response.text)
            return response.json() if response.content else None
        raise AssertionError("request retry loop exhausted")

    async def edit_interaction_response(self, token: str, content: str) -> None:
        await self._request(
            "PATCH",
            f"/webhooks/{self.settings.discord_application_id}/{token}/messages/@original",
            json={"content": content, "allowed_mentions": {"parse": []}},
        )

    async def create_proposal_announcement(self, proposal: Proposal) -> str:
        nonce = _nonce(f"created:{proposal.id}")
        result = await self._request(
            "POST",
            f"/channels/{self.settings.discord_output_channel_id}/messages",
            json={
                "content": (
                    f"A member proposal for {proposal.display_name} was added, "
                    f"set to pass <t:{int(proposal.deadline_at.timestamp())}:R>."
                ),
                "components": proposal_buttons(proposal.id),
                "allowed_mentions": {"parse": []},
                "nonce": nonce,
                "enforce_nonce": True,
            },
        )
        if not result or "id" not in result:
            raise DiscordAPIError(502, "Discord did not return a message ID")
        return str(result["id"])

    async def sync_terminal_announcement(self, proposal: Proposal) -> None:
        status_text = {
            ProposalStatus.PASSED: "passed",
            ProposalStatus.VETOED: "been vetoed",
            ProposalStatus.DELETED: "been deleted by an admin",
        }[proposal.status]
        content = f"The proposal for {proposal.display_name} has {status_text}."
        if proposal.message_id:
            try:
                await self._request(
                    "PATCH",
                    (
                        f"/channels/{self.settings.discord_output_channel_id}"
                        f"/messages/{proposal.message_id}"
                    ),
                    json={
                        "content": content,
                        "components": [],
                        "allowed_mentions": {"parse": []},
                    },
                )
                return
            except DiscordAPIError as exc:
                if exc.status_code != 404:
                    raise
        await self._request(
            "POST",
            f"/channels/{self.settings.discord_output_channel_id}/messages",
            json={
                "content": content,
                "allowed_mentions": {"parse": []},
                "nonce": _nonce(f"{proposal.status}:{proposal.id}"),
                "enforce_nonce": True,
            },
        )

    async def send_dm(self, user_id: str, content: str, *, event_key: str) -> None:
        channel = await self._request(
            "POST", "/users/@me/channels", json={"recipient_id": user_id}
        )
        if not channel or "id" not in channel:
            raise DiscordAPIError(502, "Discord did not return a DM channel")
        await self._request(
            "POST",
            f"/channels/{channel['id']}/messages",
            json={
                "content": content,
                "allowed_mentions": {"parse": []},
                "nonce": _nonce(event_key),
                "enforce_nonce": True,
            },
        )


def _nonce(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:25]
