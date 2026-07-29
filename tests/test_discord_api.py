import json
from dataclasses import replace

import httpx
import pytest

from viteoh.config import Settings
from viteoh.discord_api import (
    EMBED_LINKS,
    MANAGE_GUILD,
    READ_MESSAGE_HISTORY,
    SEND_MESSAGES,
    VIEW_CHANNEL,
    DiscordAPIError,
    DiscordClient,
)
from viteoh.domain import Proposal, ProposalStatus, utcnow


def proposal(status: ProposalStatus = ProposalStatus.ACTIVE) -> Proposal:
    now = utcnow()
    return Proposal(
        id="12345678-1234-1234-1234-123456789abc",
        guild_id="guild",
        guild_name="Test Guild",
        output_channel_id="channel",
        title="Alice",
        normalized_title="alice",
        context="Supporting context",
        reservation_id="reserve",
        status=status,
        created_at=now,
        deadline_at=now,
        message_id="message",
    )


async def test_discord_announcement_edit_response_and_dm() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v10/users/@me/channels":
            return httpx.Response(200, json={"id": "dm"})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "message"})
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        discord_api_base_url="https://discord.test/api/v10",
        discord_application_id="app",
        discord_bot_token="token",
    )
    client = DiscordClient(settings, http)
    message_id = await client.create_proposal_announcement(proposal())
    assert message_id == "message"
    body = json.loads(requests[0].content)
    assert body["enforce_nonce"] is True
    assert body["components"]
    assert body["embeds"][0]["color"] == 0x5865F2
    assert body["embeds"][0]["title"] == "Alice"
    assert body["embeds"][0]["author"]["name"] == "Proposal"
    created_nonce = body["nonce"]
    assert (
        await client.sync_proposal_announcement(replace(proposal(), message_id=None))
        == "message"
    )
    raced_sync_body = json.loads(requests[-1].content)
    assert raced_sync_body["nonce"] == created_nonce
    await client.create_proposal_announcement(
        replace(
            proposal(),
            type_id="builtin:new-member",
            type_name="New member",
        )
    )
    typed_body = json.loads(requests[-1].content)
    assert typed_body["embeds"][0]["author"]["name"] == "Type · New member"

    await client.edit_interaction_response("interaction-token", "Done")
    terminal = proposal(ProposalStatus.PASSED)
    assert await client.sync_proposal_announcement(terminal) == "message"
    assert await client.create_outcome_reply(terminal) == "message"
    await client.delete_proposal_history_messages(
        replace(terminal, outcome_message_id="outcome")
    )
    deleted_paths = [
        request.url.path for request in requests if request.method == "DELETE"
    ]
    assert deleted_paths[-2:] == [
        "/api/v10/channels/channel/messages/outcome",
        "/api/v10/channels/channel/messages/message",
    ]
    await client.send_dm("user", "Hello", event_key="event")
    assert any(
        request.url.path.endswith("/channels/dm/messages") for request in requests
    )
    await client.close()


