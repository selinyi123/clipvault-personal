"""C7: backup Git operations are append-only, path-scoped, and fail closed.

A hung Git operation must surface as a normal Git error rather than an
uncaught ``TimeoutExpired``.  Backup commits and pushes must never carry files
outside the dated ``clips/**/*.jsonl`` layout, even when the configured backup
directory accidentally points at an existing repository.
"""

import inspect
import os
import stat
import subprocess
from pathlib import Path

import pytest

from clipvault.backup import git_repo

FORBIDDEN = {"pull", "force", "rebase", "amend", "reset", "fetch"}
ALLOWED_PATH = "clips/2026/06/2026-06-13.jsonl"
BRANCH_REF = "refs/heads/main"
SENSITIVE_DIAGNOSTIC = "PRIVATE-AKIAIOSFODNN7EXAMPLE-Alice-Vault"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _git_bytes(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
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
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def _allow_backup_line(_relpath: str, _line: str) -> bool:
    return True


def _authorized_push(
    repo: Path,
    remote: str = "origin",
    *,
    validator=_allow_backup_line,
) -> git_repo.PushAuthorization:
    candidate = git_repo.prepare_push(repo, remote)
    return git_repo.authorize_push(candidate, validator=validator)


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


def _install_marker_command(
    repo: Path,
    name: str,
    marker_name: str,
    *,
    passthrough: bool = False,
) -> Path:
    command = repo / name
    command.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' {name} >> {marker_name}\n"
        + ("cat\n" if passthrough else "exit 1\n"),
        encoding="utf-8",
        newline="\n",
    )
    command.chmod(
        command.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    return command


def _unspecified_attributes_output(*paths: str) -> str:
    return "".join(
        f"{path}\0{attribute}\0unspecified\0"
        for path in paths
        for attribute in git_repo._CONTENT_ATTRIBUTES
    )


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


def _timeout_result(*_args, **_kwargs):
    return subprocess.CompletedProcess(
        ["git"],
        returncode=124,
        stdout="",
        stderr="git timed out",
    )


def test_push_candidate_timeout_raises_gitpush_error_not_timeout(monkeypatch):
    # The runner's synthetic timeout result must become a GitPushError so
    # BackupWorker backs off instead of leaking TimeoutExpired.
    monkeypatch.setattr(git_repo, "_run", _timeout_result)
    with pytest.raises(git_repo.GitPushError):
        git_repo.prepare_push("/tmp/whatever")


def test_add_timeout_raises_git_error(monkeypatch):
    monkeypatch.setattr(git_repo, "_run", _timeout_result)
    with pytest.raises(git_repo.GitError):
        git_repo.add_commit("/tmp/whatever", "msg", paths=[ALLOWED_PATH])


def test_raw_object_write_failure_does_not_run_commit_tree(monkeypatch):
    calls = []

    def fake_run(
        repo, args, timeout=git_repo._TIMEOUT, *, env=None, input_bytes=None,
    ):
        calls.append((args, env))
        if args == ["symbolic-ref", "--quiet", "HEAD"]:
            return subprocess.CompletedProcess(["git", *args], 0, BRANCH_REF + "\n", "")
        if args == ["rev-parse", "--verify", "-q", f"{BRANCH_REF}^{{commit}}"]:
            return subprocess.CompletedProcess(
                ["git", *args], returncode=1, stdout="", stderr="no commits",
            )
        if args and args[0] == "check-attr":
            return subprocess.CompletedProcess(
                ["git", *args], 0, _unspecified_attributes_output(ALLOWED_PATH), "",
            )
        if args == ["read-tree", "--empty"] and env:
            return subprocess.CompletedProcess(["git", *args], 0, "", "")
        if args and args[0] == "hash-object":
            return subprocess.CompletedProcess(
                ["git", *args], returncode=1, stdout="", stderr=SENSITIVE_DIAGNOSTIC,
            )
        raise AssertionError(f"commit-tree should not run after failed object write: {args}")

    monkeypatch.setattr(git_repo, "_run", fake_run)
    monkeypatch.setattr(
        git_repo, "_read_backup_file_no_follow", lambda repo, path: b"{}\n",
    )

    with pytest.raises(git_repo.GitError) as caught:
        git_repo.add_commit("/tmp/whatever", "msg", paths=[ALLOWED_PATH])

    assert calls[-1][0][0] == "hash-object"
    assert not any(args and args[0] in {"commit-tree", "update-ref"} for args, _ in calls)
    assert SENSITIVE_DIAGNOSTIC not in str(caught.value)


def test_commit_timeout_raises_git_error(monkeypatch):
    calls = []
    object_id = "1" * 40
    tree_id = "2" * 40

    def fake_run(
        repo, args, timeout=git_repo._TIMEOUT, *, env=None, input_bytes=None,
    ):
        calls.append((args, env))
        if args == ["symbolic-ref", "--quiet", "HEAD"]:
            return subprocess.CompletedProcess(["git", *args], 0, BRANCH_REF + "\n", "")
        if args == ["rev-parse", "--verify", "-q", f"{BRANCH_REF}^{{commit}}"]:
            return subprocess.CompletedProcess(
                ["git", *args], returncode=1, stdout="", stderr="no commits",
            )
        if args and args[0] == "check-attr":
            return subprocess.CompletedProcess(
                ["git", *args], 0, _unspecified_attributes_output(ALLOWED_PATH), "",
            )
        if args == ["read-tree", "--empty"] and env:
            return subprocess.CompletedProcess(["git", *args], 0, "", "")
        if args and args[0] == "hash-object":
            return subprocess.CompletedProcess(["git", *args], 0, object_id + "\n", "")
        if args and args[0] == "update-index" and env:
            return subprocess.CompletedProcess(["git", *args], 0, "", "")
        if args == ["write-tree"] and env:
            return subprocess.CompletedProcess(["git", *args], 0, tree_id + "\n", "")
        if args and args[0] == "commit-tree":
            return subprocess.CompletedProcess(
                ["git", *args], 124, "", SENSITIVE_DIAGNOSTIC,
            )
        raise AssertionError(f"unexpected git operation: {args}")

    monkeypatch.setattr(git_repo, "_run", fake_run)
    monkeypatch.setattr(
        git_repo, "_read_backup_file_no_follow", lambda repo, path: b"{}\n",
    )

    with pytest.raises(git_repo.GitError) as caught:
        git_repo.add_commit("/tmp/whatever", "msg", paths=[ALLOWED_PATH])

    assert calls[-1][0][0] == "commit-tree"
    assert SENSITIVE_DIAGNOSTIC not in str(caught.value)


@pytest.mark.parametrize("returncode", [2, 124])
def test_add_history_probe_failure_fails_closed_before_add(monkeypatch, returncode):
    calls = []

    def fake_run(
        repo, args, timeout=git_repo._TIMEOUT, *, env=None, input_bytes=None,
    ):
        calls.append(args)
        if args == ["symbolic-ref", "--quiet", "HEAD"]:
            return subprocess.CompletedProcess(["git", *args], 0, BRANCH_REF + "\n", "")
        if args == ["rev-parse", "--verify", "-q", f"{BRANCH_REF}^{{commit}}"]:
            return subprocess.CompletedProcess(
                ["git", *args], returncode, "", SENSITIVE_DIAGNOSTIC,
            )
        raise AssertionError(f"history probe failure must stop before Git mutation: {args}")

    monkeypatch.setattr(git_repo, "_run", fake_run)

    with pytest.raises(git_repo.GitError) as caught:
        git_repo.add_commit("/tmp/whatever", "msg", paths=[ALLOWED_PATH])

    assert calls == [
        ["symbolic-ref", "--quiet", "HEAD"],
        ["rev-parse", "--verify", "-q", f"{BRANCH_REF}^{{commit}}"],
    ]
    assert caught.value.returncode == returncode
    assert SENSITIVE_DIAGNOSTIC not in str(caught.value)


def test_push_requires_a_sealed_authorization():
    with pytest.raises(git_repo.GitPushError):
        git_repo.push(object())


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [
        (1, ""),
        (0, f"{'a' * 40}\t{BRANCH_REF}\n{'b' * 40}\t{BRANCH_REF}\n"),
        (0, f"{'a' * 40}\trefs/heads/other\n"),
        (0, f"not-an-object\t{BRANCH_REF}\n"),
    ],
)
def test_remote_tip_rejects_errors_ambiguity_and_wrong_ref(
    monkeypatch,
    returncode,
    stdout,
):
    monkeypatch.setattr(
        git_repo,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["git"], returncode, stdout, SENSITIVE_DIAGNOSTIC,
        ),
    )

    with pytest.raises(git_repo.GitError) as caught:
        git_repo._remote_branch_tip("repo", "private-target", BRANCH_REF)

    assert SENSITIVE_DIAGNOSTIC not in str(caught.value)


