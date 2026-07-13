"""JSONL serialization and daily-file layout (GHB-1, CONTRACTS section 7)."""

from __future__ import annotations

import io
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Collection, Iterable, Iterator

from clipvault.core.models import Clip


_DAILY_RELPATH_RE = re.compile(
    r"^clips/(?P<year>[0-9]{4})/(?P<month>0[1-9]|1[0-2])/"
    r"(?P<date>(?P=year)-(?P=month)-(?:0[1-9]|[12][0-9]|3[01]))\.jsonl$"
)


class JsonlIntegrityError(ValueError):
    """A JSONL file cannot be safely read or extended."""


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
    # Preserve the local Owner audit across disaster recovery without changing
    # canonical bytes for legacy/non-released public rows. Sync deliberately
    # does not trust peer-supplied release authority; this is backup-only data.
    if not isinstance(clip.released, bool):
        raise JsonlIntegrityError("invalid release audit flag")
    if clip.released:
        if not isinstance(clip.released_at, str):
            raise JsonlIntegrityError("released clip is missing its audit timestamp")
        daily_relpath(clip.released_at)  # canonical UTC timestamp validation
        obj["released"] = True
        obj["released_at"] = clip.released_at
    elif clip.released_at is not None:
        raise JsonlIntegrityError("unreleased clip has release audit metadata")
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def deserialize_clip(line: str) -> Clip:
    obj = json.loads(line)
    released = obj.get("released", False)
    released_at = obj.get("released_at")
    if not isinstance(released, bool):
        raise JsonlIntegrityError("invalid release audit flag")
    if released:
        if not isinstance(released_at, str):
            raise JsonlIntegrityError("released clip is missing its audit timestamp")
        daily_relpath(released_at)
    elif released_at is not None:
        raise JsonlIntegrityError("unreleased clip has release audit metadata")
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
        released=released,
        released_at=released_at,
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


def _reject_json_constant(_value: str):
    raise ValueError("non-standard JSON constant")


def _parse_line(line: str):
    if not isinstance(line, str) or not line or "\n" in line or "\r" in line:
        raise JsonlIntegrityError("invalid JSONL record")
    try:
        return json.loads(line, parse_constant=_reject_json_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise JsonlIntegrityError("invalid JSONL record") from None


def _clip_id(obj) -> str:
    if not isinstance(obj, dict):
        raise JsonlIntegrityError("invalid JSONL clip record")
    clip_id = obj.get("id")
    if not isinstance(clip_id, str) or not clip_id:
        raise JsonlIntegrityError("invalid JSONL clip record")
    return clip_id


def _stat_signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
    )


def _private_regular_info(path: Path) -> os.stat_result:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise JsonlIntegrityError("backup target is not a private regular file")
    return info


@contextmanager
def _open_private_regular(path: Path):
    """Open one exact private inode without reading through links or swaps."""

    before = _private_regular_info(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise JsonlIntegrityError("backup target safety check failed") from None
    stream = None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _stat_signature(opened) != _stat_signature(before)
        ):
            raise JsonlIntegrityError("backup target changed during open")
        stream = os.fdopen(descriptor, "rb")
        descriptor = -1
        yield stream
        after = os.fstat(stream.fileno())
        try:
            current = _private_regular_info(path)
        except FileNotFoundError:
            raise JsonlIntegrityError("backup target changed during read") from None
        if (
            _stat_signature(after) != _stat_signature(opened)
            or _stat_signature(current) != _stat_signature(opened)
        ):
            raise JsonlIntegrityError("backup target changed during read")
    finally:
        if stream is not None:
            stream.close()
        elif descriptor >= 0:
            os.close(descriptor)


