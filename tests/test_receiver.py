import json
import time
from datetime import timedelta

import pytest
from nacl.signing import SigningKey

from tests.fakes import FakeRepository, FakeTasks
from viteoh.components import component_id
from viteoh.config import Settings
from viteoh.domain import utcnow
from viteoh.receiver import InteractionReceiver
from viteoh.security import SignatureVerifier


@pytest.fixture
def receiver() -> tuple[InteractionReceiver, SigningKey, FakeTasks]:
    key = SigningKey.generate()
    tasks = FakeTasks()
    settings = Settings(discord_public_key=key.verify_key.encode().hex())
    return (
        InteractionReceiver(
            settings, SignatureVerifier(settings.discord_public_key), tasks
        ),
        key,
        tasks,
    )


async def signed_receive(
    receiver: InteractionReceiver, key: SigningKey, payload: dict[str, object]
) -> tuple[int, dict[str, object]]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = key.sign(timestamp.encode() + body).signature.hex()
    return await receiver.receive(body, signature, timestamp)


async def test_ping_and_invalid_signature(
    receiver: tuple[InteractionReceiver, SigningKey, FakeTasks],
) -> None:
    service, key, _ = receiver
    status, response = await signed_receive(service, key, {"type": 1})
    assert status == 200
    assert response == {"type": 1}
    status, _ = await service.receive(b"{}", "bad", "0")
    assert status == 401


async def test_command_is_deferred_and_enqueued(
    receiver: tuple[InteractionReceiver, SigningKey, FakeTasks],
) -> None:
    service, key, tasks = receiver
    payload = {
        "id": "42",
        "type": 2,
        "guild_id": "guild",
        "member": {"user": {"id": "user"}},
        "data": {"name": "view"},
        "token": "token",
    }
    _, response = await signed_receive(service, key, payload)
    assert response["type"] == 5
    assert tasks.interactions == [payload]


async def test_veto_prompts_without_worker(
    receiver: tuple[InteractionReceiver, SigningKey, FakeTasks],
) -> None:
    service, key, tasks = receiver
    proposal_id = "12345678-1234-1234-1234-123456789abc"
    payload = {
        "id": "43",
        "type": 3,
        "guild_id": "guild",
        "member": {"user": {"id": "user"}},
        "data": {"custom_id": component_id("proposal", "veto", proposal_id)},
    }
    _, response = await signed_receive(service, key, payload)
    assert response["type"] == 9
    assert response["data"]["custom_id"] == f"proposal-veto|{proposal_id}"
    assert not tasks.interactions


async def test_nudge_button_opens_private_user_selector(
    receiver: tuple[InteractionReceiver, SigningKey, FakeTasks],
) -> None:
    service, key, tasks = receiver
    proposal_id = "12345678-1234-1234-1234-123456789abc"
    _, response = await signed_receive(
        service,
        key,
        {
            "id": "nudge",
            "type": 3,
            "guild_id": "guild",
            "member": {"user": {"id": "user"}},
            "data": {"custom_id": component_id("proposal", "nudge", proposal_id)},
        },
    )
    assert response["type"] == 4
    selector = response["data"]["components"][0]["components"][0]
    assert selector["type"] == 5
    assert selector["custom_id"] == f"proposal:nudge-select:{proposal_id}"
    assert not tasks.interactions


async def test_create_command_is_deferred_without_opening_a_modal() -> None:
    key = SigningKey.generate()
    repository = FakeRepository()
    now = utcnow()
    await repository.set_guild_config(
        "guild", "Test Guild", "channel", 60, "admin", now
    )
    tasks = FakeTasks()
    service = InteractionReceiver(
        Settings(discord_public_key=key.verify_key.encode().hex()),
        SignatureVerifier(key.verify_key.encode().hex()),
        tasks,
        repository,  # type: ignore[arg-type]
    )
    _, response = await signed_receive(
        service,
        key,
        {
            "id": "create",
            "type": 2,
            "guild_id": "guild",
            "member": {"user": {"id": "user"}},
            "data": {
                "name": "proposal",
                "options": [
                    {
                        "name": "create",
                        "type": 1,
                        "options": [{"name": "title", "value": "Quiet hours"}],
                    }
                ],
            },
        },
    )
    assert response["type"] == 5
    assert tasks.interactions[0]["id"] == "create"


