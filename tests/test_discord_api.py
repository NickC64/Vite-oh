import json

import httpx

from viteoh.config import Settings
from viteoh.discord_api import DiscordAPIError, DiscordClient
from viteoh.domain import Proposal, ProposalStatus, utcnow


def proposal(status: ProposalStatus = ProposalStatus.ACTIVE) -> Proposal:
    now = utcnow()
    return Proposal(
        id="12345678-1234-1234-1234-123456789abc",
        display_name="Alice",
        normalized_name="alice",
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
        discord_output_channel_id="channel",
        discord_bot_token="token",
    )
    client = DiscordClient(settings, http)
    message_id = await client.create_proposal_announcement(proposal())
    assert message_id == "message"
    body = json.loads(requests[0].content)
    assert body["enforce_nonce"] is True
    assert body["components"]

    await client.edit_interaction_response("interaction-token", "Done")
    await client.sync_terminal_announcement(proposal(ProposalStatus.PASSED))
    await client.send_dm("user", "Hello", event_key="event")
    assert any(
        request.url.path.endswith("/channels/dm/messages") for request in requests
    )
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
