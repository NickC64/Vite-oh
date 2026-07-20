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
    command_options = options
    if command_options is None:
        command_options = [{"name": "name", "value": value}] if value else []
    return {
        "id": interaction_id,
        "type": 2,
        "token": "token",
        "guild_id": guild_id,
        "member": {
            "user": {"id": user_id},
            "permissions": str(permissions),
        },
        "data": {"name": name, "options": command_options},
    }


async def test_new_is_durable_scheduled_and_idempotent(
    system: tuple[InteractionProcessor, FakeRepository, FakeTasks, FakeDiscord],
) -> None:
    processor, repository, tasks, discord = system
    await processor.process(command("new", value=" Alice "))
    proposal = next(iter(repository.proposals.values()))
    assert proposal.display_name == "Alice"
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
    assert "subscribed" in discord.responses[0]
    assert "already" in discord.responses[1]
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
        "data": {"custom_id": component_id("confirm-veto", proposal.id)},
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
    await processor.process(command("delete", interaction_id="2", value="Alice"))
    assert "Manage Server" in discord.responses[-1]
    await processor.process(
        command("delete", interaction_id="3", user_id="owner", value="Alice")
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
    assert "not been configured" in discord.responses[-1]
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
            "data": {"custom_id": component_id("confirm-veto", proposal.id)},
        }
    )
    assert proposal.status is ProposalStatus.ACTIVE
    assert "another server" in discord.responses[-1]
