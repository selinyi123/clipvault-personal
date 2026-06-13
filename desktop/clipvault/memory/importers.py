"""Memory importers. Pure functions over injectable data (zero network) plus
a thin apply() that upserts via MemoryRepo. Re-running is idempotent.
"""

import re
from pathlib import Path

from clipvault.store.memory_repo import MemoryRepo

# OBS-1 filenames look like 20260613-095022_slug_ABC123.md
_OBS_PREFIX = re.compile(r"^\d{8}-\d{6}_")
_OBS_SUFFIX = re.compile(r"_[0-9A-Za-z]{6}$")


def _title_from_filename(name: str) -> str:
    stem = name[:-3] if name.endswith(".md") else name
    stem = _OBS_PREFIX.sub("", stem)
    stem = _OBS_SUFFIX.sub("", stem)
    return stem.replace("-", " ").strip()


def from_obsidian_titles(vault_path) -> list[tuple[str, str]]:
    """Scan a vault for note titles → (kind='path', title) candidates."""
    root = Path(vault_path)
    if not root.exists():
        return []
    seen = set()
    out: list[tuple[str, str]] = []
    for md in sorted(root.rglob("*.md")):
        title = _title_from_filename(md.name)
        if title and title.lower() not in seen:
            seen.add(title.lower())
            out.append(("path", title))
    return out


def from_names(names, kind: str = "term") -> list[tuple[str, str]]:
    """Turn a list of names (e.g. GitHub repo names) into memory candidates."""
    seen = set()
    out: list[tuple[str, str]] = []
    for n in names:
        n = (n or "").strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append((kind, n))
    return out


def apply(repo: MemoryRepo, items: list[tuple[str, str]], source: str) -> int:
    """Upsert candidates; return count of newly created items.
    Newly-created items are published to peers (S008)."""
    from datetime import datetime, timezone

    from clipvault.sync import engine

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = 0
    for kind, text in items:
        before = repo.by_kind_text(kind, text)
        item = repo.upsert(kind, text, source=source)
        if before is None:
            created += 1
            engine.emit_memory_upsert(repo.conn, item, now)
    return created