def _iter_validated_records(path: Path) -> Iterator[tuple[bytes, str, object]]:
    """Stream raw records while enforcing strict UTF-8, JSON and LF framing."""

    with _open_private_regular(path) as fh:
        for raw_line in fh:
            if not raw_line.endswith(b"\n"):
                raise JsonlIntegrityError("incomplete JSONL record")
            payload = raw_line[:-1]
            # Treat CRLF as a complete legacy line ending. New writes remain
            # canonical LF, while the raw legacy bytes are preserved verbatim.
            if payload.endswith(b"\r"):
                payload = payload[:-1]
            try:
                line = payload.decode("utf-8")
            except UnicodeDecodeError:
                raise JsonlIntegrityError("invalid JSONL encoding") from None
            yield raw_line, line, _parse_line(line)


def _iter_validated_bytes(data: bytes) -> Iterator[tuple[bytes, str, object]]:
    """Validate in-memory JSONL with the same framing rules as repository files."""

    if not isinstance(data, bytes):
        raise TypeError("JSONL content must be bytes")
    with io.BytesIO(data) as fh:
        for raw_line in fh:
            if not raw_line.endswith(b"\n"):
                raise JsonlIntegrityError("incomplete JSONL record")
            payload = raw_line[:-1]
            if payload.endswith(b"\r"):
                payload = payload[:-1]
            try:
                line = payload.decode("utf-8")
            except UnicodeDecodeError:
                raise JsonlIntegrityError("invalid JSONL encoding") from None
            yield raw_line, line, _parse_line(line)


def latest_clip_lines_bytes(data: bytes, clip_ids: Collection[str]) -> dict[str, str]:
    """Return each requested clip's latest exact line from validated JSONL bytes."""

    requested = set(clip_ids)
    if any(not isinstance(clip_id, str) or not clip_id for clip_id in requested):
        raise ValueError("invalid clip id")
    latest: dict[str, str] = {}
    for _raw_line, line, obj in _iter_validated_bytes(data):
        clip_id = _clip_id(obj)
        if clip_id in requested:
            latest[clip_id] = line
    return latest


def append_latest_clip_states_bytes(data: bytes, lines: Iterable[str]) -> bytes:
    """Build validated append-only bytes, omitting repeated latest clip states."""

    desired: list[tuple[str, str]] = []
    tracked_ids: set[str] = set()
    for line in lines:
        clip_id = _clip_id(_parse_line(line))
        desired.append((clip_id, line))
        tracked_ids.add(clip_id)

    latest = latest_clip_lines_bytes(data, tracked_ids)
    pending: list[bytes] = []
    for clip_id, line in desired:
        if latest.get(clip_id) != line:
            pending.append((line + "\n").encode("utf-8"))
            latest[clip_id] = line
    return data + b"".join(pending)


def _prepare_target(repo_path, relpath: str) -> Path:
    target = daily_target_path(repo_path, relpath)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Re-resolve after creating the hierarchy so an existing nested link cannot
    # become trusted merely because the initial lexical path did not exist.
    checked = daily_target_path(repo_path, relpath)
    if checked != target:
        raise ValueError("backup path changed during validation")
    return checked


def _fsync_directory(path: Path) -> None:
    """Best-effort directory durability (opening directories fails on Windows)."""

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(path, flags)
    except (NotImplementedError, OSError):
        return
    try:
        os.fsync(fd)
    except (NotImplementedError, OSError):
        pass
    finally:
        os.close(fd)


