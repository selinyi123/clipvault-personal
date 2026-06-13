"""Device pairing (PAIR-1). A one-time numeric code (5-min TTL, single use) is
shown in the desktop Web UI; the device redeems it for a long-lived token. The
desktop stores only sha256(token).
"""

import hashlib
import secrets
import time


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Pairing:
    def __init__(self, ttl_seconds: int = 300, clock=time.monotonic):
        self._codes: dict[str, float] = {}  # code -> expiry (monotonic)
        self.ttl = ttl_seconds
        self._clock = clock

    def mint_code(self) -> str:
        code = f"{secrets.randbelow(10**8):08d}"
        self._codes[code] = self._clock() + self.ttl
        return code

    def _valid(self, code: str) -> bool:
        exp = self._codes.get(code)
        return exp is not None and self._clock() < exp

    def redeem(self, code: str) -> str | None:
        """Consume a valid code, returning a fresh token; None if invalid/expired."""
        # opportunistic expiry sweep
        now = self._clock()
        self._codes = {c: e for c, e in self._codes.items() if e > now}
        if not self._valid(code):
            return None
        del self._codes[code]  # single use
        return secrets.token_urlsafe(32)
