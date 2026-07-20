import time

from nacl.signing import SigningKey

from viteoh.components import component_id, parse_component_id
from viteoh.security import SignatureVerifier


def test_signature_verification_and_expiry() -> None:
    key = SigningKey.generate()
    timestamp = str(int(time.time()))
    body = b'{"type":1}'
    signature = key.sign(timestamp.encode() + body).signature.hex()
    verifier = SignatureVerifier(key.verify_key.encode().hex(), 300)

    assert verifier.verify(body, signature, timestamp)
    assert not verifier.verify(body + b" ", signature, timestamp)
    assert not verifier.verify(body, None, timestamp)
    assert not verifier.verify(body, signature, timestamp, now=float(timestamp) + 301)


def test_component_round_trip_and_rejection() -> None:
    proposal_id = "12345678-1234-1234-1234-123456789abc"
    value = component_id("confirm-veto", proposal_id)
    assert parse_component_id(value) == ("confirm-veto", proposal_id)
    assert parse_component_id("veto") is None
