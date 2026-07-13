"""JSONL serialization and daily-file layout (GHB-1, CONTRACTS §7)."""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from clipvault.core.models import Clip


_DAILY_RELPATH_RE = re.compile(
    r"^clips/(?P<year>[0-9]{4})/(?P<month>0[1-9]|1[0-2])/"
    r"(?P<date>(?P=year)-(?P=month)-(?:0[1-9]|[12][0-9]|3[01]))\.jsonl$"
)


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


def validated_daily_relpath(relpath: str) -> str:
    """Return a canonical backup path or reject it without echoing input."""

    if not isinstance(relpath, str):
        raise ValueError("invalid backup path")
    match = _DAILY_RELPATH_RE.fullmatch(relpath)
    if match is None:
        raise ValueError("invalid backup path")
    try:
        datetime.strptime(match.group("date"), "%Y-%m-%d")
    except ValueError:
        raise ValueError("invalid backup path") from None
    return relpath


def daily_relpath(iso_ts: str) -> str:
    # 2026-06-13T01:50:22Z -> clips/2026/06/2026-06-13.jsonl
    if not isinstance(iso_ts, str):
        raise ValueError("invalid backup timestamp")
    try:
        timestamp = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ValueError("invalid backup timestamp") from None
    canonical_timestamp = timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical_timestamp != iso_ts:
        raise ValueError("invalid backup timestamp")
    date = canonical_timestamp[:10]
    return validated_daily_relpath(
        f"clips/{date[:4]}/{date[5:7]}/{date}.jsonl"
    )


def daily_target_path(repo_path, relpath: str) -> Path:
    """Resolve a daily JSONL target without following a nested symlink."""

    validated = validated_daily_relpath(relpath)
    root = Path(repo_path).resolve()
    lexical_target = root.joinpath(*validated.split("/"))
    resolved_target = lexical_target.resolve()
    try:
        resolved_target.relative_to(root)
    except ValueError:
        raise ValueError("backup path escapes repository") from None
    if resolved_target != lexical_target:
        raise ValueError("backup path uses a symbolic link")
    return lexical_target


def append_lines(repo_path, relpath: str, lines: list[str]) -> Path:
    target = daily_target_path(repo_path, relpath)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return target


def iter_jsonl(repo_path):
    """Yield every JSON line under clips/ in deterministic path order."""
    root = Path(repo_path).resolve()
    clips_dir = root / "clips"
    if clips_dir.is_symlink():
        raise ValueError("backup path uses a symbolic link")
    if not clips_dir.exists():
        return
    for path in sorted(clips_dir.rglob("*.jsonl")):
        relpath = path.relative_to(root).as_posix()
        target = daily_target_path(root, relpath)
        for line in target.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield line
