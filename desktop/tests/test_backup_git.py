"""C7: backup Git operations are append-only, path-scoped, and fail closed.

A hung Git operation must surface as a normal Git error rather than an
uncaught ``TimeoutExpired``.  Backup commits and pushes must never carry files
outside the dated ``clips/**/*.jsonl`` layout, even when the configured backup
directory accidentally points at an existing repository.
"""

import inspect
import stat
import subprocess
from pathlib import Path

import pytest

from clipvault.backup import git_repo

FORBIDDEN = {"pull", "force", "rebase", "amend", "reset", "fetch"}
ALLOWED_PATH = "clips/2026/06/2026-06-13.jsonl"
SENSITIVE_DIAGNOSTIC = "PRIVATE-AKIAIOSFODNN7EXAMPLE-Alice-Vault"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _configured_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "backup"
    git_repo.init(repo)
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    return repo


def _write(repo: Path, relpath: str, content: str = "{}\n") -> Path:
    path = repo / Path(relpath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _install_hook(
    repo: Path,
    name: str,
    marker_name: str,
    *,
    exit_code: int,
    hooks_dir: Path | None = None,
) -> Path:
    hook = (hooks_dir or repo / ".git" / "hooks") / name
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' {name} >> {marker_name}\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
        newline="\n",
    )
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook


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


def test_add_timeout_raises_git_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _timeout)
    with pytest.raises(git_repo.GitError):
        git_repo.add_commit("/tmp/whatever", "msg", paths=[ALLOWED_PATH])


def test_add_failure_does_not_run_commit(monkeypatch):
    calls = []

    def fake_run(repo, args, timeout=git_repo._TIMEOUT):
        calls.append(args)
        if args == ["rev-parse", "--verify", "-q", "HEAD^{commit}"]:
            return subprocess.CompletedProcess(
                ["git", *args], returncode=1, stdout="", stderr="no commits",
            )
        if args and args[0] == "add":
            return subprocess.CompletedProcess(
                ["git", *args], returncode=1, stdout="", stderr=SENSITIVE_DIAGNOSTIC,
            )
        raise AssertionError(f"commit should not run after failed add: {args}")

    monkeypatch.setattr(git_repo, "_run", fake_run)

    with pytest.raises(git_repo.GitError) as caught:
        git_repo.add_commit("/tmp/whatever", "msg", paths=[ALLOWED_PATH])

    assert calls[-1][0] == "add"
    assert not any(args and args[0] == "commit" for args in calls)
    assert SENSITIVE_DIAGNOSTIC not in str(caught.value)


def test_commit_timeout_raises_git_error(monkeypatch):
    calls = []

    def fake_run(repo, args, timeout=git_repo._TIMEOUT):
        calls.append(args)
        if args == ["rev-parse", "--verify", "-q", "HEAD^{commit}"]:
            return subprocess.CompletedProcess(
                ["git", *args], returncode=1, stdout="", stderr="no commits",
            )
        if args and args[0] == "add":
            return subprocess.CompletedProcess(["git", *args], 0, "", "")
        if args and args[0] == "diff":
            return subprocess.CompletedProcess(["git", *args], 1, "", "")
        if args and args[0] == "commit":
            return subprocess.CompletedProcess(
                ["git", *args], 124, "", SENSITIVE_DIAGNOSTIC,
            )
        raise AssertionError(f"unexpected git operation: {args}")

    monkeypatch.setattr(git_repo, "_run", fake_run)

    with pytest.raises(git_repo.GitError) as caught:
        git_repo.add_commit("/tmp/whatever", "msg", paths=[ALLOWED_PATH])

    assert calls[-1][0] == "commit"
    assert SENSITIVE_DIAGNOSTIC not in str(caught.value)


@pytest.mark.parametrize("returncode", [2, 124])
def test_add_history_probe_failure_fails_closed_before_add(monkeypatch, returncode):
    calls = []

    def fake_run(repo, args, timeout=git_repo._TIMEOUT):
        calls.append(args)
        if args == ["rev-parse", "--verify", "-q", "HEAD^{commit}"]:
            return subprocess.CompletedProcess(
                ["git", *args], returncode, "", SENSITIVE_DIAGNOSTIC,
            )
        raise AssertionError(f"history probe failure must stop before Git mutation: {args}")

    monkeypatch.setattr(git_repo, "_run", fake_run)

    with pytest.raises(git_repo.GitError) as caught:
        git_repo.add_commit("/tmp/whatever", "msg", paths=[ALLOWED_PATH])

    assert calls == [["rev-parse", "--verify", "-q", "HEAD^{commit}"]]
    assert caught.value.returncode == returncode
    assert SENSITIVE_DIAGNOSTIC not in str(caught.value)


