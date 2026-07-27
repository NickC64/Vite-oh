import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from tests.fakes import FakeDiscord, FakeRepository, FakeTasks
from viteoh.components import component_id
from viteoh.config import Settings
from viteoh.domain import GuildConfig, ProposalStatus, utcnow
from viteoh.worker import MANAGE_GUILD, InteractionProcessor


@pytest.fixture
def system() -> tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord]:
    repository = FakeRepository()
    tasks = FakeTasks()
    discord = FakeDiscord()
    settings = Settings(
        discord_application_id="app",
        discord_owner_user_id="owner",
        workspace_signing_secret="secret",
        workspace_url="https://workspace.example",
    )
    now = utcnow()
    repository.guilds["guild"] = GuildConfig(
        guild_id="guild",
        guild_name="Test Guild",
        output_channel_id="channel",
        proposal_timeout_seconds=60,
        configured_by="owner",
        created_at=now,
        updated_at=now,
    )
    processor = InteractionProcessor(settings, repository, tasks, discord)  # type: ignore[arg-type]
    return processor, repository, tasks, discord


def command(
    name: str,
    *,
    interaction_id: str = "1",
    user_id: str = "user",
    value: str = "",
    guild_id: str = "guild",
    permissions: int = 0,
    options: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if name == "new":
        command_options = list(options or [])
        if not any(item["name"] == "title" for item in command_options):
            command_options.insert(0, {"name": "title", "value": value})
        return {
            "id": interaction_id,
            "type": 2,
            "token": "token",
            "guild_id": guild_id,
            "member": {
                "user": {"id": user_id},
                "permissions": str(permissions),
            },
            "data": {
                "name": "proposal",
                "options": [{"name": "create", "type": 1, "options": command_options}],
            },
        }
    if name == "delete":
        return {
            "id": interaction_id,
            "type": 3,
            "token": "token",
            "guild_id": guild_id,
            "member": {
                "user": {"id": user_id},
                "permissions": str(permissions),
            },
            "data": {"custom_id": component_id("proposal", "confirm-delete", value)},
        }
    path = {
        "setup": "configure",
        "view": "list",
        "nudge": "nudge",
        "sub": "preferences",
        "unsub": "preferences",
        "nudges": "preferences",
    }.get(name, name)
    command_options = list(options or [])
    if name == "sub":
        command_options = [{"name": "new_proposals", "value": True}]
    elif name == "unsub":
        command_options = [{"name": "new_proposals", "value": False}]
    elif name == "nudges":
        command_options = [
            {"name": "nudges", "value": item.get("value")} for item in command_options
        ]
    return {
        "id": interaction_id,
        "type": 2,
        "token": "token",
        "guild_id": guild_id,
        "member": {
            "user": {"id": user_id},
            "permissions": str(permissions),
        },
        "data": {
            "name": "proposal",
            "options": [{"name": path, "type": 1, "options": command_options}],
        },
    }


async def test_new_is_durable_scheduled_and_idempotent(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, tasks, discord = system
    await processor.process(command("new", value=" Alice "))
    proposal = next(iter(repository.proposals.values()))
    assert proposal.title == "Alice"
    assert proposal.message_id == "999"
    assert proposal.task_scheduled
    assert len(tasks.deadlines) == 1
    assert len(discord.announcements) == 1

    await processor.process(command("new", value="Alice"))
    assert len(repository.proposals) == 1
    assert len(discord.announcements) == 1


async def test_subscriptions_and_view(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, _, _, discord = system
    await processor.process(command("sub"))
    await processor.process(command("sub", interaction_id="2"))
    assert "New proposal DMs: **on**" in discord.responses[0]
    assert "New proposal DMs: **on**" in discord.responses[1]
    await processor.process(command("view", interaction_id="3"))
    assert discord.responses[-1] == "There are no active proposals."


async def test_timer_cannot_pass_early_and_passes_once(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", value="Alice"))
    proposal = next(iter(repository.proposals.values()))

    assert await processor.finalize(proposal.id) == "not_due"
    assert repository.proposals[proposal.id].status is ProposalStatus.ACTIVE

    due = proposal.deadline_at + timedelta(seconds=1)
    monkeypatch.setattr("viteoh.worker.utcnow", lambda: due)
    assert await processor.finalize(proposal.id) == "transitioned"
    assert repository.proposals[proposal.id].status is ProposalStatus.PASSED
    assert await processor.finalize(proposal.id) == "already_terminal"
    assert len(discord.synced) == 1
    assert len(discord.outcomes) == 1


async def test_veto_wins_before_deadline_and_is_anonymous(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, tasks, discord = system
    await processor.process(command("new", value="Alice"))
    proposal = next(iter(repository.proposals.values()))
    payload = {
        "id": "2",
        "type": 3,
        "token": "token",
        "guild_id": "guild",
        "member": {"user": {"id": "vetoing-user"}},
        "data": {"custom_id": component_id("proposal", "confirm-veto", proposal.id)},
    }
    await processor.process(payload)
    assert repository.proposals[proposal.id].status is ProposalStatus.VETOED
    assert tasks.deleted
    assert "vetoing-user" not in repr(repository.proposals[proposal.id])
    assert "anonymously" in discord.responses[-1]


async def test_reconciliation_repairs_missing_task(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, tasks, _ = system
    await processor.process(command("new", value="Alice"))
    proposal = next(iter(repository.proposals.values()))
    assert proposal.deadline_task_name
    tasks.missing.add(proposal.deadline_task_name)
    result = await processor.reconcile()
    assert result["repaired"] == 1


async def test_workspace_guild_summaries_filter_and_describe_live_memberships(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, _, _, _ = system
    result = await processor.workspace_guild_summaries(
        ["guild", "missing", "guild", "guild-2"], "owner"
    )

    assert [item["guild_id"] for item in result["guilds"]] == ["guild-2", "guild"]
    assert result["guilds"][0]["configured"] is False
    assert result["guilds"][1]["configured"] is True
    assert all(item["can_manage"] for item in result["guilds"])


async def test_reconciliation_upgrades_legacy_active_announcement_once(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", value="Alice"))
    proposal = next(iter(repository.proposals.values()))
    repository.proposals[proposal.id] = replace(proposal, render_version=0)

    first = await processor.reconcile()
    second = await processor.reconcile()

    assert first["rendered"] == 1
    assert second["rendered"] == 0
    assert repository.proposals[proposal.id].render_version == 1
    assert discord.synced[-1].id == proposal.id


async def test_deadline_and_veto_race_has_one_terminal_winner(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, _ = system
    await processor.process(command("new", value="Alice"))
    proposal = next(iter(repository.proposals.values()))
    boundary = proposal.deadline_at
    results = await asyncio.gather(
        repository.transition(proposal.id, ProposalStatus.PASSED, boundary),
        repository.transition(proposal.id, ProposalStatus.VETOED, boundary),
    )
    assert sum(result.changed for result in results) == 1
    assert repository.proposals[proposal.id].status is ProposalStatus.PASSED


async def test_reconciliation_completes_pending_terminal_effects(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", value="Alice"))
    proposal = next(iter(repository.proposals.values()))
    repository.proposals[proposal.id] = replace(
        proposal,
        status=ProposalStatus.VETOED,
        terminal_at=proposal.created_at,
        effects_complete=False,
    )
    result = await processor.reconcile()
    assert result["effects_retried"] == 1
    assert repository.proposals[proposal.id].effects_complete
    assert discord.synced[-1].status is ProposalStatus.VETOED


async def test_owner_delete_is_enforced(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", value="Alice"))
    proposal = next(iter(repository.proposals.values()))
    await processor.process(command("delete", interaction_id="2", value=proposal.id))
    assert "Manage Server" in discord.responses[-1]
    await processor.process(
        command("delete", interaction_id="3", user_id="owner", value=proposal.id)
    )
    assert next(iter(repository.proposals.values())).status is ProposalStatus.DELETED


async def test_setup_requires_manage_server_and_supports_partial_updates(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    repository.guilds.clear()
    await processor.process(command("setup"))
    assert "Manage Server" in discord.responses[-1]
    await processor.process(
        command(
            "setup",
            interaction_id="2",
            permissions=MANAGE_GUILD,
            options=[
                {"name": "channel", "value": "channel"},
                {"name": "duration_minutes", "value": 1},
            ],
        )
    )
    assert repository.guilds["guild"].proposal_timeout_seconds == 60
    await processor.process(
        command(
            "setup",
            interaction_id="3",
            permissions=MANAGE_GUILD,
            options=[{"name": "duration_minutes", "value": 2880}],
        )
    )
    assert repository.guilds["guild"].proposal_timeout_seconds == 172800


async def test_unconfigured_guild_is_rejected_and_guilds_are_isolated(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", guild_id="guild-2", value="Alice"))
    assert "has not been configured" in discord.responses[-1]
    now = utcnow()
    repository.guilds["guild-2"] = GuildConfig(
        guild_id="guild-2",
        guild_name="Second Guild",
        output_channel_id="channel-2",
        proposal_timeout_seconds=60,
        configured_by="owner",
        created_at=now,
        updated_at=now,
    )
    await processor.process(command("new", value="Alice"))
    repository.next_id = "87654321-4321-4321-4321-cba987654321"
    await processor.process(
        command("new", interaction_id="2", guild_id="guild-2", value="Alice")
    )
    assert {item.guild_id for item in repository.proposals.values()} == {
        "guild",
        "guild-2",
    }


async def test_copied_component_cannot_cross_guild(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", value="Alice"))
    proposal = next(iter(repository.proposals.values()))
    await processor.process(
        {
            "id": "2",
            "type": 3,
            "token": "token",
            "guild_id": "guild-2",
            "member": {"user": {"id": "user"}},
            "data": {
                "custom_id": component_id("proposal", "confirm-veto", proposal.id)
            },
        }
    )
    assert proposal.status is ProposalStatus.ACTIVE
    assert "another server" in discord.responses[-1]


async def test_generalized_proposal_context_and_acknowledgement(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(
        command(
            "new",
            options=[
                {"name": "title", "value": "Adopt quiet hours"},
                {"name": "context", "value": "No pings after 10 PM."},
            ],
        )
    )
    proposal = next(iter(repository.proposals.values()))
    assert proposal.title == "Adopt quiet hours"
    assert proposal.context == "No pings after 10 PM."
    payload = {
        "id": "2",
        "type": 3,
        "token": "token",
        "guild_id": "guild",
        "member": {"user": {"id": "reviewer"}},
        "data": {"custom_id": component_id("proposal", "acknowledge", proposal.id)},
    }
    await processor.process(payload)
    await processor.process(payload)
    assert repository.proposals[proposal.id].acknowledgement_count == 1
    assert repository.acknowledgements[proposal.id] == {"reviewer"}
    assert discord.synced[-1].acknowledgement_count == 1
    assert "already acknowledged" in discord.responses[-1]


async def test_nudge_is_anonymous_deduplicated_and_respects_preference(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", value="Review the bylaws"))
    proposal = next(iter(repository.proposals.values()))
    options = [
        {"name": "proposal", "value": proposal.id},
        {"name": "user", "value": "target"},
    ]
    await processor.process(command("nudge", interaction_id="2", options=options))
    assert repository.nudges[proposal.id]["target"] == "delivered"
    assert discord.dms[-1][0] == "target"
    assert "Someone in" in discord.dms[-1][1]
    assert "user" not in discord.dms[-1][1]
    await processor.process(command("nudge", interaction_id="3", options=options))
    assert "already been nudged" in discord.responses[-1]
    assert len(discord.dms) == 1

    await processor.process(
        command(
            "nudges",
            interaction_id="4",
            user_id="target",
            options=[{"name": "enabled", "value": False}],
        )
    )
    repository.next_id = "87654321-4321-4321-4321-cba987654321"
    await processor.process(command("new", interaction_id="5", value="Second proposal"))
    second = repository.proposals[repository.next_id]
    await processor.process(
        command(
            "nudge",
            interaction_id="6",
            options=[
                {"name": "proposal", "value": second.id},
                {"name": "user", "value": "target"},
            ],
        )
    )
    assert "disabled" in discord.responses[-1]


async def test_nudge_user_selector_reuses_anonymous_delivery(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", value="Review the bylaws"))
    proposal = next(iter(repository.proposals.values()))
    await processor.process(
        {
            "id": "nudge-select",
            "type": 3,
            "token": "token",
            "guild_id": "guild",
            "member": {"user": {"id": "requester"}},
            "data": {
                "custom_id": component_id("proposal", "nudge-select", proposal.id),
                "values": ["target"],
            },
        }
    )
    assert repository.nudges[proposal.id]["target"] == "delivered"
    assert discord.dms[-1][0] == "target"
    assert "requester" not in discord.dms[-1][1]


async def test_nudge_rejects_self_bots_forged_ids_and_limit(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", value="Proposal"))
    proposal = next(iter(repository.proposals.values()))
    await processor.process(
        command(
            "nudge",
            interaction_id="2",
            options=[
                {"name": "proposal", "value": proposal.id},
                {"name": "user", "value": "user"},
            ],
        )
    )
    assert "yourself" in discord.responses[-1]
    await processor.process(
        command(
            "nudge",
            interaction_id="3",
            options=[
                {"name": "proposal", "value": proposal.id},
                {"name": "user", "value": "bot"},
            ],
        )
    )
    assert "Bots" in discord.responses[-1]
    await processor.process(
        command(
            "delete",
            interaction_id="4",
            user_id="owner",
            value="not-a-uuid",
        )
    )
    assert "no longer valid" in discord.responses[-1]
    repository.proposals[proposal.id] = replace(proposal, nudge_count=10)
    await processor.process(
        command(
            "nudge",
            interaction_id="5",
            options=[
                {"name": "proposal", "value": proposal.id},
                {"name": "user", "value": "target"},
            ],
        )
    )
    assert "limit of 10" in discord.responses[-1]


async def test_closed_dm_is_recorded_as_failed(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    discord.dm_failures.add("target")
    await processor.process(command("new", value="Proposal"))
    proposal = next(iter(repository.proposals.values()))
    await processor.process(
        command(
            "nudge",
            interaction_id="2",
            options=[
                {"name": "proposal", "value": proposal.id},
                {"name": "user", "value": "target"},
            ],
        )
    )
    assert repository.nudges[proposal.id]["target"] == "failed"
    assert "could not deliver" in discord.responses[-1]


async def test_acknowledgement_at_deadline_is_rejected(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", value="Proposal"))
    proposal = next(iter(repository.proposals.values()))
    monkeypatch.setattr("viteoh.worker.utcnow", lambda: proposal.deadline_at)
    await processor.process(
        {
            "id": "2",
            "type": 3,
            "token": "token",
            "guild_id": "guild",
            "member": {"user": {"id": "reviewer"}},
            "data": {"custom_id": component_id("proposal", "acknowledge", proposal.id)},
        }
    )
    assert repository.proposals[proposal.id].acknowledgement_count == 0
    assert "no longer active" in discord.responses[-1]


async def test_announcement_rendering_converges_when_finalize_races_ack(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", value="Proposal"))
    proposal = next(iter(repository.proposals.values()))
    original_sync = discord.sync_proposal_announcement
    raced = False

    async def racing_sync(current: object) -> str:
        nonlocal raced
        assert hasattr(current, "status")
        if not raced:
            raced = True
            await repository.transition(
                proposal.id, ProposalStatus.PASSED, proposal.deadline_at
            )
        return await original_sync(current)  # type: ignore[arg-type]

    discord.sync_proposal_announcement = racing_sync  # type: ignore[method-assign]
    await processor.process(
        {
            "id": "2",
            "type": 3,
            "token": "token",
            "guild_id": "guild",
            "member": {"user": {"id": "reviewer"}},
            "data": {"custom_id": component_id("proposal", "acknowledge", proposal.id)},
        }
    )
    assert discord.synced[-1].status is ProposalStatus.PASSED


async def test_custom_type_modal_creation_and_snapshot(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(
        {
            "id": "type-create",
            "type": 5,
            "token": "token",
            "guild_id": "guild",
            "member": {
                "user": {"id": "admin"},
                "permissions": str(MANAGE_GUILD),
            },
            "data": {
                "custom_id": "type-create|new",
                "components": [
                    {"components": [{"custom_id": "name", "value": "Policy"}]},
                    {
                        "components": [
                            {"custom_id": "description", "value": "Change policy"}
                        ]
                    },
                ],
            },
        }
    )
    proposal_type = next(iter(repository.types.values()))
    assert "created" in discord.responses[-1]

    await processor.process(
        command(
            "new",
            interaction_id="proposal-create",
            options=[
                {"name": "title", "value": "quiet hours"},
                {"name": "type", "value": proposal_type.id},
                {"name": "context", "value": "Reduce late pings"},
            ],
        )
    )
    proposal = next(iter(repository.proposals.values()))
    assert proposal.title == "quiet hours"
    assert proposal.type_name == "Policy"
    await repository.save_type(
        "guild",
        proposal_type.id,
        "Rules",
        "rules",
        proposal_type.description,
        "admin",
        utcnow(),
    )
    assert repository.proposals[proposal.id].type_name == "Policy"


async def test_veto_modal_reason_is_anonymous_public_and_outcome_is_idempotent(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", value="Document rollbacks"))
    proposal = next(iter(repository.proposals.values()))
    await processor.process(
        {
            "id": "veto",
            "type": 5,
            "token": "token",
            "guild_id": "guild",
            "member": {"user": {"id": "secret-vetoer"}},
            "data": {
                "custom_id": f"proposal-veto|{proposal.id}",
                "components": [
                    {
                        "components": [
                            {
                                "custom_id": "reason",
                                "value": "The rollback plan is incomplete.",
                            }
                        ]
                    }
                ],
            },
        }
    )
    terminal = repository.proposals[proposal.id]
    assert terminal.veto_reason == "The rollback plan is incomplete."
    assert "secret-vetoer" not in repr(terminal)
    assert discord.synced[-1].veto_reason
    assert len(discord.outcomes) == 1
    await processor.sync_terminal_effects(repository.proposals[proposal.id])
    assert len(discord.outcomes) == 1


async def test_custom_type_delete_confirmation_revalidates_admin(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    proposal_type = (
        await repository.save_type(
            "guild",
            None,
            "Policy",
            "policy",
            "Change policy",
            "admin",
            utcnow(),
        )
    ).proposal_type
    assert proposal_type
    control = component_id("type", "confirm-delete", proposal_type.id)
    payload = {
        "id": "delete-type",
        "type": 3,
        "token": "token",
        "guild_id": "guild",
        "member": {"user": {"id": "user"}, "permissions": "0"},
        "data": {"custom_id": control},
    }
    await processor.process(payload)
    assert "Manage Server" in discord.responses[-1]
    payload["member"] = {
        "user": {"id": "admin"},
        "permissions": str(MANAGE_GUILD),
    }
    await processor.process(payload)
    assert await repository.get_type("guild", proposal_type.id) is None


async def test_workspace_launcher_is_private_one_time_and_contextual(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    payload = {
        "id": "open",
        "type": 2,
        "token": "token",
        "guild_id": "guild",
        "member": {"user": {"id": "user"}},
        "data": {"name": "proposal"},
    }
    await processor.process(payload)
    components = discord.response_components[-1]
    assert components
    url = str(components[0]["components"][0]["url"])  # type: ignore[index]
    assert url.startswith("https://workspace.example/launch?code=")
    code = url.split("code=", 1)[1]
    first = await processor.exchange_workspace_launch(code)
    assert first == {
        "user_id": "user",
        "guild_id": "guild",
        "guild_ids": ["guild"],
        "proposal_id": None,
        "display_name": "Discord member",
    }
    assert await processor.exchange_workspace_launch(code) is None
    assert next(iter(repository.workspace_launches.values())).consumed_at


async def test_dm_workspace_launcher_includes_every_configured_mutual_guild(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    now = utcnow()
    repository.guilds["guild-2"] = GuildConfig(
        guild_id="guild-2",
        guild_name="Second Guild",
        output_channel_id="channel-2",
        proposal_timeout_seconds=60,
        configured_by="owner",
        created_at=now,
        updated_at=now,
    )
    await processor.process(
        {
            "id": "dm-open",
            "type": 2,
            "token": "token",
            "user": {"id": "user"},
            "data": {"name": "proposal"},
        }
    )
    assert "2 available servers" in discord.responses[-1]
    components = discord.response_components[-1]
    assert components
    url = str(components[0]["components"][0]["url"])  # type: ignore[index]
    launch = await processor.exchange_workspace_launch(url.split("code=", 1)[1])
    assert launch
    assert launch["guild_ids"] == ["guild-2", "guild"]


async def test_workspace_jobs_reuse_durable_proposal_and_preference_logic(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, tasks, discord = system
    create = {
        "id": "web-create",
        "action": "create",
        "actor_user_id": "user",
        "guild_id": "guild",
        "data": {
            "title": "Quiet hours",
            "context": "No late pings",
            "type_id": "",
        },
    }
    await processor.process_workspace(create)
    job = repository.workspace_jobs["web-create"]
    assert job.status == "succeeded"
    assert job.proposal_id
    assert repository.proposals[job.proposal_id].title == "Quiet hours"
    assert repository.proposals[job.proposal_id].type_name == ""
    assert tasks.deadlines
    assert discord.announcements

    await processor.process_workspace(
        {
            "id": "web-preferences",
            "action": "preferences",
            "actor_user_id": "user",
            "guild_id": "guild",
            "data": {"new_proposals": True, "nudges": False},
        }
    )
    assert repository.workspace_jobs["web-preferences"].status == "succeeded"
    assert await repository.get_guild_subscription("guild", "user")
    assert not await repository.get_nudges_enabled("guild", "user")


async def test_workspace_admin_jobs_validate_permission_and_types(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, _ = system
    denied = {
        "id": "denied",
        "action": "configure",
        "actor_user_id": "user",
        "guild_id": "guild",
        "data": {"channel_id": "channel", "duration_minutes": 5},
    }
    await processor.process_workspace(denied)
    assert repository.workspace_jobs["denied"].status == "failed"
    assert "Manage Server" in repository.workspace_jobs["denied"].message

    save_type = {
        "id": "type",
        "action": "type_save",
        "actor_user_id": "admin",
        "guild_id": "guild",
        "data": {"name": "Policy", "description": "Policy changes"},
    }
    await processor.process_workspace(save_type)
    assert repository.workspace_jobs["type"].status == "succeeded"
    custom = next(iter(repository.types.values()))

    await processor.process_workspace(
        {
            "id": "delete-type",
            "action": "type_delete",
            "actor_user_id": "admin",
            "guild_id": "guild",
            "data": {"type_id": custom.id},
        }
    )
    assert repository.workspace_jobs["delete-type"].status == "succeeded"
    assert not repository.types


async def test_workspace_admin_can_archive_then_purge_history(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(command("new", value="Retire an old rule"))
    proposal = next(iter(repository.proposals.values()))
    repository.proposals[proposal.id] = replace(
        proposal,
        status=ProposalStatus.PASSED,
        terminal_at=utcnow(),
        effects_complete=True,
        outcome_message_id="outcome",
    )

    await processor.process_workspace(
        {
            "id": "archive-history",
            "action": "archive",
            "actor_user_id": "admin",
            "guild_id": "guild",
            "data": {"proposal_id": proposal.id},
        }
    )
    archived = repository.proposals[proposal.id]
    assert archived.archived
    assert archived.archived_by == "admin"
    assert repository.workspace_jobs["archive-history"].status == "succeeded"
    assert not await repository.list_guild_proposals("guild")
    assert await repository.list_guild_proposals("guild", archived=True) == [archived]

    await processor.process_workspace(
        {
            "id": "restore-history",
            "action": "restore",
            "actor_user_id": "admin",
            "guild_id": "guild",
            "data": {"proposal_id": proposal.id},
        }
    )
    assert repository.workspace_jobs["restore-history"].status == "succeeded"
    assert not repository.proposals[proposal.id].archived
    await repository.archive_proposal(proposal.id, "guild", "admin", utcnow())
    archived = repository.proposals[proposal.id]

    repository.acknowledgements[proposal.id] = {"member"}
    repository.nudges[proposal.id] = {"member": "delivered"}
    repository.subscribers[proposal.id] = {"member"}
    repository.delivered.add((proposal.id, "passed", "member"))
    await processor.process_workspace(
        {
            "id": "purge-history",
            "action": "purge",
            "actor_user_id": "admin",
            "guild_id": "guild",
            "data": {"proposal_id": proposal.id},
        }
    )
    assert repository.workspace_jobs["purge-history"].status == "succeeded"
    assert proposal.id not in repository.proposals
    assert proposal.id not in repository.acknowledgements
    assert proposal.id not in repository.nudges
    assert proposal.id not in repository.subscribers
    assert not repository.delivered
    assert discord.deleted_history == [archived]

    await repository.set_workspace_job(
        "purge-retry",
        "guild",
        processor.workspace_codec.fingerprint("admin"),
        "purge",
        "processing",
        "",
        None,
        utcnow(),
        3600,
    )
    await processor.process_workspace(
        {
            "id": "purge-retry",
            "action": "purge",
            "actor_user_id": "admin",
            "guild_id": "guild",
            "data": {"proposal_id": proposal.id},
        }
    )
    assert repository.workspace_jobs["purge-retry"].status == "succeeded"


async def test_active_or_unarchived_proposals_cannot_be_purged(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, _ = system
    await processor.process(command("new", value="Still underway"))
    proposal = next(iter(repository.proposals.values()))
    active_archive = await repository.archive_proposal(
        proposal.id, "guild", "admin", utcnow()
    )
    assert active_archive.reason == "active"

    repository.proposals[proposal.id] = replace(
        proposal, status=ProposalStatus.PASSED, terminal_at=utcnow()
    )
    await processor.process_workspace(
        {
            "id": "premature-purge",
            "action": "purge",
            "actor_user_id": "admin",
            "guild_id": "guild",
            "data": {"proposal_id": proposal.id},
        }
    )
    assert repository.workspace_jobs["premature-purge"].status == "failed"
    assert "Archive" in repository.workspace_jobs["premature-purge"].message
