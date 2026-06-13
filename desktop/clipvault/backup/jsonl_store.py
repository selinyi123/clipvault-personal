"""JSONL serialization and daily-file layout (GHB-1, CONTRACTS §7)."""

import json
import os
from pathlib import Path

from clipvault.core.models import Clip


def serialize_clip(clip: Clip) -> str:
    """Single-line JSON with a stable key order = stable diffs in the repo."""
    obj = {
        "id": clip.id,
        "content": clip.content,
        "content_hash": clip.content_hash,
        "content_type": clip.content_type,
        "is_secret": clip.is_secret,
        "secret_level": clip.secret_level,
        "secret_reasons": clip.secret_reasons,
        "source_device": clip.source_device,
        "source_app": clip.source_app,
        "created_at": clip.created_at,
        "last_seen_at": clip.last_seen_at,
        "times_seen": clip.times_seen,
        "pinned": clip.pinned,
        "favorite": clip.favorite,
        "deleted": clip.deleted,
    }
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def deserialize_clip(line: str) -> Clip:
    obj = json.loads(line)
    return Clip(
        id=obj["id"],
        content=obj["content"],
        content_hash=obj["content_hash"],
        content_type=obj["content_type"],
        is_secret=obj.get("is_secret", False),
        secret_level=obj.get("secret_level"),
        secret_reasons=obj.get("secret_reasons", []),
        source_device=obj["source_device"],
        source_app=obj.get("source_app"),
        created_at=obj["created_at"],
        last_seen_at=obj["last_seen_at"],
        times_seen=obj.get("times_seen", 1),
        pinned=obj.get("pinned", False),
        favorite=obj.get("favorite", False),
        deleted=obj.get("deleted", False),
    )


def daily_relpath(iso_ts: str) -> str:
    # 2026-06-13T01:50:22Z -> clips/2026/06/2026-06-13.jsonl
    date = iso_ts[:10]
    return f"clips/{date[:4]}/{date[5:7]}/{date}.jsonl"


def append_lines(repo_path, relpath: str, lines: list[str]) -> Path:
    target = Path(repo_path) / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return target


def iter_jsonl(repo_path):
    """Yield every JSON line under clips/ in deterministic path order."""
    clips_dir = Path(repo_path) / "clips"
    if not clips_dir.exists():
        return
    for path in sorted(clips_dir.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield line
