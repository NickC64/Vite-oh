import asyncio
import time
from typing import Any

import httpx
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from viteoh.config import Settings


class WorkspaceWorkerError(RuntimeError):
    pass


class WorkspaceWorkerClient:
    def __init__(
        self, settings: Settings, client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=10.0)
        self._access_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._guild_cache: dict[
            tuple[str, tuple[str, ...]], tuple[float, list[dict[str, Any]]]
        ] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def exchange_launch(self, code: str) -> dict[str, Any]:
        return await self._post("/internal/workspace/exchange", {"code": code})

    async def get_access(self, guild_id: str, user_id: str) -> dict[str, Any]:
        key = (guild_id, user_id)
        cached = self._access_cache.get(key)
        now = time.monotonic()
        if cached and cached[0] > now:
            return dict(cached[1])
        access = await self._post(
            "/internal/workspace/access",
            {"guild_id": guild_id, "user_id": user_id},
        )
        self._access_cache[key] = (
            now + self.settings.workspace_auth_cache_seconds,
            dict(access),
        )
        return access

    async def list_guild_summaries(
        self, guild_ids: tuple[str, ...], user_id: str
    ) -> list[dict[str, Any]]:
        key = (user_id, guild_ids)
        cached = self._guild_cache.get(key)
        now = time.monotonic()
        if cached and cached[0] > now:
            return [dict(item) for item in cached[1]]
        result = await self._post(
            "/internal/workspace/guilds",
            {"guild_ids": list(guild_ids), "user_id": user_id},
        )
        raw = result.get("guilds")
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise WorkspaceWorkerError("The worker returned invalid server data.")
        summaries = [dict(item) for item in raw]
        self._guild_cache[key] = (
            now + self.settings.workspace_auth_cache_seconds,
            summaries,
        )
        return [dict(item) for item in summaries]

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if self.settings.google_cloud_project:
            token = await asyncio.to_thread(
                id_token.fetch_id_token,
                GoogleRequest(),
                self.settings.worker_url,
            )
            headers["Authorization"] = f"Bearer {token}"
        response = await self.client.post(
            f"{self.settings.worker_url}{path}",
            json=payload,
            headers=headers,
        )
        if response.status_code >= 400:
            message = "The workspace could not verify your Discord access."
            try:
                message = str(response.json().get("detail") or message)
            except ValueError:
                pass
            raise WorkspaceWorkerError(message)
        result = response.json()
        if not isinstance(result, dict):
            raise WorkspaceWorkerError("The worker returned an invalid response.")
        return result
