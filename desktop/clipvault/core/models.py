"""Core data models (CONTRACTS §1)."""

from dataclasses import dataclass, field

CONTENT_TYPES = ("text", "url", "path", "command", "code", "error_log", "prompt")

SECRET_LEVEL_HARD = "hard"
SECRET_LEVEL_SUSPECT = "suspect"


@dataclass
class SecretVerdict:
    is_secret: bool
    level: str | None  # "hard" | "suspect" | None
    reasons: list[str] = field(default_factory=list)


@dataclass
class Clip:
    id: str
    content: str
    content_hash: str
    content_type: str
    source_device: str
    created_at: str  # UTC ISO8601, e.g. 2026-06-12T08:30:00Z
    last_seen_at: str
    is_secret: bool = False
    secret_level: str | None = None
    secret_reasons: list[str] = field(default_factory=list)
    released: bool = False
    released_at: str | None = None
    source_app: str | None = None
    times_seen: int = 1
    pinned: bool = False
    favorite: bool = False
    deleted: bool = False
    obsidian_path: str | None = None
    backed_up_at: str | None = None
