"""Thin git CLI wrapper for the backup repo (GHB-1).

Deliberately offers only append-style operations. There is NO pull, force,
rebase, or amend function — the backup repo is an append-only log
(ADR-0003, GATES G3). test_backup_git.py::test_c7 enforces this absence.
"""

import subprocess
from pathlib import Path

_TIMEOUT = 60


class GitError(Exception):
    pass


class GitPushError(GitError):
    pass


def _run(repo, args: list[str], timeout: int = _TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=timeout,
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


def add_commit(repo, message: str) -> str | None:
    """Stage everything and commit. Returns new HEAD, or None if nothing to commit."""
    _run(repo, ["add", "-A"])
    result = _run(repo, ["commit", "-m", message])
    if result.returncode != 0:
        if "nothing to commit" in (result.stdout + result.stderr):
            return None
        raise GitError(f"commit failed: {result.stderr.strip()}")
    return head_commit(repo)


def push(repo, remote: str = "origin") -> None:
    # Push the current branch explicitly so behaviour never depends on
    # push.default or a configured upstream. -u records the upstream.
    branch = current_branch(repo)
    result = _run(repo, ["push", "-u", remote, branch])
    if result.returncode != 0:
        raise GitPushError(result.stderr.strip() or "git push failed")


def has_remote(repo, name: str = "origin") -> bool:
    result = _run(repo, ["remote"])
    return name in result.stdout.split()
