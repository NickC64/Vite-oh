import json
import time

import pytest
from nacl.signing import SigningKey

from tests.fakes import FakeTasks
from viteoh.components import component_id
from viteoh.config import Settings
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
        "data": {"custom_id": component_id("veto", proposal_id)},
    }
    _, response = await signed_receive(service, key, payload)
    assert response["type"] == 4
    assert not tasks.interactions


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