async def test_type_autocomplete_and_admin_delete_confirmation() -> None:
    key = SigningKey.generate()
    repository = FakeRepository()
    now = utcnow()
    await repository.set_guild_config(
        "guild", "Test Guild", "channel", 60, "admin", now
    )
    saved = await repository.save_type(
        "guild",
        None,
        "Policy",
        "policy",
        "Change a server policy",
        "admin",
        now,
    )
    assert saved.proposal_type
    service = InteractionReceiver(
        Settings(discord_public_key=key.verify_key.encode().hex()),
        SignatureVerifier(key.verify_key.encode().hex()),
        FakeTasks(),
        repository,  # type: ignore[arg-type]
    )
    _, choices = await signed_receive(
        service,
        key,
        {
            "id": "autocomplete",
            "type": 4,
            "guild_id": "guild",
            "member": {"user": {"id": "admin"}},
            "data": {
                "name": "proposal",
                "options": [
                    {
                        "name": "type",
                        "type": 2,
                        "options": [
                            {
                                "name": "delete",
                                "type": 1,
                                "options": [
                                    {
                                        "name": "type",
                                        "value": "pol",
                                        "focused": True,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        },
    )
    assert choices["data"]["choices"][0]["value"] == saved.proposal_type.id

    _, confirmation = await signed_receive(
        service,
        key,
        {
            "id": "delete",
            "type": 2,
            "guild_id": "guild",
            "member": {
                "user": {"id": "admin"},
                "permissions": str(1 << 5),
            },
            "data": {
                "name": "proposal",
                "options": [
                    {
                        "name": "type",
                        "type": 2,
                        "options": [
                            {
                                "name": "delete",
                                "type": 1,
                                "options": [
                                    {"name": "type", "value": saved.proposal_type.id}
                                ],
                            }
                        ],
                    }
                ],
            },
        },
    )
    assert confirmation["type"] == 4
    assert confirmation["data"]["components"]


async def test_another_guild_is_accepted(
    receiver: tuple[InteractionReceiver, SigningKey, FakeTasks],
) -> None:
    service, key, tasks = receiver
    _, response = await signed_receive(
        service,
        key,
        {
            "id": "44",
            "type": 2,
            "guild_id": "other",
            "member": {"user": {"id": "user"}},
            "data": {"name": "view"},
        },
    )
    assert response["type"] == 5
    assert tasks.interactions[-1]["guild_id"] == "other"


async def test_autocomplete_returns_filtered_uuid_choices() -> None:
    key = SigningKey.generate()
    tasks = FakeTasks()
    repository = FakeRepository()
    now = utcnow()
    await repository.create_proposal(
        "one",
        "guild",
        "Test Guild",
        "channel",
        "Alpha proposal",
        "alpha proposal",
        "",
        "",
        "",
        now,
        now + timedelta(minutes=5),
    )
    service = InteractionReceiver(
        Settings(discord_public_key=key.verify_key.encode().hex()),
        SignatureVerifier(key.verify_key.encode().hex()),
        tasks,
        repository,  # type: ignore[arg-type]
    )
    _, response = await signed_receive(
        service,
        key,
        {
            "id": "45",
            "type": 4,
            "guild_id": "guild",
            "member": {"user": {"id": "user"}},
            "data": {
                "name": "proposal",
                "options": [
                    {
                        "name": "nudge",
                        "type": 1,
                        "options": [
                            {
                                "name": "proposal",
                                "value": "ALP",
                                "focused": True,
                            }
                        ],
                    }
                ],
            },
        },
    )
    assert response["type"] == 8
    assert response["data"]["choices"] == [
        {"name": "Alpha proposal", "value": repository.next_id}
    ]
    assert not tasks.interactions


async def test_autocomplete_errors_return_empty_choices() -> None:
    class BrokenRepository:
        async def list_active(self, guild_id: str) -> list[object]:
            raise RuntimeError(guild_id)

    key = SigningKey.generate()
    service = InteractionReceiver(
        Settings(discord_public_key=key.verify_key.encode().hex()),
        SignatureVerifier(key.verify_key.encode().hex()),
        FakeTasks(),
        BrokenRepository(),  # type: ignore[arg-type]
    )
    _, response = await signed_receive(
        service,
        key,
        {
            "id": "46",
            "type": 4,
            "guild_id": "guild",
            "member": {"user": {"id": "user"}},
            "data": {
                "name": "proposal",
                "options": [
                    {
                        "name": "delete",
                        "type": 1,
                        "options": [
                            {
                                "name": "proposal",
                                "value": "",
                                "focused": True,
                            }
                        ],
                    }
                ],
            },
        },
    )
    assert response == {"type": 8, "data": {"choices": []}}