def test_remote_tip_allows_an_exactly_absent_branch(monkeypatch):
    monkeypatch.setattr(
        git_repo,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["git"], 0, "", "",
        ),
    )

    assert git_repo._remote_branch_tip("repo", "private-target", BRANCH_REF) is None


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


def test_add_commit_rejects_symbolic_link_file_without_reading_target(tmp_path):
    repo = _configured_repo(tmp_path)
    outside = tmp_path / "private-outside-repo.txt"
    outside.write_text(SENSITIVE_DIAGNOSTIC, encoding="utf-8")
    target = repo / Path(ALLOWED_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc.__class__.__name__}")

    with pytest.raises(git_repo.GitError):
        git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])

    assert git_repo.head_commit(repo) is None


def test_add_commit_rejects_hard_link_file_without_reading_target(tmp_path):
    repo = _configured_repo(tmp_path)
    outside = tmp_path / "private-hardlink-target.txt"
    outside.write_text(SENSITIVE_DIAGNOSTIC, encoding="utf-8")
    target = repo / Path(ALLOWED_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(outside, target)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc.__class__.__name__}")

    with pytest.raises(git_repo.GitError):
        git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])

    assert git_repo.head_commit(repo) is None


def test_add_commit_updates_snapshotted_branch_during_concurrent_checkout(
    tmp_path,
    monkeypatch,
):
    repo = _configured_repo(tmp_path)
    _write(repo, ALLOWED_PATH, '{"content":"base"}\n')
    base = git_repo.add_commit(repo, "base", paths=[ALLOWED_PATH])
    _git(repo, "branch", "peer")
    _write(
        repo,
        ALLOWED_PATH,
        '{"content":"base"}\n{"content":"next"}\n',
    )
    peer_index_before = _git_bytes(repo, "show", f":{ALLOWED_PATH}").stdout
    peer_status_before = _git(repo, "status", "--porcelain", "--", ALLOWED_PATH).stdout
    original_run = git_repo._run
    switched = False

    def switch_before_commit(repo_arg, args, timeout=git_repo._TIMEOUT, **kwargs):
        nonlocal switched
        if args and args[0] == "commit-tree" and not switched:
            _git(repo, "checkout", "--quiet", "peer")
            switched = True
        return original_run(repo_arg, args, timeout, **kwargs)

    monkeypatch.setattr(git_repo, "_run", switch_before_commit)

    committed = git_repo.add_commit(repo, "next", paths=[ALLOWED_PATH])

    assert switched
    assert committed is not None
    assert _git(repo, "rev-parse", "refs/heads/main").stdout.strip() == committed
    assert _git(repo, "rev-parse", "refs/heads/peer").stdout.strip() == base
    assert _git_bytes(repo, "show", f":{ALLOWED_PATH}").stdout == peer_index_before
    assert _git(repo, "status", "--porcelain", "--", ALLOWED_PATH).stdout == (
        peer_status_before
    )


