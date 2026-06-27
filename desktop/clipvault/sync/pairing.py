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
    """One-time pairing-code minting + redemption.

    A short numeric code is brute-forceable on its own (10^8 space), so
    redemption is rate-limited: after `max_attempts` bad codes inside a sliding
    `attempt_window_seconds`, all redemptions are refused until the window
    drains. Combined with the 5-minute TTL and single use, this keeps the LAN
    surface non-enumerable without lengthening the user-typed code (PAIR-1).
    """

    def __init__(self, ttl_seconds: int = 300, clock=time.monotonic,
                 max_attempts: int = 5, attempt_window_seconds: float = 60.0):
        self._codes: dict[str, float] = {}  # code -> expiry (monotonic)
        self.ttl = ttl_seconds
        self._clock = clock
        self._max_attempts = max_attempts
        self._attempt_window = attempt_window_seconds
        self._failures: list[float] = []  # monotonic timestamps of recent bad codes

    def mint_code(self) -> str:
        code = f"{secrets.randbelow(10**8):08d}"
        self._codes[code] = self._clock() + self.ttl
        return code

    def _valid(self, code: str) -> bool:
        exp = self._codes.get(code)
        return exp is not None and self._clock() < exp

    def _recent_failures(self, now: float) -> int:
        cutoff = now - self._attempt_window
        self._failures = [t for t in self._failures if t > cutoff]
        return len(self._failures)

    def locked(self) -> bool:
        """True while too many recent bad codes block further redemption."""
        return self._recent_failures(self._clock()) >= self._max_attempts

    def redeem(self, code: str) -> str | None:
        """Consume a valid code, returning a fresh token; None if invalid,
        expired, or currently rate-limited."""
        now = self._clock()
        # opportunistic expiry sweep
        self._codes = {c: e for c, e in self._codes.items() if e > now}
        # Brute-force guard: refuse (even a valid code) while locked, and do not
        # record further failures so the window can drain to a steady cap of
        # max_attempts tries per window.
        if self._recent_failures(now) >= self._max_attempts:
            return None
        if not self._valid(code):
            self._failures.append(now)
            return None
        del self._codes[code]  # single use
        self._failures.clear()  # a successful pairing resets the counter
        return secrets.token_urlsafe(32)
