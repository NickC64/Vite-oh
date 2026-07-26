import json
from datetime import datetime
from typing import Any

from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2

from viteoh.config import Settings


class TaskDispatcher:
    def __init__(
        self,
        settings: Settings,
        client: tasks_v2.CloudTasksAsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client

    @property
    def client(self) -> tasks_v2.CloudTasksAsyncClient:
        if self._client is None:
            self._client = tasks_v2.CloudTasksAsyncClient()
        return self._client

    def _queue_path(self, queue: str) -> str:
        return self.client.queue_path(
            self.settings.google_cloud_project,
            self.settings.google_cloud_location,
            queue,
        )

    def _oidc(self) -> dict[str, str]:
        return {
            "service_account_email": self.settings.task_invoker_service_account,
            "audience": self.settings.worker_url,
        }

    async def enqueue_interaction(self, payload: dict[str, Any]) -> str:
        interaction_id = str(payload["id"])
        parent = self._queue_path(self.settings.interaction_queue)
        name = self.client.task_path(
            self.settings.google_cloud_project,
            self.settings.google_cloud_location,
            self.settings.interaction_queue,
            f"interaction-{interaction_id}",
        )
        task = {
            "name": name,
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{self.settings.worker_url}/tasks/interactions",
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(payload).encode(),
                "oidc_token": self._oidc(),
            },
        }
        try:
            created = await self.client.create_task(
                request={"parent": parent, "task": task}
            )
            return created.name
        except AlreadyExists:
            return name

    async def enqueue_workspace(self, payload: dict[str, Any]) -> str:
        job_id = str(payload["id"])
        parent = self._queue_path(self.settings.workspace_queue)
        name = self.client.task_path(
            self.settings.google_cloud_project,
            self.settings.google_cloud_location,
            self.settings.workspace_queue,
            f"workspace-{job_id}",
        )
        task = {
            "name": name,
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{self.settings.worker_url}/tasks/workspace",
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(payload).encode(),
                "oidc_token": self._oidc(),
            },
        }
        try:
            created = await self.client.create_task(
                request={"parent": parent, "task": task}
            )
            return created.name
        except AlreadyExists:
            return name

    async def ensure_deadline(
        self,
        proposal_id: str,
        deadline_at: datetime,
        *,
        repair_suffix: str | None = None,
    ) -> str:
        parent = self._queue_path(self.settings.deadline_queue)
        suffix = f"-repair-{repair_suffix}" if repair_suffix else ""
        task_id = f"finalize-{proposal_id}{suffix}"
        name = self.client.task_path(
            self.settings.google_cloud_project,
            self.settings.google_cloud_location,
            self.settings.deadline_queue,
            task_id,
        )
        timestamp = timestamp_pb2.Timestamp()
        timestamp.FromDatetime(deadline_at)
        task = {
            "name": name,
            "schedule_time": timestamp,
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": (
                    f"{self.settings.worker_url}/tasks/proposals/{proposal_id}/finalize"
                ),
                "headers": {"Content-Type": "application/json"},
                "body": b"{}",
                "oidc_token": self._oidc(),
            },
        }
        try:
            created = await self.client.create_task(
                request={"parent": parent, "task": task}
            )
            return created.name
        except AlreadyExists:
            return name

    async def exists(self, task_name: str) -> bool:
        try:
            await self.client.get_task(request={"name": task_name})
            return True
        except NotFound:
            return False

    async def delete(self, task_name: str | None) -> None:
        if not task_name:
            return
        try:
            await self.client.delete_task(request={"name": task_name})
        except NotFound:
            pass
