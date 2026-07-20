"""Destructively remove the disposable single-guild test state."""

import argparse
import asyncio
from typing import Any

import httpx
from google.api_core.exceptions import NotFound
from google.cloud import firestore, tasks_v2

from viteoh.config import get_settings


async def _delete_collection(collection: Any) -> int:
    deleted = 0
    async for snapshot in collection.stream():
        await snapshot.reference.delete()
        deleted += 1
    return deleted


async def cleanup(guild_id: str, channel_id: str) -> dict[str, int]:
    settings = get_settings()
    database = firestore.AsyncClient(
        project=settings.google_cloud_project,
        database=settings.firestore_database,
    )
    tasks = tasks_v2.CloudTasksAsyncClient()
    totals = {"proposals": 0, "children": 0, "tasks": 0, "messages": 0}
    headers = {"Authorization": f"Bot {settings.discord_bot_token}"}

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as discord:
        async for snapshot in database.collection("proposals").stream():
            data = snapshot.to_dict() or {}
            for child_name in ("subscribers", "notifications"):
                totals["children"] += await _delete_collection(
                    snapshot.reference.collection(child_name)
                )
            task_name = data.get("deadline_task_name")
            if task_name:
                try:
                    await tasks.delete_task(name=str(task_name))
                    totals["tasks"] += 1
                except NotFound:
                    pass
            message_id = data.get("message_id")
            if message_id:
                response = await discord.delete(
                    f"{settings.discord_api_base_url}/channels/{channel_id}"
                    f"/messages/{message_id}"
                )
                if response.status_code not in (204, 404):
                    response.raise_for_status()
                totals["messages"] += response.status_code == 204
            await snapshot.reference.delete()
            totals["proposals"] += 1

        for name in ("active_names", "interactions", "users", "guilds"):
            totals[name] = await _delete_collection(database.collection(name))

        response = await discord.put(
            (
                f"{settings.discord_api_base_url}/applications/"
                f"{settings.discord_application_id}/guilds/{guild_id}/commands"
            ),
            json=[],
        )
        response.raise_for_status()

    return totals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--guild-id", required=True)
    parser.add_argument("--channel-id", required=True)
    args = parser.parse_args()
    expected = f"DELETE-LEGACY-{args.guild_id}"
    if args.confirm != expected:
        parser.error(f"--confirm must exactly equal {expected}")
    result = asyncio.run(cleanup(args.guild_id, args.channel_id))
    print("Legacy cleanup complete:", result)


if __name__ == "__main__":
    main()