def test_add_commit_returns_durable_sha_when_real_index_sync_fails(
    tmp_path,
    monkeypatch,
):
    repo = _configured_repo(tmp_path)
    _write(repo, ALLOWED_PATH, '{"content":"public"}\n')

    def fail_index_sync(repo_arg, branch_ref, blobs):
        raise git_repo.GitError("git index synchronization failed")

    monkeypatch.setattr(git_repo, "_sync_real_index", fail_index_sync)

    committed = git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])

    assert committed is not None
    assert _git(repo, "rev-parse", "refs/heads/main").stdout.strip() == committed


@pytest.mark.parametrize("driver_command", ["clean", "process"])
def test_add_commit_rejects_info_attribute_filter_without_executing_it(
    tmp_path,
    driver_command,
):
    repo = _configured_repo(tmp_path)
    marker = repo / "filter-ran.txt"
    command = _install_marker_command(
        repo,
        "evil-filter.sh",
        marker.name,
        passthrough=True,
    )
    _git(repo, "config", f"filter.evil.{driver_command}", f"./{command.name}")
    info_attributes = repo / ".git" / "info" / "attributes"
    info_attributes.parent.mkdir(parents=True, exist_ok=True)
    info_attributes.write_text(
        f"{ALLOWED_PATH} filter=evil\n",
        encoding="utf-8",
        newline="\n",
    )
    _write(repo, ALLOWED_PATH, '{"content":"public"}\n')

    with pytest.raises(git_repo.GitError):
        git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])

    assert not marker.exists()
    assert git_repo.head_commit(repo) is None


