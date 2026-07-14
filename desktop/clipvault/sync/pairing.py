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
import threading
import time
from collections.abc import Callable


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
        self._lock = threading.Lock()
        # A valid code moves out of _codes while its token is being persisted.
        # The marker prevents a stale completion from consuming a later state.
        self._inflight: dict[str, tuple[float, object]] = {}

    def _sweep_codes_locked(self) -> None:
        now = self._clock()
        self._codes = {c: e for c, e in self._codes.items() if e > now}

    def mint_code(self) -> str:
        with self._lock:
            # Keep the dict bounded even if codes go unredeemed. Avoid an
            # extremely unlikely collision with either an active or currently
            # persisting code instead of silently replacing it.
            self._sweep_codes_locked()
            while True:
                code = f"{secrets.randbelow(10**8):08d}"
                if code not in self._codes and code not in self._inflight:
                    self._codes[code] = self._clock() + self.ttl
                    return code

    def _valid_locked(self, code: str) -> bool:
        exp = self._codes.get(code)
        return exp is not None and self._clock() < exp

    def _is_rate_limited_locked(self) -> bool:
        now = self._clock()
        self._failures = [t for t in self._failures if now - t < self._lockout]
        return len(self._failures) >= self._max_failures

    def is_rate_limited(self) -> bool:
        """True when recent redeem failures should pause new pairing attempts."""
        with self._lock:
            return self._is_rate_limited_locked()

    def redeem(
        self,
        code: str,
        *,
        persist_token: Callable[[str], None] | None = None,
    ) -> str | None:
        """Consume a valid code, returning a fresh token; None if invalid/expired
        or while rate-limited. If ``persist_token`` is supplied, the code is
        consumed only after that callback returns successfully. Exceptions,
        including commit failures, restore a still-unexpired code and propagate.
        Callers can check is_rate_limited() to report 429.
        """

        with self._lock:
            if self._is_rate_limited_locked():
                return None
            self._sweep_codes_locked()
            if code in self._inflight:
                # A concurrent request already proved this code. Do not let the
                # duplicate execute persistence or poison the bad-code window.
                return None
            if not self._valid_locked(code):
                self._failures.append(self._clock())
                return None
            expiry = self._codes.pop(code)
            marker = object()
            self._inflight[code] = (expiry, marker)

        persisted = False
        try:
            token = secrets.token_urlsafe(32)
            if persist_token is not None:
                persist_token(token)
            persisted = True
            return token
        finally:
            with self._lock:
                reservation = self._inflight.get(code)
                if reservation is not None and reservation[1] is marker:
                    del self._inflight[code]
                    if persisted:
                        self._failures.clear()
                    elif self._clock() < expiry:
                        self._codes[code] = expiry
