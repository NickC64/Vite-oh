import json
from typing import Any

from fastapi.testclient import TestClient

from viteoh.app import create_app
from viteoh.config import Settings


class StubReceiver:
    async def receive(
        self, body: bytes, signature: str | None, timestamp: str | None
    ) -> tuple[int, dict[str, Any]]:
        return 200, {"type": 1, "body": json.loads(body)}


class StubProcessor:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def process(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)

    async def finalize(self, proposal_id: str) -> str:
        return f"finalized:{proposal_id}"

    async def reconcile(self) -> dict[str, int]:
        return {
            "active": 0,
            "finalized": 0,
            "repaired": 0,
            "effects_retried": 0,
        }

    async def process_workspace(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)

    async def exchange_workspace_launch(self, code: str) -> dict[str, str] | None:
        if code == "good":
            return {"user_id": "user", "guild_id": "guild"}
        return None

    async def workspace_access(self, guild_id: str, user_id: str) -> dict[str, object]:
        return {
            "guild_id": guild_id,
            "user_id": user_id,
            "is_member": True,
        }

    async def workspace_guild_summaries(
        self, guild_ids: list[str], user_id: str
    ) -> dict[str, object]:
        return {
            "guilds": [
                {
                    "guild_id": guild_id,
                    "guild_name": f"Server {guild_id}",
                    "user_id": user_id,
                }
                for guild_id in guild_ids
            ]
        }

    async def workspace_member_search(
        self, guild_id: str, user_id: str, query: str
    ) -> dict[str, object]:
        return {
            "members": [
                {
                    "user_id": "target",
                    "display_name": f"{query} in {guild_id} for {user_id}",
                }
            ]
        }


def test_receiver_app_routes() -> None:
    app = create_app(
        Settings(service_role="receiver"),
        receiver=StubReceiver(),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        assert client.get("/health").json()["role"] == "receiver"
        response = client.post("/interactions", json={"type": 1})
        assert response.status_code == 200
        assert response.json()["type"] == 1
        assert client.post("/tasks/reconcile").status_code == 404


def test_worker_app_routes() -> None:
    processor = StubProcessor()
    app = create_app(
        Settings(service_role="worker"),
        processor=processor,  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        assert client.get("/health").json()["role"] == "worker"
        assert client.post("/tasks/interactions", json={"id": "1"}).status_code == 204
        assert processor.payloads == [{"id": "1"}]
        assert client.post("/tasks/proposals/p1/finalize").json() == {
            "result": "finalized:p1"
        }
        assert client.post("/tasks/reconcile").json()["active"] == 0
        assert client.post("/tasks/workspace", json={"id": "job"}).status_code == 204
        assert (
            client.post("/internal/workspace/exchange", json={"code": "good"}).json()[
                "user_id"
            ]
            == "user"
        )
        assert (
            client.post(
                "/internal/workspace/exchange", json={"code": "bad"}
            ).status_code
            == 401
        )
        assert client.post(
            "/internal/workspace/access",
            json={"guild_id": "guild", "user_id": "user"},
        ).json()["is_member"]
        assert (
            client.post(
                "/internal/workspace/guilds",
                json={"guild_ids": ["guild"], "user_id": "user"},
            ).json()["guilds"][0]["guild_id"]
            == "guild"
        )
        assert (
            client.post(
                "/internal/workspace/members",
                json={"guild_id": "guild", "user_id": "user", "query": "tar"},
            ).json()["members"][0]["user_id"]
            == "target"
        )
        assert client.post("/interactions").status_code == 404
