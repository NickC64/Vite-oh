import asyncio
import hashlib
import logging
from typing import Any

import httpx

from viteoh.components import proposal_buttons
from viteoh.config import Settings
from viteoh.domain import Proposal, ProposalStatus

logger = logging.getLogger(__name__)

ADMINISTRATOR = 1 << 3
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
EMBED_LINKS = 1 << 14
READ_MESSAGE_HISTORY = 1 << 16
REQUIRED_OUTPUT_PERMISSIONS = (
    VIEW_CHANNEL | SEND_MESSAGES | EMBED_LINKS | READ_MESSAGE_HISTORY
)
SUPPORTED_OUTPUT_CHANNEL_TYPES = {0, 5}


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
    ) -> Any:
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

    async def edit_interaction_response(
        self, token: str, content: str, *, clear_components: bool = False
    ) -> None:
        await self._request(
            "PATCH",
            f"/webhooks/{self.settings.discord_application_id}/{token}/messages/@original",
            json={
                "content": content,
                "allowed_mentions": {"parse": []},
                **({"components": []} if clear_components else {}),
            },
        )

    async def create_proposal_announcement(self, proposal: Proposal) -> str:
        nonce = _nonce(f"created:{proposal.id}")
        result = await self._request(
            "POST",
            f"/channels/{proposal.output_channel_id}/messages",
            json={
                "embeds": [_proposal_embed(proposal)],
                "components": proposal_buttons(proposal.id),
                "allowed_mentions": {"parse": []},
                "nonce": nonce,
                "enforce_nonce": True,
            },
        )
        if not result or "id" not in result:
            raise DiscordAPIError(502, "Discord did not return a message ID")
        return str(result["id"])

    async def sync_proposal_announcement(self, proposal: Proposal) -> str:
        embed = _proposal_embed(proposal)
        components = (
            proposal_buttons(proposal.id)
            if proposal.status is ProposalStatus.ACTIVE
            else []
        )
        if proposal.message_id:
            try:
                await self._request(
                    "PATCH",
                    (
                        f"/channels/{proposal.output_channel_id}"
                        f"/messages/{proposal.message_id}"
                    ),
                    json={
                        "content": "",
                        "embeds": [embed],
                        "components": components,
                        "allowed_mentions": {"parse": []},
                    },
                )
                return proposal.message_id
            except DiscordAPIError as exc:
                if exc.status_code != 404:
                    raise
        result = await self._request(
            "POST",
            f"/channels/{proposal.output_channel_id}/messages",
            json={
                "embeds": [embed],
                "components": components,
                "allowed_mentions": {"parse": []},
                "nonce": _nonce(f"{proposal.status}:{proposal.id}"),
                "enforce_nonce": True,
            },
        )
        if not result or "id" not in result:
            raise DiscordAPIError(502, "Discord did not return a message ID")
        return str(result["id"])

    async def create_outcome_reply(self, proposal: Proposal) -> str:
        if not proposal.message_id:
            raise DiscordAPIError(409, "Proposal has no canonical message")
        result = await self._request(
            "POST",
            f"/channels/{proposal.output_channel_id}/messages",
            json={
                "embeds": [_outcome_embed(proposal)],
                "message_reference": {
                    "message_id": proposal.message_id,
                    "channel_id": proposal.output_channel_id,
                    "guild_id": proposal.guild_id,
                    "fail_if_not_exists": False,
                },
                "allowed_mentions": {"parse": [], "replied_user": False},
                "nonce": _nonce(f"outcome:{proposal.status}:{proposal.id}"),
                "enforce_nonce": True,
            },
        )
        if not result or "id" not in result:
            raise DiscordAPIError(502, "Discord did not return an outcome message ID")
        return str(result["id"])

    async def delete_proposal_announcement(self, proposal: Proposal) -> None:
        if not proposal.message_id:
            return
        try:
            await self._request(
                "DELETE",
                f"/channels/{proposal.output_channel_id}/messages/{proposal.message_id}",
            )
        except DiscordAPIError as exc:
            if exc.status_code != 404:
                raise

    async def validate_output_channel(self, guild_id: str, channel_id: str) -> str:
        channel = await self._request("GET", f"/channels/{channel_id}")
        if not channel or str(channel.get("guild_id", "")) != guild_id:
            raise DiscordAPIError(400, "The selected channel is not in this server.")
        if int(channel.get("type", -1)) not in SUPPORTED_OUTPUT_CHANNEL_TYPES:
            raise DiscordAPIError(
                400, "Choose a text or announcement channel for proposals."
            )

        guild = await self._request("GET", f"/guilds/{guild_id}")
        bot_user = await self._request("GET", "/users/@me")
        bot_user_id = str((bot_user or {}).get("id", ""))
        if not bot_user_id:
            raise DiscordAPIError(502, "Discord did not return the bot user ID.")
        member = await self._request("GET", f"/guilds/{guild_id}/members/{bot_user_id}")
        roles = await self._request("GET", f"/guilds/{guild_id}/roles")
        if not guild or not member or not isinstance(roles, list):
            raise DiscordAPIError(502, "Discord did not return guild permissions.")

        permissions = _base_permissions(guild_id, member, roles)
        permissions = _channel_permissions(
            permissions,
            guild_id,
            bot_user_id,
            {str(role_id) for role_id in member.get("roles", [])},
            channel.get("permission_overwrites") or [],
        )
        missing = REQUIRED_OUTPUT_PERMISSIONS & ~permissions
        if missing:
            raise DiscordAPIError(
                403,
                "I need View Channel, Send Messages, Embed Links, and "
                "Read Message History permissions in the selected channel.",
            )
        return str(guild.get("name") or guild_id)

    async def get_guild_member(self, guild_id: str, user_id: str) -> dict[str, Any]:
        result = await self._request("GET", f"/guilds/{guild_id}/members/{user_id}")
        if not isinstance(result, dict):
            raise DiscordAPIError(502, "Discord did not return the guild member.")
        return result

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


