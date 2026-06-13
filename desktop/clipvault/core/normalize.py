"""NORM-1: content normalization and hashing (CONTRACTS §2).

Order is contractual: newline folding -> NFC -> strip end-of-string
whitespace. Inner-line trailing whitespace and all leading whitespace are
content and must be preserved.
"""

import hashlib
import unicodedata

REJECT_EMPTY = "empty"
REJECT_TOO_LARGE = "too_large"

DEFAULT_MAX_CLIP_BYTES = 1_048_576  # 1 MiB


def normalize(text: str) -> str:
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    s = unicodedata.normalize("NFC", s)
    return s.rstrip()


def reject_reason(normalized: str, max_bytes: int = DEFAULT_MAX_CLIP_BYTES) -> str | None:
    """Return a rejection reason or None if the content is acceptable."""
    if normalized == "":
        return REJECT_EMPTY
    if len(normalized.encode("utf-8")) > max_bytes:
        return REJECT_TOO_LARGE
    return None


def content_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