@pytest.mark.parametrize("returncode", [2, 124])
def test_push_history_probe_failure_is_safe_push_error(monkeypatch, returncode):
    calls = []

    def fake_run(repo, args, timeout=git_repo._TIMEOUT):
        calls.append(args)
        if args == ["rev-parse", "--verify", "-q", "HEAD^{commit}"]:
            return subprocess.CompletedProcess(
                ["git", *args], returncode, "", SENSITIVE_DIAGNOSTIC,
            )
        raise AssertionError(f"history probe failure must stop before push: {args}")

    monkeypatch.setattr(git_repo, "_run", fake_run)

    with pytest.raises(git_repo.GitPushError) as caught:
        git_repo.push("/tmp/whatever")

    assert calls == [["rev-parse", "--verify", "-q", "HEAD^{commit}"]]
    assert caught.value.returncode == returncode
    assert SENSITIVE_DIAGNOSTIC not in str(caught.value)


def test_push_uses_option_terminator_before_remote(monkeypatch):
    calls = []
    option_like_remote = "--upload-pack=PRIVATE-AKIAIOSFODNN7EXAMPLE"

    def fake_run(repo, args, timeout=git_repo._TIMEOUT):
        calls.append(args)
        if args == ["rev-parse", "--verify", "-q", "HEAD^{commit}"]:
            return subprocess.CompletedProcess(["git", *args], 1, "", "")
        if args == ["symbolic-ref", "--quiet", "--short", "HEAD"]:
            return subprocess.CompletedProcess(["git", *args], 0, "main\n", "")
        if args == ["remote"]:
            return subprocess.CompletedProcess(["git", *args], 0, "", "")
        if args and args[0] == "push":
            return subprocess.CompletedProcess(["git", *args], 0, "", "")
        raise AssertionError(f"unexpected git operation: {args}")

    monkeypatch.setattr(git_repo, "_run", fake_run)

    git_repo.push("/tmp/whatever", option_like_remote)

    push_args = calls[-1]
    separator = push_args.index("--")
    assert push_args[0] == "push"
    assert push_args[separator + 1:] == [
        option_like_remote,
        "refs/heads/main:refs/heads/main",
    ]


@pytest.mark.parametrize(
    "invalid_path",
    [
        "notes.txt",
        "clips/2026/06/2026-06-13.txt",
        "clips/2026/06/not-a-date.jsonl",
        "clips/2026/07/2026-06-13.jsonl",
        "clips/2025/06/2026-06-13.jsonl",
        "clips/2026/06/../../private.jsonl",
        "../private.jsonl",
        "clips\\2026\\06\\2026-06-13.jsonl",
    ],
)
def test_add_commit_rejects_paths_outside_dated_jsonl_layout(tmp_path, invalid_path):
    repo = _configured_repo(tmp_path)
    _write(repo, ALLOWED_PATH)

    with pytest.raises(git_repo.GitError):
        git_repo.add_commit(repo, "backup", paths=[invalid_path])

    assert git_repo.head_commit(repo) is None


def test_add_commit_rejects_absolute_path(tmp_path):
    repo = _configured_repo(tmp_path)
    outside = tmp_path / "private.jsonl"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(git_repo.GitError):
        git_repo.add_commit(repo, "backup", paths=[str(outside.resolve())])

    assert git_repo.head_commit(repo) is None


def test_add_commit_accepts_dated_jsonl_path(tmp_path):
    repo = _configured_repo(tmp_path)
    _write(repo, ALLOWED_PATH, '{"content":"public"}\n')

    commit = git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])

    assert commit == git_repo.head_commit(repo)
    assert _git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines() == [
        ALLOWED_PATH
    ]


def test_add_commit_preserves_unrelated_staged_and_untracked_files(tmp_path):
    repo = _configured_repo(tmp_path)
    _write(repo, ALLOWED_PATH, '{"content":"public"}\n')
    staged = _write(repo, "staged-private.txt", SENSITIVE_DIAGNOSTIC)
    untracked = _write(repo, "untracked-private.txt", SENSITIVE_DIAGNOSTIC)
    _git(repo, "add", "--", staged.name)

    git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])

    committed = _git(repo, "ls-tree", "-r", "--name-only", "HEAD")
    assert committed.stdout.splitlines() == [ALLOWED_PATH]
    assert _git(repo, "diff", "--cached", "--name-only").stdout.splitlines() == [staged.name]
    status = _git(repo, "status", "--porcelain", "--", untracked.name).stdout
    assert status == f"?? {untracked.name}\n"


