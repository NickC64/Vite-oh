import json

import httpx
import pytest

from viteoh.config import Settings
from viteoh.register_commands import profile_description, register_commands


def test_profile_description_contains_clickable_command_mention() -> None:
    description = profile_description("123456789012345678")
    assert "</proposal:123456789012345678>" in description
    assert len(description) <= 400
    with pytest.raises(RuntimeError, match="valid /proposal command ID"):
        profile_description("not-a-snowflake")


async def test_registration_synchronizes_the_application_profile() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PUT":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "123456789012345678",
                        "name": "proposal",
                        "type": 1,
                    }
                ],
            )
        return httpx.Response(200, json={"description": "updated"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        count, description = await register_commands(
            client,
            Settings(
                discord_api_base_url="https://discord.test/api/v10",
                discord_application_id="application",
                discord_bot_token="token",
            ),
        )

    assert count == 1
    assert [request.method for request in requests] == ["PUT", "PATCH"]
    assert requests[1].url.path == "/api/v10/applications/@me"
    assert json.loads(requests[1].content) == {"description": description}
    assert requests[1].headers["authorization"] == "Bot token"


async def test_registration_rejects_a_missing_proposal_command() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json=[{"id": "123456789012345678", "name": "other", "type": 1}]
            )
        )
    ) as client:
        with pytest.raises(RuntimeError, match="registered /proposal"):
            await register_commands(
                client,
                Settings(
                    discord_application_id="application",
                    discord_bot_token="token",
                ),
            )
