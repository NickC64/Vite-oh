import asyncio
from typing import Any

import httpx

from viteoh.commands import COMMANDS
from viteoh.config import Settings, get_settings

PROFILE_DESCRIPTION = (
    "Consent-based proposals that pass unless a server member anonymously vetoes. "
    "Create and manage proposals in a private workspace.\n\n"
    "Open workspace: {command_mention}"
)


def profile_description(command_id: str) -> str:
    if not command_id.isdigit():
        raise RuntimeError("Discord did not return a valid /proposal command ID.")
    return PROFILE_DESCRIPTION.format(command_mention=f"</proposal:{command_id}>")


async def register_commands(
    client: httpx.AsyncClient, settings: Settings
) -> tuple[int, str]:
    headers = {"Authorization": f"Bot {settings.discord_bot_token}"}
    response = await client.put(
        (
            f"{settings.discord_api_base_url}/applications/"
            f"{settings.discord_application_id}/commands"
        ),
        headers=headers,
        json=COMMANDS,
    )
    response.raise_for_status()
    registered: Any = response.json()
    if not isinstance(registered, list):
        raise RuntimeError("Discord returned an invalid command-registration response.")
    proposal = next(
        (
            command
            for command in registered
            if isinstance(command, dict)
            and command.get("name") == "proposal"
            and command.get("type") == 1
        ),
        None,
    )
    if not proposal:
        raise RuntimeError("Discord did not return the registered /proposal command.")

    description = profile_description(str(proposal.get("id", "")))
    profile_response = await client.patch(
        f"{settings.discord_api_base_url}/applications/@me",
        headers=headers,
        json={"description": description},
    )
    profile_response.raise_for_status()
    return len(registered), description


async def main() -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        count, _ = await register_commands(client, settings)
        print(f"Registered {count} global commands and synchronized the bot profile.")


if __name__ == "__main__":
    asyncio.run(main())