def test_push_rejects_non_backup_file_anywhere_in_history(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(repo, "remote", "add", "origin", str(remote))

    # Simulate accidentally selecting an existing unrelated repository.  The
    # foreign file is deleted again so the current HEAD tree is clean; a check
    # limited to the latest tree/commit would therefore miss the old secret.
    foreign = _write(repo, "private-history.txt", SENSITIVE_DIAGNOSTIC)
    _git(repo, "add", "--", foreign.name)
    _git(repo, "commit", "-m", "unrelated history")
    _git(repo, "rm", "--", foreign.name)
    _git(repo, "commit", "-m", "remove unrelated file")
    _write(repo, ALLOWED_PATH, '{"content":"public"}\n')
    _git(repo, "add", "--", ALLOWED_PATH)
    _git(repo, "commit", "-m", "backup")
    assert _git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines() == [
        ALLOWED_PATH
    ]

    with pytest.raises(git_repo.GitPushError):
        git_repo.push(repo)

    assert _git(remote, "show-ref", check=False).stdout == ""


def test_push_rejects_foreign_path_introduced_only_by_merge_result(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(repo, "remote", "add", "origin", str(remote))

    _write(repo, ALLOWED_PATH, '{"content":"base"}\n')
    git_repo.add_commit(repo, "base", paths=[ALLOWED_PATH])
    _git(repo, "checkout", "-b", "peer")
    peer_path = "clips/2026/06/2026-06-14.jsonl"
    _write(repo, peer_path, '{"content":"peer"}\n')
    _git(repo, "add", "--", peer_path)
    _git(repo, "commit", "-m", "peer backup")
    _git(repo, "checkout", "main")
    main_path = "clips/2026/06/2026-06-15.jsonl"
    _write(repo, main_path, '{"content":"main"}\n')
    git_repo.add_commit(repo, "main backup", paths=[main_path])

    _git(repo, "merge", "--no-commit", "--no-ff", "peer")
    foreign = _write(repo, "merge-only-private.txt", SENSITIVE_DIAGNOSTIC)
    _git(repo, "add", "--", foreign.name)
    _git(repo, "commit", "-m", "merge peer")
    parents = _git(repo, "show", "-s", "--format=%P", "HEAD").stdout.split()
    assert len(parents) == 2
    for parent in parents:
        assert foreign.name not in _git(
            repo, "ls-tree", "-r", "--name-only", parent
        ).stdout.splitlines()
    assert foreign.name in _git(
        repo, "ls-tree", "-r", "--name-only", "HEAD"
    ).stdout.splitlines()

    with pytest.raises(git_repo.GitPushError):
        git_repo.push(repo)

    assert _git(remote, "show-ref", check=False).stdout == ""


def test_push_ignores_remote_mirror_and_sends_only_current_branch(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "config", "remote.origin.mirror", "true")
    _write(repo, ALLOWED_PATH, '{"content":"main"}\n')
    git_repo.add_commit(repo, "main backup", paths=[ALLOWED_PATH])

    _git(repo, "checkout", "-b", "private-branch")
    private = _write(repo, "private-branch-secret.txt", SENSITIVE_DIAGNOSTIC)
    _git(repo, "add", "--", private.name)
    _git(repo, "commit", "-m", "private branch")
    _git(repo, "tag", "private-tag")
    _git(repo, "checkout", "main")

    git_repo.push(repo)

    refs = _git(remote, "for-each-ref", "--format=%(refname)").stdout.splitlines()
    assert refs == ["refs/heads/main"]


def test_push_rejects_configured_remote_with_multiple_push_urls(tmp_path):
    repo = _configured_repo(tmp_path)
    remotes = [tmp_path / "remote-one.git", tmp_path / "remote-two.git"]
    for remote in remotes:
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(remote)],
            check=True,
            capture_output=True,
            text=True,
        )
    _git(repo, "remote", "add", "origin", str(remotes[0]))
    for remote in remotes:
        _git(repo, "config", "--add", "remote.origin.pushurl", str(remote))
    _write(repo, ALLOWED_PATH)
    git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])

    with pytest.raises(git_repo.GitPushError):
        git_repo.push(repo)

    for remote in remotes:
        assert _git(remote, "show-ref", check=False).stdout == ""


def test_add_commit_disables_pre_and_post_commit_hooks(tmp_path):
    repo = _configured_repo(tmp_path)
    marker = repo / "commit-hook-ran.txt"
    hooks_dir = repo / "configured-hooks"
    _git(repo, "config", "core.hooksPath", str(hooks_dir))
    _install_hook(repo, "pre-commit", marker.name, exit_code=1, hooks_dir=hooks_dir)
    _install_hook(repo, "post-commit", marker.name, exit_code=0, hooks_dir=hooks_dir)
    _write(repo, ALLOWED_PATH)

    assert git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH]) is not None
    assert not marker.exists()


def test_push_disables_pre_push_hook(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(repo, "remote", "add", "origin", str(remote))
    _write(repo, ALLOWED_PATH)
    git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])
    marker = repo / "push-hook-ran.txt"
    hooks_dir = repo / "configured-hooks"
    _git(repo, "config", "core.hooksPath", str(hooks_dir))
    _install_hook(repo, "pre-push", marker.name, exit_code=1, hooks_dir=hooks_dir)

    git_repo.push(repo)

    assert not marker.exists()
    assert _git(remote, "show-ref", "--verify", "refs/heads/main").returncode == 0


def test_push_error_does_not_expose_raw_git_stderr(tmp_path):
    repo = _configured_repo(tmp_path)
    _write(repo, ALLOWED_PATH)
    git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])
    missing_remote = tmp_path / SENSITIVE_DIAGNOSTIC

    with pytest.raises(git_repo.GitPushError) as caught:
        git_repo.push(repo, str(missing_remote))

    assert SENSITIVE_DIAGNOSTIC not in str(caught.value)
    assert str(missing_remote) not in str(caught.value)
