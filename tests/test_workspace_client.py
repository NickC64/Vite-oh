import httpx
import pytest

from viteoh.config import Settings
from viteoh.workspace_client import WorkspaceWorkerClient, WorkspaceWorkerError


async def test_workspace_worker_client_exchanges_and_authorizes() -> None:
    access_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal access_requests
        if request.url.path.endswith("/exchange"):
            return httpx.Response(200, json={"user_id": "user", "guild_id": "guild"})
        access_requests += 1
        return httpx.Response(200, json={"guild_id": "guild", "is_member": True})

    client = WorkspaceWorkerClient(
        Settings(worker_url="https://worker.example"),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert (await client.exchange_launch("code"))["user_id"] == "user"
    assert (await client.get_access("guild", "user"))["is_member"]
    assert (await client.get_access("guild", "user"))["is_member"]
    assert access_requests == 1
    await client.close()


async def test_workspace_worker_client_returns_safe_worker_errors() -> None:
    client = WorkspaceWorkerClient(
        Settings(worker_url="https://worker.example"),
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(403, json={"detail": "Not a member."})
            )
        ),
    )
    with pytest.raises(WorkspaceWorkerError, match="Not a member"):
        await client.exchange_launch("code")
    await client.close()
