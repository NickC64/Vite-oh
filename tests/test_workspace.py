import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient

from tests.fakes import FakeRepository, FakeTasks
from viteoh.app import create_app
from viteoh.config import Settings
from viteoh.domain import GuildConfig, ProposalStatus, utcnow
from viteoh.workspace import Workspace, _guild_view
from viteoh.workspace_client import WorkspaceWorkerError
from viteoh.workspace_security import SignedTokenCodec


class FakeWorkspaceWorker:
    def __init__(self) -> None:
        self.used: set[str] = set()
        self.admin = True
        self.member = True

    async def close(self) -> None:
        pass

    async def exchange_launch(self, code: str) -> dict[str, Any]:
        guild_id = {"good": "guild", "second": "guild-2"}.get(code)
        if not guild_id or code in self.used:
            raise WorkspaceWorkerError(
                "This workspace link is invalid or used.", status_code=401
            )
        self.used.add(code)
        return {
            "user_id": "admin",
            "guild_id": guild_id,
            "proposal_id": None,
            "display_name": "Ada",
        }

    async def get_access(self, guild_id: str, user_id: str) -> dict[str, Any]:
        if not self.member:
            raise WorkspaceWorkerError("You are no longer a member.")
        channel_id = "channel" if guild_id == "guild" else "channel-2"
        return {
            "guild_id": guild_id,
            "guild_name": "Test Guild" if guild_id == "guild" else "Second Guild",
            "is_member": True,
            "can_manage": self.admin,
            "visible_channel_ids": [channel_id],
            "output_channels": [
                {"id": channel_id, "name": "proposals", "bot_ready": True}
            ],
        }

    async def list_guild_summaries(
        self, guild_ids: tuple[str, ...], user_id: str
    ) -> list[dict[str, Any]]:
        if not self.member:
            return []
        return [
            {
                "guild_id": guild_id,
                "guild_name": ("Test Guild" if guild_id == "guild" else "Second Guild"),
                "guild_icon_hash": "",
                "configured": True,
                "can_manage": self.admin,
            }
            for guild_id in guild_ids
        ]

    async def search_members(
        self, guild_id: str, user_id: str, query: str
    ) -> list[dict[str, str]]:
        if query == "fail":
            raise WorkspaceWorkerError("Member search is temporarily unavailable.")
        return [
            {"user_id": "target", "display_name": "Target Member"},
            {"user_id": "second", "display_name": "Second Member"},
        ]


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


