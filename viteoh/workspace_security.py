import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any


class InvalidToken(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SignedTokenCodec:
    secret: str

    def encode(
        self,
        purpose: str,
        claims: dict[str, Any],
        *,
        ttl_seconds: int,
        now: int | None = None,
    ) -> str:
        issued_at = int(time.time()) if now is None else now
        payload = {
            "purpose": purpose,
            "iat": issued_at,
            "exp": issued_at + ttl_seconds,
            "nonce": secrets.token_urlsafe(16),
            **claims,
        }
        encoded = _b64(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        )
        signature = _b64(hmac.new(self._key, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def decode(
        self,
        token: str,
        purpose: str,
        *,
        now: int | None = None,
    ) -> dict[str, Any]:
        try:
            encoded, supplied_signature = token.split(".", 1)
            expected_signature = _b64(
                hmac.new(self._key, encoded.encode(), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise InvalidToken("The link is invalid.")
            payload = json.loads(_unb64(encoded))
        except (
            ValueError,
            binascii.Error,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
            raise InvalidToken("The link is invalid.") from exc
        if not isinstance(payload, dict):
            raise InvalidToken("The link is invalid.")
        current = int(time.time()) if now is None else now
        if payload.get("purpose") != purpose:
            raise InvalidToken("The link is invalid.")
        try:
            expires_at = int(payload.get("exp", 0))
            issued_at = int(payload.get("iat", current + 1))
        except (TypeError, ValueError) as exc:
            raise InvalidToken("The link is invalid.") from exc
        if expires_at <= current:
            raise InvalidToken("The link has expired.")
        if issued_at > current + 30:
            raise InvalidToken("The link is invalid.")
        return payload

    def fingerprint(self, value: str) -> str:
        return hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()

    @property
    def _key(self) -> bytes:
        if not self.secret:
            raise InvalidToken("The workspace is not configured.")
        return self.secret.encode()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _unb64(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode()