def _atomic_append(repo_path, relpath: str, lines: Iterable[str]) -> Path:
    """Append validated records through an fsynced same-directory replacement."""

    target = _prepare_target(repo_path, relpath)
    encoded = [(line + "\n").encode("utf-8") for line in lines]
    existing_mode = None
    expected_signature = None
    try:
        existing_info = _private_regular_info(target)
    except FileNotFoundError:
        pass
    else:
        existing_mode = stat.S_IMODE(existing_info.st_mode)
        expected_signature = _stat_signature(existing_info)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            if expected_signature is not None:
                for raw_line, _line, _obj in _iter_validated_records(target):
                    out.write(raw_line)
            for raw_line in encoded:
                out.write(raw_line)
            out.flush()
            os.fsync(out.fileno())
        if existing_mode is not None:
            os.chmod(temp_path, existing_mode)
        # Fail closed if the path became a link while the replacement was built.
        if daily_target_path(repo_path, relpath) != target:
            raise ValueError("backup path changed during write")
        try:
            current_signature = _stat_signature(_private_regular_info(target))
        except FileNotFoundError:
            current_signature = None
        if current_signature != expected_signature:
            raise JsonlIntegrityError("backup target changed during write")
        os.replace(temp_path, target)
        _fsync_directory(target.parent)
    except BaseException:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return target


def replace_file_contents(repo_path, relpath: str, data: bytes | None) -> Path:
    """Atomically install or remove one validated managed JSONL file.

    This is intentionally narrower than a general file API. It exists for
    rebuilding an unpublished local backup candidate from its already-published
    base without carrying a newly classified secret forward in the worktree.
    """

    target = _prepare_target(repo_path, relpath)
    if data is None:
        try:
            info = target.lstat()
        except FileNotFoundError:
            return target
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("backup target is not a regular private file")
        target.unlink()
        _fsync_directory(target.parent)
        return target

    # Exhaust validation before changing the filesystem. Empty bytes are not a
    # valid managed file; callers remove absent paths with ``data=None``.
    if not data:
        raise JsonlIntegrityError("empty JSONL file")
    for _record in _iter_validated_bytes(data):
        pass

    existing_mode = None
    try:
        info = target.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("backup target is not a regular private file")
        existing_mode = stat.S_IMODE(info.st_mode)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if existing_mode is not None:
            os.chmod(temp_path, existing_mode)
        if daily_target_path(repo_path, relpath) != target:
            raise ValueError("backup path changed during write")
        os.replace(temp_path, target)
        _fsync_directory(target.parent)
    except BaseException:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return target


def append_lines(repo_path, relpath: str, lines: list[str]) -> Path:
    """Atomically append JSON records, retaining the historical public API."""

    validated_lines = []
    for line in lines:
        _parse_line(line)
        validated_lines.append(line)
    return _atomic_append(repo_path, relpath, validated_lines)


def append_latest_clip_states(repo_path, relpath: str, lines: list[str]) -> Path:
    """Atomically append only state changes relative to each clip's latest line.

    The comparison is deliberately against the most recent record for the same
    clip ID, not every line ever seen. Consequently an ``A -> B -> A`` state
    sequence records all three transitions while a crash retry of the current
    state is a no-op.
    """

    desired: list[tuple[str, str]] = []
    tracked_ids: set[str] = set()
    for line in lines:
        clip_id = _clip_id(_parse_line(line))
        desired.append((clip_id, line))
        tracked_ids.add(clip_id)

    target = _prepare_target(repo_path, relpath)
    latest: dict[str, str] = {}
    try:
        for _raw_line, line, obj in _iter_validated_records(target):
            clip_id = _clip_id(obj)
            if clip_id in tracked_ids:
                latest[clip_id] = line
    except FileNotFoundError:
        pass

    pending: list[str] = []
    for clip_id, line in desired:
        if latest.get(clip_id) != line:
            pending.append(line)
            latest[clip_id] = line

    if not pending:
        return target
    return _atomic_append(repo_path, relpath, pending)


def iter_jsonl(repo_path):
    """Yield every validated JSON line in deterministic path order."""
    root = Path(repo_path).resolve()
    clips_dir = root / "clips"
    if clips_dir.is_symlink():
        raise ValueError("backup path uses a symbolic link")
    if not clips_dir.exists():
        return
    for path in sorted(clips_dir.rglob("*.jsonl")):
        relpath = path.relative_to(root).as_posix()
        target = daily_target_path(root, relpath)
        for _raw_line, line, _obj in _iter_validated_records(target):
            yield line
