"""Device pairing (PAIR-1). A one-time numeric code (5-min TTL, single use) is
shown in the desktop Web UI; the device redeems it for a long-lived token. The
desktop stores only sha256(token).

/api/pair is reachable from the LAN, so redeem() is rate-limited: after
`max_failures` bad attempts within `lockout_seconds`, further attempts are
refused for the rest of that window. This bounds brute-force of the 8-digit code
and stops a flood from monopolising the single-threaded HTTP server. The lockout
is global and short by design. Failures are treated as consecutive attempts:
a successful code redemption resets the short failure window.
"""

import hashlib
import secrets
import time


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Pairing:
    def __init__(self, ttl_seconds: int = 300, clock=time.monotonic,
                 max_failures: int = 10, lockout_seconds: int = 60):
        self._codes: dict[str, float] = {}  # code -> expiry (monotonic)
        self.ttl = ttl_seconds
        self._clock = clock
        self._max_failures = max_failures
        self._lockout = lockout_seconds
        self._failures: list[float] = []  # monotonic times of recent bad attempts

    def _sweep_codes(self) -> None:
        now = self._clock()
        self._codes = {c: e for c, e in self._codes.items() if e > now}

    def mint_code(self) -> str:
        self._sweep_codes()  # keep the dict bounded even if codes go unredeemed
        code = f"{secrets.randbelow(10**8):08d}"
        self._codes[code] = self._clock() + self.ttl
        return code

    def _valid(self, code: str) -> bool:
        exp = self._codes.get(code)
        return exp is not None and self._clock() < exp

    def is_rate_limited(self) -> bool:
        """True when recent redeem failures should pause new pairing attempts."""
        now = self._clock()
        self._failures = [t for t in self._failures if now - t < self._lockout]
        return len(self._failures) >= self._max_failures

    def redeem(self, code: str) -> str | None:
        """Consume a valid code, returning a fresh token; None if invalid/expired
        or while rate-limited. Callers can check is_rate_limited() to report 429."""
        if self.is_rate_limited():
            return None
        self._sweep_codes()
        if not self._valid(code):
            self._failures.append(self._clock())
            return None
        del self._codes[code]  # single use
        self._failures.clear()
        return secrets.token_urlsafe(32)
