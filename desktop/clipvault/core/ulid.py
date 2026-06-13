"""Minimal ULID implementation (no external dependency).

48-bit millisecond timestamp + 80 random bits, Crockford base32, 26 chars.
Lexicographic order follows creation time. Both parts injectable for tests.
"""

import secrets
import time

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new(timestamp_ms: int | None = None, randomness: bytes | None = None) -> str:
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if not 0 <= ts < (1 << 48):
        raise ValueError("timestamp out of ULID range")
    rnd = secrets.token_bytes(10) if randomness is None else randomness
    if len(rnd) != 10:
        raise ValueError("randomness must be 10 bytes")
    value = (ts << 80) | int.from_bytes(rnd, "big")
    return "".join(_B32[(value >> (125 - 5 * i)) & 31] for i in range(26))
