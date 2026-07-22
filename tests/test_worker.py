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
        values = {str(item["name"]): item.get("value", "") for item in options or []}
        return {
            "id": interaction_id,
            "type": 5,
            "token": "token",
            "guild_id": guild_id,
            "member": {
                "user": {"id": user_id},
                "permissions": str(permissions),
            },
            "data": {
                "custom_id": "proposal-create|builtin:general",
                "components": [
                    {
                        "components": [
                            {
                                "custom_id": "subject",
                                "value": str(values.get("title", value)),
                            }
                        ]
                    },
                    {
                        "components": [
                            {
                                "custom_id": "context",
                                "value": str(values.get("context", "")),
                            }
                        ]
                    },
                ],
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
    assert "no longer configured" in discord.responses[-1]
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


async def test_custom_template_modal_creation_and_snapshot(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    await processor.process(
        {
            "id": "template-create",
            "type": 5,
            "token": "token",
            "guild_id": "guild",
            "member": {
                "user": {"id": "admin"},
                "permissions": str(MANAGE_GUILD),
            },
            "data": {
                "custom_id": "template-create|new|1",
                "components": [
                    {"components": [{"custom_id": "name", "value": "Policy"}]},
                    {
                        "components": [
                            {"custom_id": "description", "value": "Change policy"}
                        ]
                    },
                    {
                        "components": [
                            {"custom_id": "subject_label", "value": "What changes?"}
                        ]
                    },
                    {"components": [{"custom_id": "context_label", "value": "Why?"}]},
                    {
                        "components": [
                            {
                                "custom_id": "title_format",
                                "value": "Adopt {subject} as policy",
                            }
                        ]
                    },
                ],
            },
        }
    )
    template = next(iter(repository.templates.values()))
    assert template.context_required
    assert "created" in discord.responses[-1]

    await processor.process(
        {
            "id": "proposal-create",
            "type": 5,
            "token": "token",
            "guild_id": "guild",
            "member": {"user": {"id": "user"}},
            "data": {
                "custom_id": f"proposal-create|{template.id}",
                "components": [
                    {"components": [{"custom_id": "subject", "value": "quiet hours"}]},
                    {
                        "components": [
                            {"custom_id": "context", "value": "Reduce late pings"}
                        ]
                    },
                ],
            },
        }
    )
    proposal = next(iter(repository.proposals.values()))
    assert proposal.title == "Adopt quiet hours as policy"
    assert proposal.template_name == "Policy"
    await repository.save_template(
        "guild",
        template.id,
        "Rules",
        "rules",
        template.description,
        template.subject_label,
        template.context_label,
        template.title_format,
        False,
        "admin",
        utcnow(),
    )
    assert repository.proposals[proposal.id].template_name == "Policy"


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


async def test_custom_template_delete_confirmation_revalidates_admin(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, _, discord = system
    template = (
        await repository.save_template(
            "guild",
            None,
            "Policy",
            "policy",
            "Change policy",
            "Subject",
            "Context",
            "{subject}",
            False,
            "admin",
            utcnow(),
        )
    ).template
    assert template
    control = component_id("template", "confirm-delete", template.id)
    payload = {
        "id": "delete-template",
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
    assert await repository.get_template("guild", template.id) is None