def test_add_commit_rejects_untracked_worktree_attributes_without_running_filter(
    tmp_path,
):
    repo = _configured_repo(tmp_path)
    marker = repo / "filter-ran.txt"
    command = _install_marker_command(
        repo,
        "evil-filter.sh",
        marker.name,
        passthrough=True,
    )
    _git(repo, "config", "filter.evil.clean", f"./{command.name}")
    attributes = repo / ".gitattributes"
    attributes.write_text(
        f"{ALLOWED_PATH} filter=evil\n",
        encoding="utf-8",
        newline="\n",
    )
    _write(repo, ALLOWED_PATH, '{"content":"public"}\n')

    with pytest.raises(git_repo.GitError):
        git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])

    assert not marker.exists()
    assert _git(repo, "status", "--porcelain", "--", attributes.name).stdout == (
        f"?? {attributes.name}\n"
    )
    assert git_repo.head_commit(repo) is None


def test_add_commit_disables_configured_commit_signing_program(tmp_path):
    repo = _configured_repo(tmp_path)
    marker = repo / "gpg-ran.txt"
    command = _install_marker_command(repo, "evil-gpg.sh", marker.name)
    _git(repo, "config", "commit.gpgSign", "true")
    _git(repo, "config", "gpg.program", f"./{command.name}")
    _write(repo, ALLOWED_PATH, '{"content":"public"}\n')

    assert git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH]) is not None

    assert not marker.exists()


def test_add_commit_preserves_raw_lf_bytes_when_autocrlf_is_enabled(tmp_path):
    repo = _configured_repo(tmp_path)
    _git(repo, "config", "core.autocrlf", "true")
    raw = b'{"content":"line one\\nline two"}\n'
    path = repo / Path(ALLOWED_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)

    git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])

    assert path.read_bytes() == raw
    assert _git_bytes(repo, "show", f"HEAD:{ALLOWED_PATH}").stdout == raw


def test_add_commit_preserves_unrelated_staged_and_untracked_files(tmp_path):
    repo = _configured_repo(tmp_path)
    _write(repo, ALLOWED_PATH, '{"content":"public"}\n')
    staged_bytes = (SENSITIVE_DIAGNOSTIC + "-staged\n").encode()
    working_bytes = (SENSITIVE_DIAGNOSTIC + "-working\n").encode()
    staged = repo / "staged-private.txt"
    staged.write_bytes(staged_bytes)
    untracked = _write(repo, "untracked-private.txt", SENSITIVE_DIAGNOSTIC)
    _git(repo, "add", "--", staged.name)
    staged.write_bytes(working_bytes)
    staged_entry_before = _git(repo, "ls-files", "--stage", "--", staged.name).stdout
    staged_blob_before = _git_bytes(repo, "show", f":{staged.name}").stdout
    status_before = _git(repo, "status", "--porcelain", "--", staged.name).stdout

    git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])

    committed = _git(repo, "ls-tree", "-r", "--name-only", "HEAD")
    assert committed.stdout.splitlines() == [ALLOWED_PATH]
    assert _git(repo, "diff", "--cached", "--name-only").stdout.splitlines() == [staged.name]
    assert _git(repo, "ls-files", "--stage", "--", staged.name).stdout == staged_entry_before
    assert _git_bytes(repo, "show", f":{staged.name}").stdout == staged_blob_before
    assert staged_blob_before == staged_bytes
    assert staged.read_bytes() == working_bytes
    assert _git(repo, "status", "--porcelain", "--", staged.name).stdout == status_before
    status = _git(repo, "status", "--porcelain", "--", untracked.name).stdout
    assert status == f"?? {untracked.name}\n"


