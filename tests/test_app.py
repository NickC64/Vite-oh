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


def test_receiver_app_routes() -> None:
    app = create_app(
        Settings(service_role="receiver"),
        receiver=StubReceiver(),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        assert client.get("/healthz").json()["role"] == "receiver"
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
        assert client.get("/healthz").json()["role"] == "worker"
        assert client.post("/tasks/interactions", json={"id": "1"}).status_code == 204
        assert processor.payloads == [{"id": "1"}]
        assert client.post("/tasks/proposals/p1/finalize").json() == {
            "result": "finalized:p1"
        }
        assert client.post("/tasks/reconcile").json()["active"] == 0
        assert client.post("/interactions").status_code == 404
