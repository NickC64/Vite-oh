import pytest

from viteoh.workspace_security import (
    InvalidToken,
    SignedTokenCodec,
    new_csrf_token,
)


def test_signed_tokens_enforce_signature_purpose_and_expiry() -> None:
    codec = SignedTokenCodec("secret")
    token = codec.encode("session", {"user_id": "42"}, ttl_seconds=60, now=100)
    assert codec.decode(token, "session", now=120)["user_id"] == "42"
    with pytest.raises(InvalidToken):
        codec.decode(token + "x", "session", now=120)
    with pytest.raises(InvalidToken):
        codec.decode(token, "launch", now=120)
    with pytest.raises(InvalidToken, match="expired"):
        codec.decode(token, "session", now=160)
    with pytest.raises(InvalidToken):
        codec.decode("not-a-token", "session")
    with pytest.raises(InvalidToken):
        codec.decode("💥.also-bad", "session")


def test_token_fingerprints_and_csrf_values_are_stable_or_random() -> None:
    codec = SignedTokenCodec("secret")
    assert codec.fingerprint("user") == codec.fingerprint("user")
    assert codec.fingerprint("user") != codec.fingerprint("other")
    assert new_csrf_token() != new_csrf_token()
    with pytest.raises(InvalidToken, match="not configured"):
        SignedTokenCodec("").fingerprint("user")
