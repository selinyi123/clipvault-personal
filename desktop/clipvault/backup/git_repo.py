"""Thin git CLI wrapper for the backup repo (GHB-1).

Deliberately offers only append-style operations. There is NO pull, force,
rebase, or amend function — the backup repo is an append-only log
(ADR-0003, GATES G3). test_backup_git.py::test_c7 enforces this absence.
"""

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from clipvault.backup import jsonl_store

_TIMEOUT = 60
_DISABLED_HOOKS_PATH = "NUL" if os.name == "nt" else "/dev/null"


class GitError(Exception):
    def __init__(self, message: str, returncode: int | None = None) -> None:
        self.returncode = returncode
        super().__init__(message)


class GitPushError(GitError):
    pass


def _validated_backup_paths(paths: Sequence[str]) -> list[str]:
    """Return unique pathspec-safe JSONL paths or fail without echoing input."""

    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)):
        raise GitError("backup path validation failed")
    validated: set[str] = set()
    for path in paths:
        try:
            path = jsonl_store.validated_daily_relpath(path)
        except (TypeError, ValueError):
            raise GitError("backup path validation failed") from None
        validated.add(path)
    if not validated:
        raise GitError("backup paths required")
    return sorted(validated)


def _run(repo, args: list[str], timeout: int = _TIMEOUT) -> subprocess.CompletedProcess:
    # Backup is an unattended data path. Repository/global hooks must not turn
    # a misconfigured worktree into arbitrary side effects during add/commit/push.
    cmd = [
        "git",
        "-c",
        f"core.hooksPath={_DISABLED_HOOKS_PATH}",
        "-C",
        str(repo),
        *args,
    ]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # A hung git op (most likely a network push) must surface as a normal
        # non-zero result so callers handle it through their existing returncode
        # paths — push() -> GitPushError (backoff), add_commit() -> GitError
        # (queue stays pending, retried) — instead of an uncaught TimeoutExpired
        # crashing the backup worker thread.
        return subprocess.CompletedProcess(
            cmd, returncode=124, stdout="", stderr=f"git timed out after {timeout}s",
        )


def init(repo) -> None:
    # -b main: create the initial branch deterministically so the first push
    # always has a named branch (avoids unborn/detached-HEAD edge cases).
    Path(repo).mkdir(parents=True, exist_ok=True)
    result = _run(repo, ["init", "-b", "main"])
    if result.returncode != 0:  # older git without -b
        _run(repo, ["init"])
        _run(repo, ["checkout", "-b", "main"])


def is_clean(repo) -> bool:
    result = _run(repo, ["status", "--porcelain"])
    return result.returncode == 0 and result.stdout.strip() == ""


def head_commit(repo) -> str | None:
    result = _run(repo, ["rev-parse", "HEAD"])
    return result.stdout.strip() if result.returncode == 0 else None


def current_branch(repo) -> str:
    result = _run(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    return result.stdout.strip()


def _assert_backup_only_history(repo) -> None:
    """Refuse to push a branch whose existing history ever tracked other files."""

    head_result = _run(repo, ["rev-parse", "--verify", "-q", "HEAD^{commit}"])
    if head_result.returncode == 1:
        return
    head = head_result.stdout.strip()
    if head_result.returncode != 0 or not head:
        raise GitError("backup history head inspection failed", head_result.returncode)
    result = _run(
        repo,
        [
            "log",
            "--diff-merges=separate",
            "--format=",
            "--name-only",
            "-z",
            head,
        ],
    )
    if result.returncode != 0:
        raise GitError("backup history inspection failed", result.returncode)
    paths = [path for path in result.stdout.split("\0") if path]
    if paths:
        _validated_backup_paths(paths)


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _resolved_push_target(repo, remote: str) -> str:
    """Resolve one configured remote to one URL, bypassing remote push config."""

    if not isinstance(remote, str) or not remote or _has_control_chars(remote):
        raise GitPushError("backup remote validation failed")
    remotes = _run(repo, ["remote"])
    if remotes.returncode != 0:
        raise GitPushError("backup remote inspection failed", remotes.returncode)
    if remote not in remotes.stdout.splitlines():
        return remote
    if remote.startswith("-"):
        raise GitPushError("backup remote validation failed")
    resolved = _run(repo, ["remote", "get-url", "--push", "--all", remote])
    if resolved.returncode != 0:
        raise GitPushError("backup remote resolution failed", resolved.returncode)
    targets = [target for target in resolved.stdout.splitlines() if target]
    if len(targets) != 1 or _has_control_chars(targets[0]):
        raise GitPushError("backup remote resolution failed")
    return targets[0]


def add_commit(
    repo,
    message: str,
    *,
    paths: Sequence[str],
) -> str | None:
    """Commit only validated JSONL paths; never consume unrelated index state."""

    validated = _validated_backup_paths(paths)
    _assert_backup_only_history(repo)
    result = _run(repo, ["add", "--", *validated])
    if result.returncode != 0:
        raise GitError("git add failed", result.returncode)
    staged = _run(repo, ["diff", "--cached", "--quiet", "--", *validated])
    if staged.returncode == 0:
        return None
    if staged.returncode != 1:
        raise GitError("git diff failed", staged.returncode)
    result = _run(
        repo,
        ["commit", "--no-verify", "--only", "-m", message, "--", *validated],
    )
    if result.returncode != 0:
        raise GitError("git commit failed", result.returncode)
    committed = head_commit(repo)
    if committed is None:
        raise GitError("git head lookup failed")
    return committed


def push(repo, remote: str = "origin") -> None:
    # Push the current branch explicitly so behaviour never depends on
    # push.default, a configured upstream, or remote-specific ref settings.
    try:
        _assert_backup_only_history(repo)
    except GitError as exc:
        raise GitPushError(
            "backup history validation failed",
            exc.returncode,
        ) from None
    branch_result = _run(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    branch = branch_result.stdout.strip()
    if branch_result.returncode != 0 or not branch:
        raise GitPushError("backup branch inspection failed", branch_result.returncode)
    branch_ref = f"refs/heads/{branch}"
    target = _resolved_push_target(repo, remote)
    result = _run(
        repo,
        [
            "push",
            "--no-all",
            "--no-mirror",
            "--no-tags",
            "--no-follow-tags",
            "--no-force",
            "--no-delete",
            "--no-prune",
            "--recurse-submodules=no",
            "--no-signed",
            "--no-push-option",
            "--no-verify",
            "--receive-pack=git-receive-pack",
            "--",
            target,
            f"{branch_ref}:{branch_ref}",
        ],
    )
    if result.returncode != 0:
        raise GitPushError("git push failed", result.returncode)


def has_remote(repo, name: str = "origin") -> bool:
    result = _run(repo, ["remote"])
    return name in result.stdout.split()