async def test_veto_embed_includes_public_reason_without_mentions() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "message"})

    client = DiscordClient(
        Settings(discord_api_base_url="https://discord.test"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    vetoed = proposal(ProposalStatus.VETOED)
    vetoed = replace(
        vetoed,
        veto_reason="Please document the rollback plan, @everyone.",
    )
    await client.create_outcome_reply(vetoed)
    body = json.loads(requests[-1].content)
    assert "rollback" in body["embeds"][0]["description"]
    assert body["allowed_mentions"] == {"parse": [], "replied_user": False}
    await client.close()


async def test_discord_error_classifies_retryability() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="closed")

    client = DiscordClient(
        Settings(discord_api_base_url="https://discord.test"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        await client.send_dm("user", "Hello", event_key="event")
    except DiscordAPIError as exc:
        assert not exc.retryable
        assert exc.status_code == 403
    else:
        raise AssertionError("Expected DiscordAPIError")
    await client.close()


async def test_search_guild_members_uses_bounded_prefix_query() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[{"user": {"id": "target", "username": "target"}}],
        )

    client = DiscordClient(
        Settings(discord_api_base_url="https://discord.test/api/v10"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await client.search_guild_members("guild", "tar get", limit=99)
    assert result[0]["user"]["id"] == "target"
    assert requests[0].url.path.endswith("/guilds/guild/members/search")
    assert requests[0].url.params["query"] == "tar get"
    assert requests[0].url.params["limit"] == "8"
    await client.close()


async def test_validate_output_channel_checks_guild_type_and_permissions() -> None:
    required = VIEW_CHANNEL | SEND_MESSAGES | EMBED_LINKS | READ_MESSAGE_HISTORY

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/channels/channel"):
            return httpx.Response(
                200,
                json={
                    "id": "channel",
                    "guild_id": "guild",
                    "type": 0,
                    "permission_overwrites": [],
                },
            )
        if path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": "bot"})
        if path.endswith("/guilds/guild/members/bot"):
            return httpx.Response(
                200, json={"user": {"id": "bot"}, "roles": ["bot-role"]}
            )
        if path.endswith("/guilds/guild/roles"):
            return httpx.Response(
                200,
                json=[
                    {"id": "guild", "permissions": "0"},
                    {"id": "bot-role", "permissions": str(required)},
                ],
            )
        if path.endswith("/guilds/guild"):
            return httpx.Response(200, json={"id": "guild", "name": "Test Guild"})
        raise AssertionError(path)

    client = DiscordClient(
        Settings(
            discord_api_base_url="https://discord.test/api/v10",
            discord_bot_token="token",
        ),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await client.validate_output_channel("guild", "channel") == "Test Guild"
    await client.close()


async def test_validate_output_channel_rejects_cross_guild_channel() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "channel", "guild_id": "other", "type": 0}
        )

    client = DiscordClient(
        Settings(discord_api_base_url="https://discord.test"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(DiscordAPIError, match="not in this server"):
        await client.validate_output_channel("guild", "channel")
    await client.close()


async def test_validate_output_channel_requires_embed_links() -> None:
    permissions = VIEW_CHANNEL | SEND_MESSAGES | READ_MESSAGE_HISTORY

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/channels/channel"):
            return httpx.Response(
                200,
                json={
                    "guild_id": "guild",
                    "type": 0,
                    "permission_overwrites": [],
                },
            )
        if path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": "bot"})
        if path.endswith("/guilds/guild/members/bot"):
            return httpx.Response(200, json={"roles": ["bot-role"]})
        if path.endswith("/guilds/guild/roles"):
            return httpx.Response(
                200,
                json=[
                    {"id": "guild", "permissions": "0"},
                    {"id": "bot-role", "permissions": str(permissions)},
                ],
            )
        if path.endswith("/guilds/guild"):
            return httpx.Response(200, json={"name": "Test Guild"})
        raise AssertionError(path)

    client = DiscordClient(
        Settings(discord_api_base_url="https://discord.test"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(DiscordAPIError, match="Embed Links"):
        await client.validate_output_channel("guild", "channel")
    await client.close()


async def test_workspace_access_resolves_member_and_bot_channel_permissions() -> None:
    required = VIEW_CHANNEL | SEND_MESSAGES | EMBED_LINKS | READ_MESSAGE_HISTORY

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": "bot"})
        if path.endswith("/guilds/guild/members/user"):
            return httpx.Response(
                200,
                json={
                    "nick": "Ada",
                    "roles": ["member-role"],
                    "user": {"id": "user", "username": "ada"},
                },
            )
        if path.endswith("/guilds/guild/members/bot"):
            return httpx.Response(
                200, json={"roles": ["bot-role"], "user": {"id": "bot"}}
            )
        if path.endswith("/guilds/guild/roles"):
            return httpx.Response(
                200,
                json=[
                    {"id": "guild", "permissions": "0"},
                    {
                        "id": "member-role",
                        "permissions": str(VIEW_CHANNEL | MANAGE_GUILD),
                    },
                    {"id": "bot-role", "permissions": str(required)},
                ],
            )
        if path.endswith("/guilds/guild/channels"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "ready",
                        "name": "proposals",
                        "type": 0,
                        "permission_overwrites": [],
                    },
                    {
                        "id": "hidden",
                        "name": "staff",
                        "type": 0,
                        "permission_overwrites": [
                            {
                                "id": "guild",
                                "type": 0,
                                "allow": "0",
                                "deny": str(VIEW_CHANNEL),
                            }
                        ],
                    },
                    {"id": "voice", "name": "voice", "type": 2},
                ],
            )
        if path.endswith("/guilds/guild"):
            return httpx.Response(
                200,
                json={
                    "id": "guild",
                    "name": "Test Guild",
                    "icon": "a_deadbeef",
                    "owner_id": "someone-else",
                },
            )
        raise AssertionError(path)

    client = DiscordClient(
        Settings(discord_api_base_url="https://discord.test/api/v10"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    access = await client.get_workspace_access("guild", "user")
    assert access["display_name"] == "Ada"
    assert access["guild_icon_hash"] == "a_deadbeef"
    assert access["can_manage"]
    assert access["visible_channel_ids"] == ["ready", "voice"]
    assert access["output_channels"] == [
        {"id": "ready", "name": "proposals", "bot_ready": True}
    ]
    await client.close()


def test_administrator_channel_permissions_ignore_overwrite_denials() -> None:
    from viteoh.discord_api import ADMINISTRATOR, _channel_permissions

    permissions = _channel_permissions(
        ADMINISTRATOR,
        "guild",
        "admin",
        set(),
        [{"id": "guild", "type": 0, "allow": "0", "deny": str(VIEW_CHANNEL)}],
    )
    assert permissions & VIEW_CHANNEL