def test_noop_commit_synchronizes_target_path_in_real_index(tmp_path):
    repo = _configured_repo(tmp_path)
    committed_bytes = b'{"content":"committed"}\n'
    staged_bytes = b'{"content":"staged-but-not-committed"}\n'
    target = repo / Path(ALLOWED_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(committed_bytes)
    git_repo.add_commit(repo, "initial backup", paths=[ALLOWED_PATH])

    target.write_bytes(staged_bytes)
    _git(repo, "add", "--", ALLOWED_PATH)
    assert _git_bytes(repo, "show", f":{ALLOWED_PATH}").stdout == staged_bytes
    target.write_bytes(committed_bytes)

    assert git_repo.add_commit(repo, "noop backup", paths=[ALLOWED_PATH]) is None

    assert _git_bytes(repo, "show", f":{ALLOWED_PATH}").stdout == committed_bytes
    assert target.read_bytes() == committed_bytes
    assert _git(repo, "status", "--porcelain", "--", ALLOWED_PATH).stdout == ""


def test_managed_preflight_preserves_complete_uncommitted_append(tmp_path):
    repo = _configured_repo(tmp_path)
    target = _write(repo, ALLOWED_PATH, '{"id":"first"}\n')
    git_repo.add_commit(repo, "initial backup", paths=[ALLOWED_PATH])
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('{"id":"pending"}\n')
    before = target.read_bytes()

    git_repo.assert_managed_worktree_append_only(repo)

    assert target.read_bytes() == before
    assert _git_bytes(repo, "show", f":{ALLOWED_PATH}").stdout == b'{"id":"first"}\n'


def test_managed_preflight_does_not_overwrite_arbitrary_rewrite(tmp_path):
    repo = _configured_repo(tmp_path)
    target = _write(repo, ALLOWED_PATH, '{"id":"durable"}\n')
    git_repo.add_commit(repo, "initial backup", paths=[ALLOWED_PATH])
    target.write_text('{"id":"manual-rewrite"}\n', encoding="utf-8", newline="\n")
    before = target.read_bytes()
    indexed = _git_bytes(repo, "show", f":{ALLOWED_PATH}").stdout

    with pytest.raises(git_repo.GitWorktreeRecoveryRequired):
        git_repo.assert_managed_worktree_append_only(repo)

    assert target.read_bytes() == before
    assert _git_bytes(repo, "show", f":{ALLOWED_PATH}").stdout == indexed


def _empty_remote(tmp_path: Path, name: str = "remote.git") -> Path:
    remote = tmp_path / name
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    return remote


def test_authorized_push_to_empty_remote_validates_every_line(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = _empty_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    line = '{"content":"public"}'
    _write(repo, ALLOWED_PATH, line + "\n")
    committed = git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])
    seen = []

    candidate = git_repo.prepare_push(repo)
    assert candidate.branch_ref == BRANCH_REF
    assert candidate.remote_base is None
    assert candidate.candidate_sha == committed
    authorization = git_repo.authorize_push(
        candidate,
        validator=lambda path, value: not seen.append((path, value)),
    )

    assert git_repo.push(authorization) is True
    assert seen == [(ALLOWED_PATH, line)]
    assert _git(remote, "rev-parse", BRANCH_REF).stdout.strip() == committed

    already_published = git_repo.prepare_push(repo)
    assert already_published.remote_base == committed
    noop = git_repo.authorize_push(
        already_published,
        validator=lambda _path, _line: True,
    )
    assert git_repo.push(noop) is False


