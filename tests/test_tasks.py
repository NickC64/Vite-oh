from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from google.api_core.exceptions import AlreadyExists, NotFound

from viteoh.config import Settings
from viteoh.domain import utcnow
from viteoh.tasks import TaskDispatcher


class FakeTaskClient:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.raise_exists = False
        self.raise_not_found = False

    def queue_path(self, project: str, location: str, queue: str) -> str:
        return f"projects/{project}/locations/{location}/queues/{queue}"

    def task_path(self, project: str, location: str, queue: str, task: str) -> str:
        return f"{self.queue_path(project, location, queue)}/tasks/{task}"

    async def create_task(self, request: dict[str, Any]) -> Any:
        self.created.append(request)
        if self.raise_exists:
            raise AlreadyExists("exists")
        return SimpleNamespace(name=request["task"]["name"])

    async def get_task(self, request: dict[str, Any]) -> Any:
        if self.raise_not_found:
            raise NotFound("missing")
        return SimpleNamespace(name=request["name"])

    async def delete_task(self, request: dict[str, Any]) -> None:
        if self.raise_not_found:
            raise NotFound("missing")


async def test_task_dispatch_and_deadline_deduplicate() -> None:
    fake = FakeTaskClient()
    dispatcher = TaskDispatcher(
        Settings(
            google_cloud_project="project",
            worker_url="https://worker",
            task_invoker_service_account="tasks@example.com",
        ),
        fake,  # type: ignore[arg-type]
    )
    interaction_name = await dispatcher.enqueue_interaction({"id": "42"})
    assert interaction_name.endswith("interaction-42")
    workspace_name = await dispatcher.enqueue_workspace(
        {"id": "job", "action": "create"}
    )
    assert workspace_name.endswith("workspace-job")
    deadline_name = await dispatcher.ensure_deadline(
        "12345678-1234-1234-1234-123456789abc",
        utcnow() + timedelta(hours=1),
    )
    assert deadline_name.endswith("12345678-1234-1234-1234-123456789abc")
    assert await dispatcher.exists(deadline_name)

    fake.raise_exists = True
    assert await dispatcher.enqueue_interaction({"id": "42"}) == interaction_name
    assert (
        await dispatcher.enqueue_workspace({"id": "job", "action": "create"})
        == workspace_name
    )
    fake.raise_not_found = True
    assert not await dispatcher.exists(deadline_name)
    await dispatcher.delete(deadline_name)
    await dispatcher.delete(None)