_COLORS = {
    ProposalStatus.ACTIVE: 0x5865F2,
    ProposalStatus.PASSED: 0x57F287,
    ProposalStatus.VETOED: 0xED4245,
    ProposalStatus.DELETED: 0x747F8D,
}


def _proposal_embed(proposal: Proposal) -> dict[str, Any]:
    if proposal.status is ProposalStatus.ACTIVE:
        status = "Active — passes unless vetoed"
    else:
        status = {
            ProposalStatus.PASSED: "Passed",
            ProposalStatus.VETOED: "Vetoed",
            ProposalStatus.DELETED: "Deleted by a moderator",
        }[proposal.status]
    count = proposal.acknowledgement_count
    fields: list[dict[str, Any]] = [
        {"name": "Status", "value": status, "inline": True},
        {
            "name": "Deadline",
            "value": (
                f"<t:{int(proposal.deadline_at.timestamp())}:F>\n"
                f"<t:{int(proposal.deadline_at.timestamp())}:R>"
            ),
            "inline": True,
        },
        {
            "name": "Acknowledged",
            "value": f"{count} member{'s' if count != 1 else ''}",
            "inline": True,
        },
    ]
    if proposal.status is ProposalStatus.VETOED and proposal.veto_reason:
        fields.append(
            {
                "name": "Anonymous veto reason",
                "value": proposal.veto_reason,
                "inline": False,
            }
        )
    return {
        "author": {"name": f"{proposal.template_name} proposal"[:256]},
        "title": proposal.title,
        **({"description": proposal.context} if proposal.context else {}),
        "color": _COLORS[proposal.status],
        "fields": fields,
        "footer": {
            "text": (
                "Consent-based: acknowledgement is not support"
                if proposal.status is ProposalStatus.ACTIVE
                else "Proposal resolved"
            )
        },
        "timestamp": proposal.created_at.isoformat(),
    }


def _outcome_embed(proposal: Proposal) -> dict[str, Any]:
    title, description = {
        ProposalStatus.PASSED: ("Proposal passed", proposal.title),
        ProposalStatus.VETOED: ("Proposal vetoed", proposal.title),
        ProposalStatus.DELETED: (
            "Proposal deleted by a moderator",
            proposal.title,
        ),
    }[proposal.status]
    if proposal.status is ProposalStatus.VETOED and proposal.veto_reason:
        description += f"\n\n**Anonymous reason**\n{proposal.veto_reason}"
    return {
        "title": title,
        "description": description,
        "color": _COLORS[proposal.status],
        "timestamp": (proposal.terminal_at or proposal.created_at).isoformat(),
    }


def _base_permissions(
    guild_id: str, member: dict[str, Any], roles: list[dict[str, Any]]
) -> int:
    member_roles = {str(role_id) for role_id in member.get("roles", [])}
    permissions = 0
    for role in roles:
        role_id = str(role.get("id", ""))
        if role_id == guild_id or role_id in member_roles:
            permissions |= int(role.get("permissions", 0))
    if permissions & ADMINISTRATOR:
        return (1 << 53) - 1
    return permissions


def _channel_permissions(
    permissions: int,
    guild_id: str,
    user_id: str,
    member_roles: set[str],
    overwrites: list[dict[str, Any]],
) -> int:
    everyone = next(
        (
            overwrite
            for overwrite in overwrites
            if str(overwrite.get("id", "")) == guild_id
        ),
        None,
    )
    if everyone:
        permissions = _apply_overwrite(permissions, everyone)

    role_allow = 0
    role_deny = 0
    for overwrite in overwrites:
        if (
            int(overwrite.get("type", -1)) == 0
            and str(overwrite.get("id", "")) in member_roles
        ):
            role_allow |= int(overwrite.get("allow", 0))
            role_deny |= int(overwrite.get("deny", 0))
    permissions = (permissions & ~role_deny) | role_allow

    member = next(
        (
            overwrite
            for overwrite in overwrites
            if int(overwrite.get("type", -1)) == 1
            and str(overwrite.get("id", "")) == user_id
        ),
        None,
    )
    if member:
        permissions = _apply_overwrite(permissions, member)
    return permissions


def _apply_overwrite(permissions: int, overwrite: dict[str, Any]) -> int:
    deny = int(overwrite.get("deny", 0))
    allow = int(overwrite.get("allow", 0))
    return (permissions & ~deny) | allow
