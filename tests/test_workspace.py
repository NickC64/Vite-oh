import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient

from tests.fakes import FakeRepository, FakeTasks
from viteoh.app import create_app
from viteoh.config import Settings
from viteoh.domain import GuildConfig, utcnow
from viteoh.workspace import Workspace
from viteoh.workspace_client import WorkspaceWorkerError
from viteoh.workspace_security import SignedTokenCodec


class FakeWorkspaceWorker:
    def __init__(self) -> None:
        self.used = False
        self.admin = True
        self.member = True

    async def close(self) -> None:
        pass

    async def exchange_launch(self, code: str) -> dict[str, Any]:
        if code != "good" or self.used:
            raise WorkspaceWorkerError("This workspace link is invalid or used.")
        self.used = True
        return {
            "user_id": "admin",
            "guild_id": "guild",
            "proposal_id": None,
            "display_name": "Ada",
        }

    async def get_access(self, guild_id: str, user_id: str) -> dict[str, Any]:
        if not self.member:
            raise WorkspaceWorkerError("You are no longer a member.")
        return {
            "guild_id": guild_id,
            "guild_name": "Test Guild",
            "is_member": True,
            "can_manage": self.admin,
            "visible_channel_ids": ["channel"],
            "output_channels": [
                {"id": "channel", "name": "proposals", "bot_ready": True}
            ],
        }


def web_system(
    *, secure_cookies: bool = False
) -> tuple[TestClient, FakeRepository, FakeTasks, FakeWorkspaceWorker]:
    repository = FakeRepository()
    tasks = FakeTasks()
    worker = FakeWorkspaceWorker()
    now = utcnow()
    repository.guilds["guild"] = GuildConfig(
        guild_id="guild",
        guild_name="Test Guild",
        output_channel_id="channel",
        proposal_timeout_seconds=60,
        configured_by="admin",
        created_at=now,
        updated_at=now,
    )
    settings = Settings(
        service_role="web",
        workspace_signing_secret="secret",
        secure_cookies=secure_cookies,
        discord_owner_user_id="owner",
    )
    workspace = Workspace(
        settings,
        repository,  # type: ignore[arg-type]
        tasks,  # type: ignore[arg-type]
        worker,  # type: ignore[arg-type]
    )
    return (
        TestClient(create_app(settings, workspace=workspace)),
        repository,
        tasks,
        worker,
    )


def login(client: TestClient) -> None:
    response = client.get("/launch?code=good", follow_redirects=False)
    assert response.status_code == 303
    assert "viteoh_session" in response.cookies


def csrf(client: TestClient) -> str:
    token = client.cookies["viteoh_session"]
    return str(SignedTokenCodec("secret").decode(token, "session")["csrf"])


def test_launch_dashboard_create_and_detail_flow() -> None:
    client, repository, tasks, _ = web_system()
    with client:
        assert client.get("/").status_code == 200
        assert client.get("/launch?code=bad").status_code == 401
        login(client)
        dashboard = client.get("/app?guild=guild")
        assert dashboard.status_code == 200
        assert "Create proposal" in dashboard.text
        form = client.get("/app/proposals/new?guild=guild")
        assert "Discord preview" in form.text
        response = client.post(
            "/app/proposals",
            data={
                "csrf": csrf(client),
                "guild_id": "guild",
                "title": "Quiet hours",
                "context": "No pings after ten.",
                "proposal_type": "builtin:general",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert tasks.workspace[-1]["action"] == "create"

        now = utcnow()
        result = asyncio.run(
            repository.create_proposal(
                "manual",
                "guild",
                "Test Guild",
                "channel",
                "Quiet hours",
                "quiet hours",
                "No pings after ten.",
                "builtin:general",
                "General",
                now,
                now + timedelta(minutes=1),
            )
        )
        assert result.proposal
        repository.proposals[result.proposal.id] = replace(
            result.proposal, message_id="999"
        )
        detail = client.get(f"/app/proposals/{result.proposal.id}?guild=guild")
        assert detail.status_code == 200
        assert "Quiet hours" in detail.text
        delete = client.post(
            f"/app/proposals/{result.proposal.id}/delete",
            data={"csrf": csrf(client), "guild_id": "guild"},
            follow_redirects=False,
        )
        assert delete.status_code == 303
        assert tasks.workspace[-1]["action"] == "delete"


def test_production_session_cookie_is_secure_http_only_and_same_site() -> None:
    client, _, _, _ = web_system(secure_cookies=True)
    with client:
        response = client.get("/launch?code=good", follow_redirects=False)
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_preferences_settings_types_jobs_and_security_headers() -> None:
    client, repository, tasks, worker = web_system()
    with client:
        login(client)
        preferences = client.get("/app/preferences?guild=guild")
        assert "Anonymous nudges" in preferences.text
        client.post(
            "/app/preferences",
            data={
                "csrf": csrf(client),
                "guild_id": "guild",
                "new_proposals": "on",
            },
        )
        assert tasks.workspace[-1]["action"] == "preferences"

        settings = client.get("/app/settings?guild=guild")
        assert "#proposals" in settings.text
        client.post(
            "/app/settings",
            data={
                "csrf": csrf(client),
                "guild_id": "guild",
                "channel_id": "channel",
                "duration_minutes": "5",
            },
        )
        assert tasks.workspace[-1]["action"] == "configure"

        types = client.get("/app/types?guild=guild")
        assert "New member" in types.text
        client.post(
            "/app/types",
            data={
                "csrf": csrf(client),
                "guild_id": "guild",
                "name": "Policy",
                "description": "Change a policy",
            },
        )
        assert tasks.workspace[-1]["action"] == "type_save"

        now = utcnow()
        asyncio.run(
            repository.set_workspace_job(
                "job",
                "guild",
                SignedTokenCodec("secret").fingerprint("admin"),
                "preferences",
                "succeeded",
                "Saved.",
                None,
                now,
                3600,
            )
        )
        job = client.get("/app/jobs/job?guild=guild")
        assert "Saved." in job.text
        assert job.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in job.headers["content-security-policy"]
        assert job.headers["cache-control"] == "no-store"
        assert "max-age=31536000" in job.headers["strict-transport-security"]

        bad_csrf = client.post(
            "/app/preferences",
            data={"csrf": "bad", "guild_id": "guild"},
        )
        assert bad_csrf.status_code == 403
        worker.admin = False
        assert client.get("/app/settings?guild=guild").status_code == 403


def test_unconfigured_guild_routes_admin_to_setup() -> None:
    client, repository, _, _ = web_system()
    repository.guilds.clear()
    with client:
        login(client)
        dashboard = client.get("/app?guild=guild")
        assert "not configured" in dashboard.text
        settings = client.get("/app/settings?guild=guild")
        assert settings.status_code == 200