def test_authorization_validates_only_linear_suffix_after_published_base(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = _empty_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    first_line = '{"id":"one","content":"public"}'
    second_line = '{"id":"two","content":"also-public"}'
    _write(repo, ALLOWED_PATH, first_line + "\n")
    first = git_repo.add_commit(repo, "first", paths=[ALLOWED_PATH])
    assert git_repo.push(_authorized_push(repo)) is True

    with (repo / Path(ALLOWED_PATH)).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(second_line + "\n")
    second = git_repo.add_commit(repo, "second", paths=[ALLOWED_PATH])
    candidate = git_repo.prepare_push(repo)
    seen = []
    authorization = git_repo.authorize_push(
        candidate,
        validator=lambda path, line: not seen.append((path, line)),
    )

    assert candidate.remote_base == first
    assert candidate.candidate_sha == second
    assert seen == [(ALLOWED_PATH, second_line)]
    assert git_repo.push(authorization) is True
    assert _git(remote, "rev-parse", BRANCH_REF).stdout.strip() == second


def test_authorization_rejects_manual_secret_line(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = _empty_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    _write(repo, ALLOWED_PATH, '{"content":"MANUAL-SECRET"}\n')
    _git(repo, "add", "--", ALLOWED_PATH)
    _git(repo, "commit", "-m", "manual line")
    candidate = git_repo.prepare_push(repo)

    with pytest.raises(git_repo.GitPushError):
        git_repo.authorize_push(
            candidate,
            validator=lambda _path, line: "SECRET" not in line,
        )

    assert _git(remote, "show-ref", check=False).stdout == ""


def test_prepare_rejects_foreign_path_even_when_it_is_in_published_base(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = _empty_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    foreign = _write(repo, "private-history.txt", SENSITIVE_DIAGNOSTIC)
    _git(repo, "add", "--", foreign.name)
    _git(repo, "commit", "-m", "foreign published base")
    published_base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "push", "origin", f"{published_base}:{BRANCH_REF}")
    _git(repo, "rm", "--", foreign.name)
    _write(repo, ALLOWED_PATH, '{"content":"public"}\n')
    _git(repo, "add", "--", ALLOWED_PATH)
    _git(repo, "commit", "-m", "hide foreign path")

    with pytest.raises(git_repo.GitPushError) as caught:
        git_repo.prepare_push(repo)

    assert SENSITIVE_DIAGNOSTIC not in str(caught.value)
    assert _git(remote, "rev-parse", BRANCH_REF).stdout.strip() == published_base


def test_intermediate_secret_then_delete_is_still_rejected(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = _empty_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    secret_line = '{"content":"INTERMEDIATE-SECRET"}'
    _write(repo, ALLOWED_PATH, secret_line + "\n")
    _git(repo, "add", "--", ALLOWED_PATH)
    _git(repo, "commit", "-m", "secret intermediate")
    _git(repo, "rm", "--", ALLOWED_PATH)
    _git(repo, "commit", "-m", "hide intermediate")
    assert _git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout == ""
    seen = []

    with pytest.raises(git_repo.GitPushError):
        git_repo.authorize_push(
            git_repo.prepare_push(repo),
            validator=lambda _path, line: not seen.append(line) and "SECRET" not in line,
        )

    assert seen == [secret_line]
    assert _git(remote, "show-ref", check=False).stdout == ""


def test_authorization_rejects_non_append_rewrite(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = _empty_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    _write(repo, ALLOWED_PATH, '{"content":"first"}\n')
    _git(repo, "add", "--", ALLOWED_PATH)
    _git(repo, "commit", "-m", "first")
    _write(repo, ALLOWED_PATH, '{"content":"rewritten"}\n')
    _git(repo, "add", "--", ALLOWED_PATH)
    _git(repo, "commit", "-m", "rewrite")

    with pytest.raises(git_repo.GitPushError):
        git_repo.authorize_push(
            git_repo.prepare_push(repo),
            validator=_allow_backup_line,
        )


def test_authorization_rejects_merge_even_when_paths_are_backup_jsonl(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = _empty_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    _write(repo, ALLOWED_PATH, '{"content":"base"}\n')
    git_repo.add_commit(repo, "base", paths=[ALLOWED_PATH])
    _git(repo, "checkout", "-b", "peer")
    peer_path = "clips/2026/06/2026-06-14.jsonl"
    _write(repo, peer_path, '{"content":"peer"}\n')
    _git(repo, "add", "--", peer_path)
    _git(repo, "commit", "-m", "peer")
    _git(repo, "checkout", "main")
    main_path = "clips/2026/06/2026-06-15.jsonl"
    _write(repo, main_path, '{"content":"main"}\n')
    git_repo.add_commit(repo, "main", paths=[main_path])
    _git(repo, "merge", "--no-ff", "peer", "-m", "merge")

    with pytest.raises(git_repo.GitPushError):
        git_repo.authorize_push(
            git_repo.prepare_push(repo),
            validator=_allow_backup_line,
        )


def test_push_rejects_remote_concurrency_after_authorization(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = _empty_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    _write(repo, ALLOWED_PATH, '{"content":"base"}\n')
    git_repo.add_commit(repo, "base", paths=[ALLOWED_PATH])
    assert git_repo.push(_authorized_push(repo)) is True

    with (repo / Path(ALLOWED_PATH)).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('{"content":"candidate"}\n')
    git_repo.add_commit(repo, "candidate", paths=[ALLOWED_PATH])
    authorization = _authorized_push(repo)

    peer = tmp_path / "peer"
    subprocess.run(
        ["git", "clone", "--branch", "main", str(remote), str(peer)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(peer, "config", "user.name", "Peer")
    _git(peer, "config", "user.email", "peer@example.com")
    peer_path = "clips/2026/06/2026-06-14.jsonl"
    _write(peer, peer_path, '{"content":"peer"}\n')
    _git(peer, "add", "--", peer_path)
    _git(peer, "commit", "-m", "peer wins race")
    _git(peer, "push", "origin", "main")
    remote_tip = _git(remote, "rev-parse", BRANCH_REF).stdout.strip()

    with pytest.raises(git_repo.GitPushError):
        git_repo.push(authorization)

    assert _git(remote, "rev-parse", BRANCH_REF).stdout.strip() == remote_tip


def test_push_rejects_local_branch_movement_after_authorization(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = _empty_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    _write(repo, ALLOWED_PATH, '{"content":"authorized"}\n')
    git_repo.add_commit(repo, "authorized", paths=[ALLOWED_PATH])
    authorization = _authorized_push(repo)
    with (repo / Path(ALLOWED_PATH)).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write('{"content":"moved"}\n')
    git_repo.add_commit(repo, "moved", paths=[ALLOWED_PATH])

    with pytest.raises(git_repo.GitPushError):
        git_repo.push(authorization)

    assert _git(remote, "show-ref", check=False).stdout == ""


def test_push_runs_final_validator_after_remote_preflight(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = _empty_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    _write(repo, ALLOWED_PATH, '{"content":"authorized"}\n')
    git_repo.add_commit(repo, "authorized", paths=[ALLOWED_PATH])
    authorization = _authorized_push(repo)
    seen = []

    with pytest.raises(git_repo.GitPushError, match="final validation"):
        git_repo.push(
            authorization,
            final_validator=lambda candidate: not seen.append(
                candidate.candidate_sha
            ) and False,
        )

    assert seen == [authorization.candidate.candidate_sha]
    assert _git(remote, "show-ref", check=False).stdout == ""


def test_push_ignores_remote_mirror_and_sends_only_authorized_branch(tmp_path):
    repo = _configured_repo(tmp_path)
    remote = _empty_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "config", "remote.origin.mirror", "true")
    _write(repo, ALLOWED_PATH, '{"content":"main"}\n')
    git_repo.add_commit(repo, "main backup", paths=[ALLOWED_PATH])
    authorization = _authorized_push(repo)

    _git(repo, "checkout", "-b", "private-branch")
    private = _write(repo, "private-branch-secret.txt", SENSITIVE_DIAGNOSTIC)
    _git(repo, "add", "--", private.name)
    _git(repo, "commit", "-m", "private branch")
    _git(repo, "tag", "private-tag")
    _git(repo, "checkout", "main")

    assert git_repo.push(authorization) is True

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
        git_repo.prepare_push(repo)

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
    authorization = _authorized_push(repo)
    marker = repo / "push-hook-ran.txt"
    hooks_dir = repo / "configured-hooks"
    _git(repo, "config", "core.hooksPath", str(hooks_dir))
    _install_hook(repo, "pre-push", marker.name, exit_code=1, hooks_dir=hooks_dir)

    assert git_repo.push(authorization) is True

    assert not marker.exists()
    assert _git(remote, "show-ref", "--verify", "refs/heads/main").returncode == 0


def test_push_error_does_not_expose_raw_git_stderr(tmp_path):
    repo = _configured_repo(tmp_path)
    _write(repo, ALLOWED_PATH)
    git_repo.add_commit(repo, "backup", paths=[ALLOWED_PATH])
    missing_remote = tmp_path / SENSITIVE_DIAGNOSTIC

    with pytest.raises(git_repo.GitPushError) as caught:
        git_repo.prepare_push(repo, str(missing_remote))

    assert SENSITIVE_DIAGNOSTIC not in str(caught.value)
    assert str(missing_remote) not in str(caught.value)