def test_guild_view_builds_safe_icons_and_deterministic_fallbacks() -> None:
    guild = _guild_view(
        {
            "guild_id": "123456789012345678",
            "guild_name": "The Mailroom",
            "guild_icon_hash": "a_deadbeef",
        }
    )
    assert guild["initials"] == "TM"
    assert guild["guild_icon_url"] == (
        "https://cdn.discordapp.com/icons/123456789012345678/a_deadbeef.webp?size=96"
    )
    assert (
        _guild_view(
            {
                "guild_id": "123456789012345678",
                "guild_name": "Nope",
                "guild_icon_hash": "https://attacker.example/icon",
            }
        )["guild_icon_url"]
        == ""
    )


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
        assert "No type" in form.text
        assert "category tags" in form.text
        response = client.post(
            "/app/proposals",
            data={
                "csrf": csrf(client),
                "guild_id": "guild",
                "title": "Quiet hours",
                "context": "No pings after ten.",
                "proposal_type": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert tasks.workspace[-1]["action"] == "create"
        assert tasks.workspace[-1]["data"]["type_id"] == ""

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
                "",
                "",
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
        assert "Back to proposals" in detail.text
        assert "Member actions" in detail.text
        assert 'data-local-datetime="long"' in detail.text
        delete = client.post(
            f"/app/proposals/{result.proposal.id}/delete",
            data={"csrf": csrf(client), "guild_id": "guild"},
            follow_redirects=False,
        )
        assert delete.status_code == 303
        assert tasks.workspace[-1]["action"] == "delete"


def test_member_actions_enqueue_durable_jobs_and_search_conservatively() -> None:
    client, repository, tasks, _ = web_system()
    now = utcnow()
    result = asyncio.run(
        repository.create_proposal(
            "actions",
            "guild",
            "Test Guild",
            "channel",
            "Action test",
            "action test",
            "",
            "",
            "",
            now,
            now + timedelta(minutes=5),
        )
    )
    assert result.proposal
    proposal_id = result.proposal.id
    with client:
        login(client)
        common = {"csrf": csrf(client), "guild_id": "guild"}
        cases = [
            ("acknowledge", "acknowledge", {}),
            ("subscription", "subscription", {"enabled": "true"}),
            ("nudge", "nudge", {"target_user_id": "target"}),
            ("veto", "veto", {"reason": "Needs a rollback plan."}),
        ]
        for path, expected, extra in cases:
            response = client.post(
                f"/app/proposals/{proposal_id}/{path}",
                data={**common, **extra},
                headers={"HX-Request": "true"},
            )
            assert response.status_code == 200
            assert "Saving" in response.text
            assert tasks.workspace[-1]["action"] == expected
            assert tasks.workspace[-1]["data"]["proposal_id"] == proposal_id

        assert client.get("/app/guilds/guild/members?q=t").json() == {"members": []}
        search = client.get("/app/guilds/guild/members?q=ta")
        assert search.json()["members"][0]["display_name"] == "Target Member"


def test_inline_action_job_returns_feedback_and_updated_controls() -> None:
    client, repository, _, _ = web_system()
    now = utcnow()
    result = asyncio.run(
        repository.create_proposal(
            "feedback",
            "guild",
            "Test Guild",
            "channel",
            "Feedback",
            "feedback",
            "",
            "",
            "",
            now,
            now + timedelta(minutes=5),
        )
    )
    assert result.proposal
    asyncio.run(
        repository.set_workspace_job(
            "job",
            "guild",
            SignedTokenCodec("secret").fingerprint("admin"),
            "subscription",
            "succeeded",
            "Subscribed to proposal updates.",
            result.proposal.id,
            now,
            86400,
        )
    )
    asyncio.run(repository.set_proposal_subscription(result.proposal.id, "admin", True))
    with client:
        login(client)
        response = client.get(
            f"/app/jobs/job?guild=guild&proposal={result.proposal.id}&inline=true"
        )
        assert response.status_code == 200
        assert "Subscribed to proposal updates." in response.text
        assert "Unsubscribe" in response.text
        assert 'hx-swap-oob="outerHTML"' in response.text


def test_production_session_cookie_is_secure_http_only_and_same_site() -> None:
    client, _, _, _ = web_system(secure_cookies=True)
    with client:
        response = client.get("/launch?code=good", follow_redirects=False)
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_server_rail_accumulates_launches_and_switches_without_javascript() -> None:
    client, repository, _, _ = web_system()
    now = utcnow()
    repository.guilds["guild-2"] = GuildConfig(
        guild_id="guild-2",
        guild_name="Second Guild",
        output_channel_id="channel-2",
        proposal_timeout_seconds=60,
        configured_by="admin",
        created_at=now,
        updated_at=now,
    )
    with client:
        login(client)
        second = client.get("/launch?code=second", follow_redirects=False)
        assert second.status_code == 303
        dashboard = client.get("/app?guild=guild-2")

    assert "Test Guild" in dashboard.text
    assert "Second Guild" in dashboard.text
    assert 'class="server-button selected"' in dashboard.text
    assert 'href="/app?guild=guild"' in dashboard.text
    assert 'href="/app?guild=guild-2"' in dashboard.text
    assert "onchange=" not in dashboard.text
    assert "Add another server" in dashboard.text
    assert 'aria-label="Workspace navigation"' in dashboard.text


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
        assert "New type" in types.text
        assert "Delete type" not in types.text
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
        custom_type = asyncio.run(
            repository.save_type(
                "guild",
                "custom-type",
                "Policy",
                "policy",
                "Change a policy",
                "admin",
                utcnow(),
            )
        )
        assert custom_type.proposal_type
        types_with_custom = client.get("/app/types?guild=guild")
        assert "Edit Policy" in types_with_custom.text
        assert "Delete Policy?" in types_with_custom.text
        client.post(
            "/app/types/custom-type/delete",
            data={"csrf": csrf(client), "guild_id": "guild"},
        )
        assert tasks.workspace[-1]["action"] == "type_delete"

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
        assert "https://cdn.discordapp.com" in job.headers["content-security-policy"]
        assert job.headers["cache-control"] == "no-store"
        assert "max-age=31536000" in job.headers["strict-transport-security"]

        bad_csrf = client.post(
            "/app/preferences",
            data={"csrf": "bad", "guild_id": "guild"},
        )
        assert bad_csrf.status_code == 403
        worker.admin = False
        forbidden = client.get("/app/settings?guild=guild")
        assert forbidden.status_code == 403
        assert "You no longer have access" in forbidden.text
        assert "Manage Server" in forbidden.text

        invalid = client.post(
            "/app/settings",
            data={
                "csrf": csrf(client),
                "guild_id": "guild",
                "channel_id": "channel",
                "duration_minutes": "not-a-number",
            },
        )
        assert invalid.status_code == 422
        assert "Check the information you entered" in invalid.text
        assert "duration minutes" in invalid.text


def test_created_proposal_job_renders_web_timestamp() -> None:
    client, repository, _, _ = web_system()
    now = utcnow()
    result = asyncio.run(
        repository.create_proposal(
            "web-job-proposal",
            "guild",
            "Test Guild",
            "channel",
            "Rendered deadline",
            "rendered deadline",
            "",
            "",
            "",
            now,
            now + timedelta(minutes=2),
        )
    )
    assert result.proposal
    asyncio.run(
        repository.set_workspace_job(
            "create-job",
            "guild",
            SignedTokenCodec("secret").fingerprint("admin"),
            "create",
            "succeeded",
            "Proposal created successfully. It will pass <t:123:R> unless vetoed.",
            result.proposal.id,
            now,
            3600,
        )
    )

    with client:
        login(client)
        response = client.get("/app/jobs/create-job?guild=guild")

    assert response.status_code == 200
    assert "<t:123:R>" not in response.text
    assert f'data-relative="{result.proposal.deadline_at.isoformat()}"' in response.text
    assert "unless vetoed" in response.text


def test_navigation_theme_controls_and_dialogs_are_csp_safe() -> None:
    client, repository, _, _ = web_system()
    with client:
        login(client)
        overview = client.get("/app?guild=guild")
        assert 'aria-current="page"' in overview.text
        assert "data-theme-toggle" in overview.text
        assert "/static/theme.js" in overview.text
        assert "?v=dev" in overview.text
        assert "includeIndicatorStyles" in overview.text
        assert "Vite-oh proposals" not in overview.text

        now = utcnow()
        result = asyncio.run(
            repository.create_proposal(
                "dialog",
                "guild",
                "Test Guild",
                "channel",
                "Delete me",
                "delete me",
                "",
                "",
                "",
                now,
                now + timedelta(minutes=1),
            )
        )
        assert result.proposal
        repository.proposals[result.proposal.id] = replace(
            result.proposal, message_id="999"
        )
        detail = client.get(f"/app/proposals/{result.proposal.id}?guild=guild")

    assert "delete-proposal-dialog" in detail.text
    assert "data-dialog-open" in detail.text
    assert "onsubmit=" not in detail.text


def test_resolved_history_can_be_archived_and_purged_from_archive() -> None:
    client, repository, tasks, _ = web_system()
    now = utcnow()
    result = asyncio.run(
        repository.create_proposal(
            "history",
            "guild",
            "Test Guild",
            "channel",
            "Old decision",
            "old decision",
            "",
            "",
            "",
            now,
            now + timedelta(minutes=1),
        )
    )
    assert result.proposal
    terminal = replace(
        result.proposal,
        status=ProposalStatus.PASSED,
        terminal_at=now,
        message_id="999",
        effects_complete=True,
    )
    repository.proposals[terminal.id] = terminal

    with client:
        login(client)
        overview = client.get("/app?guild=guild")
        detail = client.get(f"/app/proposals/{terminal.id}?guild=guild")
        archive_post = client.post(
            f"/app/proposals/{terminal.id}/archive",
            data={"csrf": csrf(client), "guild_id": "guild"},
            follow_redirects=False,
        )

    assert "Old decision" in overview.text
    assert "View archive" in overview.text
    assert "archive-proposal-dialog" in detail.text
    assert archive_post.status_code == 303
    assert tasks.workspace[-1]["action"] == "archive"

    asyncio.run(repository.archive_proposal(terminal.id, "guild", "admin", now))
    with client:
        archived = client.get("/app/archive?guild=guild")
        archived_detail = client.get(f"/app/proposals/{terminal.id}?guild=guild")
        purge_post = client.post(
            f"/app/proposals/{terminal.id}/purge",
            data={"csrf": csrf(client), "guild_id": "guild"},
            follow_redirects=False,
        )

    assert "Old decision" in archived.text
    assert "History archive" in archived.text
    assert "purge-proposal-dialog" in archived_detail.text
    assert "Delete permanently" in archived_detail.text
    assert "Restore to history" in archived_detail.text
    assert purge_post.status_code == 303
    assert tasks.workspace[-1]["action"] == "purge"


def test_unconfigured_guild_routes_admin_to_setup() -> None:
    client, repository, _, _ = web_system()
    repository.guilds.clear()
    with client:
        login(client)
        dashboard = client.get("/app?guild=guild")
        assert "not configured" in dashboard.text
        settings = client.get("/app/settings?guild=guild")
        assert settings.status_code == 200
