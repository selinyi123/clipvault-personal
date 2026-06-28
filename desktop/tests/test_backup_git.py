"""C7: the git wrapper must not expose history-rewriting operations.
Plus: a hung git op (timeout) must surface as a normal git error, not an
uncaught TimeoutExpired that crashes the backup worker."""

import inspect
import subprocess

import pytest

from clipvault.backup import git_repo

FORBIDDEN = {"pull", "force", "rebase", "amend", "reset", "fetch"}


def test_c7_no_history_rewriting_functions():
    public = {
        name for name, obj in inspect.getmembers(git_repo, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public.isdisjoint(FORBIDDEN), f"forbidden git ops present: {public & FORBIDDEN}"


def test_c7_source_has_no_force_or_pull():
    source = inspect.getsource(git_repo)
    # The wrapper builds git argv lists; none may contain these tokens.
    for token in ('"pull"', '"--force"', '"-f"', '"rebase"', '"--amend"', '"reset"'):
        assert token not in source, f"git_repo must not reference {token}"


def _timeout(*args, **kwargs):
    raise subprocess.TimeoutExpired(kwargs.get("args") or args[0], kwargs.get("timeout", 60))


def test_push_timeout_raises_gitpush_error_not_timeout(monkeypatch):
    # A hung push must become a GitPushError so BackupWorker backs off instead of
    # the worker thread dying on an uncaught subprocess.TimeoutExpired.
    monkeypatch.setattr(subprocess, "run", _timeout)
    with pytest.raises(git_repo.GitPushError):
        git_repo.push("/tmp/whatever")


def test_commit_timeout_raises_git_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _timeout)
    with pytest.raises(git_repo.GitError):
        git_repo.add_commit("/tmp/whatever", "msg")
