import time

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


class SignatureVerifier:
    def __init__(self, public_key: str, max_age_seconds: int = 300) -> None:
        self.verify_key = VerifyKey(bytes.fromhex(public_key))
        self.max_age_seconds = max_age_seconds

    def verify(
        self,
        body: bytes,
        signature: str | None,
        timestamp: str | None,
        *,
        now: float | None = None,
    ) -> bool:
        if not signature or not timestamp:
            return False
        try:
            signed_at = int(timestamp)
            current = time.time() if now is None else now
            if abs(current - signed_at) > self.max_age_seconds:
                return False
            self.verify_key.verify(timestamp.encode() + body, bytes.fromhex(signature))
            return True
        except (BadSignatureError, ValueError, TypeError):
            return False
