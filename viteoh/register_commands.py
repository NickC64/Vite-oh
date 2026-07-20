import asyncio

import httpx

from viteoh.commands import COMMANDS
from viteoh.config import get_settings


async def main() -> None:
    settings = get_settings()
    url = (
        f"{settings.discord_api_base_url}/applications/"
        f"{settings.discord_application_id}/guilds/{settings.discord_guild_id}/commands"
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.put(
            url,
            headers={"Authorization": f"Bot {settings.discord_bot_token}"},
            json=COMMANDS,
        )
        response.raise_for_status()
        print(f"Registered {len(response.json())} guild commands.")


if __name__ == "__main__":
    asyncio.run(main())
