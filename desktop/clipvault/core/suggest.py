"""SUG-1: deterministic suggestion scoring (CONTRACTS §11).

Pure logic — no IO (GATES G8). May use math/datetime only. Candidates are
built by the IO layer (handlers) from Personal Memory and recent high-use
clips; this module only scores and ranks them.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Candidate:
    id: str
    kind: str
    text: str
    label: str | None = None
    pinned: bool = False
    use_count: int = 0
    last_used_at: str | None = None
    source_app: str | None = None
    origin: str = "memory"  # "memory" | "clip"


@dataclass
class Weights:
    pinned: float = 3.0
    prefix: float = 1.5
    substr: float = 0.6
    freq: float = 1.0
    app: float = 0.5
    half_life_days: float = 14.0


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _match_score(c: Candidate, query: str, w: Weights) -> float | None:
    """None means 'drop this candidate' (query present but no match)."""
    if not query:
        return 0.0
    q = query.casefold()
    fields = [c.text.casefold()]
    if c.label:
        fields.append(c.label.casefold())
    if any(f.startswith(q) for f in fields):
        return w.prefix
    if any(q in f for f in fields):
        return w.substr
    return None


def score(c: Candidate, query: str, app: str | None, w: Weights,
          now: datetime | None = None) -> float | None:
    m = _match_score(c, query, w)
    if m is None:
        return None
    now = now or datetime.now(timezone.utc)

    last = _parse(c.last_used_at)
    if last is None:
        decay = 1.0
    else:
        days = max(0.0, (now - last).total_seconds() / 86400.0)
        decay = math.exp(-days / w.half_life_days)
    freq = w.freq * math.log1p(c.use_count) * decay

    pinned = w.pinned if c.pinned else 0.0
    app_bonus = w.app if (app and c.source_app == app) else 0.0
    return pinned + m + freq + app_bonus


def rank(candidates: list[Candidate], query: str, app: str | None, w: Weights,
         now: datetime | None = None, limit: int = 10) -> list[tuple[Candidate, float]]:
    now = now or datetime.now(timezone.utc)
    scored: list[tuple[Candidate, float]] = []
    for c in candidates:
        s = score(c, query, app, w, now)
        if s is not None:
            scored.append((c, s))
    # SUG-1.1: pinned is a hard top tier (PRODUCT_SPEC "pinned 永远置顶"),
    # then by score, then most-recently-used. Predictable ordering > raw score.
    scored.sort(
        key=lambda cs: (cs[0].pinned, cs[1], cs[0].last_used_at or ""),
        reverse=True,
    )
    return scored[:limit]
