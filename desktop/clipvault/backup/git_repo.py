"""Thin git CLI wrapper for the backup repo (GHB-1).

Deliberately offers only append-style publication. There is NO pull, force,
rebase, or amend function: the remote backup is an append-only log (ADR-0003,
GATES G3). A private helper may replace an unsafe *local-only* suffix with a
new candidate rooted at the exact published base; it never rewrites that base
and never returns a push authorization. test_backup_git.py::test_c7 enforces
the forbidden public operations.
"""

import os
import re
import math
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from clipvault.backup import cancellation, jsonl_store
from clipvault.backup.process_tree import ProcessTreeController

_TIMEOUT = 60
_PROCESS_POLL_S = 0.05
_PROCESS_TERMINATE_GRACE_S = 0.25
_PROCESS_REAP_TIMEOUT_S = 2.0
_DISABLED_HOOKS_PATH = "NUL" if os.name == "nt" else "/dev/null"
_OBJECT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_CONTENT_ATTRIBUTES = (
    "filter",
    "working-tree-encoding",
    "ident",
    "text",
    "eol",
)
_ROUTING_ENVIRONMENT = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
)


class GitError(Exception):
    def __init__(self, message: str, returncode: int | None = None) -> None:
        self.returncode = returncode
        super().__init__(message)


class GitPushError(GitError):
    pass


class OwnerRemediationRequired(GitPushError):
    """Published backup history needs Owner-controlled incident remediation."""


class GitWorktreeRecoveryRequired(GitError):
    """Managed worktree bytes no longer form an append of the branch tip."""


@dataclass(frozen=True, slots=True, repr=False)
class PushCandidate:
    """Immutable snapshot of one unpublished backup branch tip.

    The resolved target may contain credentials or a private path, so neither
    this object nor its authorization has a value-bearing representation.
    """

    _repo: str = field(repr=False)
    _target: str = field(repr=False)
    branch_ref: str
    remote_base: str | None
    candidate_sha: str

    def __repr__(self) -> str:
        return "<PushCandidate redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class PushAuthorization:
    """In-memory proof that every unpublished JSONL line was validated."""

    candidate: PushCandidate = field(repr=False)
    _seal: object = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "<PushAuthorization redacted>"


_AUTHORIZATION_SEAL = object()


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


def _run(
    repo,
    args: list[str],
    timeout: int = _TIMEOUT,
    *,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess:
    # Backup is an unattended data path. Repository/global hooks must not turn
    # a misconfigured worktree into arbitrary side effects during add/commit/push.
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout < 0
    ):
        raise ValueError("invalid Git command timeout")
    cmd = [
        "git",
        "-c",
        f"core.hooksPath={_DISABLED_HOOKS_PATH}",
        "-c",
        f"core.attributesFile={_DISABLED_HOOKS_PATH}",
        "-c",
        "core.autocrlf=false",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "commit.gpgSign=false",
        "-C",
        str(repo),
        *args,
    ]
    process_env = os.environ.copy()
    for name in _ROUTING_ENVIRONMENT:
        process_env.pop(name, None)
    for name in tuple(process_env):
        if name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            process_env.pop(name, None)
    if env:
        process_env.update(env)
    # These values are policy, not caller preferences. Apply them last so a
    # temporary-index environment cannot restore terminal prompts. Deliberately
    # preserve configured AskPass helpers: they are also a standard unattended
    # credential source for existing HTTPS remotes and encrypted SSH keys.
    process_env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    cancellation.checkpoint()
    controller = ProcessTreeController(grace_s=_PROCESS_TERMINATE_GRACE_S)
    popen_kwargs = {
        "stdin": subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": process_env,
    }
    popen_kwargs.update(controller.popen_kwargs)
    process = None
    try:
        process = subprocess.Popen(cmd, **popen_kwargs)
        controller.attach(process)
    except BaseException:
        try:
            if process is not None:
                try:
                    controller.terminate(process)
                finally:
                    if process.poll() is None:
                        try:
                            process.kill()
                        except OSError:
                            pass
                    _reap_process(process)
        finally:
            controller.close()
        raise

    deadline = time.monotonic() + float(timeout)
    first_input = input_bytes
    # Hard-cancelling persistent ref/index writers can strand Git lock files.
    # Temporary-index updates are disposable and remain interruptible; an
    # already-started ref or real-index write may finish (or reach the existing
    # command timeout) before the next checkpoint observes shutdown.
    persistent_writer = bool(args) and (
        args[0] == "update-ref"
        or (
            args[0] == "update-index"
            and (env is None or "GIT_INDEX_FILE" not in env)
        )
    )
    interruptible = not persistent_writer
    aborted = False
    try:
        while True:
            now = time.monotonic()
            if interruptible:
                cancellation.checkpoint()
            remaining = deadline - now
            if remaining <= 0:
                aborted = True
                _stop_and_reap_process(process, controller)
                event = cancellation.current_event()
                if event is not None and event.is_set():
                    raise cancellation.BackupCancelled(
                        "backup shutdown requested"
                    )
                # Keep the established timeout contract: callers turn 124 into
                # GitError/GitPushError and retain their existing retry policy.
                return subprocess.CompletedProcess(
                    cmd,
                    returncode=124,
                    stdout="",
                    stderr=f"git timed out after {timeout}s",
                )
            try:
                stdout, stderr = process.communicate(
                    input=first_input,
                    timeout=min(_PROCESS_POLL_S, remaining),
                )
                break
            except subprocess.TimeoutExpired:
                # communicate() may be retried after TimeoutExpired, but input
                # must only be supplied on the first call.
                first_input = None

        return subprocess.CompletedProcess(
            cmd,
            process.returncode,
            stdout=stdout.decode("utf-8", errors="surrogateescape"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )
    except BaseException:
        # Popen succeeded, so every exit path must reap it before the repo lock
        # can be released. This also covers unexpected decoder/test failures.
        if not aborted:
            aborted = True
            _stop_and_reap_process(process, controller)
        raise
    finally:
        # KILL_ON_JOB_CLOSE (Windows) and the owned process group (POSIX) keep a
        # helper that closed its pipe but outlived Git from escaping this scope.
        controller.close()


def _stop_and_reap_process(
    process: subprocess.Popen,
    controller: ProcessTreeController,
) -> None:
    """Terminate the owned tree and prove the direct process was reaped."""

    termination_error = None
    try:
        controller.terminate(process)
    except cancellation.BackupProcessTerminationError as exc:
        termination_error = exc
    finally:
        _reap_process(process)
    if termination_error is not None:
        raise termination_error


def _reap_process(process: subprocess.Popen) -> None:
    """Close pipes and wait for the direct Git process after tree termination."""

    try:
        process.communicate(timeout=_PROCESS_REAP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.communicate(timeout=_PROCESS_REAP_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            raise cancellation.BackupProcessTerminationError(
                "Git process reap failed"
            ) from None
    except OSError:
        raise cancellation.BackupProcessTerminationError(
            "Git process reap failed"
        ) from None
    if process.poll() is None:
        raise cancellation.BackupProcessTerminationError(
            "Git process reap failed"
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


def _symbolic_branch_ref(repo) -> str:
    result = _run(repo, ["symbolic-ref", "--quiet", "HEAD"])
    branch_ref = result.stdout.strip()
    if (
        result.returncode != 0
        or not branch_ref.startswith("refs/heads/")
        or _has_control_chars(branch_ref)
    ):
        raise GitError("backup branch inspection failed", result.returncode)
    return branch_ref


def _assert_backup_only_history(repo, branch_ref: str) -> str | None:
    """Refuse to push a branch whose existing history ever tracked other files."""

    head_result = _run(
        repo,
        ["rev-parse", "--verify", "-q", f"{branch_ref}^{{commit}}"],
    )
    if head_result.returncode == 1:
        return None
    head = head_result.stdout.strip()
    if (
        head_result.returncode != 0
        or _OBJECT_ID_RE.fullmatch(head) is None
    ):
        raise GitError("backup history head inspection failed", head_result.returncode)
    result = _run(
        repo,
        [
            "log",
            "--diff-merges=separate",
            "--no-ext-diff",
            "--no-textconv",
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
    return head


def _assert_no_content_attributes(repo, paths: Sequence[str]) -> None:
    """Reject attributes that could execute helpers or transform JSONL bytes."""

    result = _run(
        repo,
        ["check-attr", "-z", *_CONTENT_ATTRIBUTES, "--", *paths],
    )
    if result.returncode != 0:
        raise GitError("backup attribute inspection failed", result.returncode)
    fields = result.stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    expected_pairs = {
        (path, attribute)
        for path in paths
        for attribute in _CONTENT_ATTRIBUTES
    }
    if len(fields) != len(expected_pairs) * 3:
        raise GitError("backup attribute inspection failed")
    seen: set[tuple[str, str]] = set()
    for index in range(0, len(fields), 3):
        path, attribute, value = fields[index:index + 3]
        pair = (path, attribute)
        if pair not in expected_pairs or pair in seen or value != "unspecified":
            raise GitError("backup content attributes are not allowed")
        seen.add(pair)
    if seen != expected_pairs:
        raise GitError("backup attribute inspection failed")


def _object_id(result: subprocess.CompletedProcess, operation: str) -> str:
    value = result.stdout.strip()
    if result.returncode != 0 or _OBJECT_ID_RE.fullmatch(value) is None:
        raise GitError(operation, result.returncode)
    return value


def _cacheinfo_args(path: str, object_id: str) -> list[str]:
    return ["update-index", "--add", "--cacheinfo", f"100644,{object_id},{path}"]


def _sync_real_index(
    repo,
    branch_ref: str,
    blobs: Mapping[str, str],
) -> None:
    """Sync managed entries only while the snapshotted branch stays checked out."""

    current_ref = _run(repo, ["symbolic-ref", "--quiet", "HEAD"])
    if current_ref.returncode != 0:
        raise GitError("backup branch inspection failed", current_ref.returncode)
    if current_ref.stdout.strip() != branch_ref:
        return
    for path, object_id in blobs.items():
        result = _run(repo, _cacheinfo_args(path, object_id))
        if result.returncode != 0:
            raise GitError("git index synchronization failed", result.returncode)


def _read_regular_file_posix(root: Path, relpath: str) -> bytes:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory is None:
        raise GitError("backup file safety check is unavailable")
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, os.O_RDONLY | directory | no_follow | close_on_exec)
        descriptors.append(current)
        parts = relpath.split("/")
        for part in parts[:-1]:
            current = os.open(
                part,
                os.O_RDONLY | directory | no_follow | close_on_exec,
                dir_fd=current,
            )
            descriptors.append(current)
            if not stat.S_ISDIR(os.fstat(current).st_mode):
                raise GitError("backup path safety check failed")
        file_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | no_follow | close_on_exec | getattr(os, "O_NONBLOCK", 0),
            dir_fd=current,
        )
        descriptors.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise GitError("backup path safety check failed")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(file_descriptor)
        signature_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        signature_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        data = b"".join(chunks)
        if signature_before != signature_after or len(data) != before.st_size:
            raise GitError("backup file changed during safety check")
        return data
    except OSError:
        raise GitError("backup path safety check failed") from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_regular_file_windows(root: Path, relpath: str) -> bytes:
    # Keep every directory handle open without FILE_SHARE_DELETE while opening
    # the next component.  FILE_FLAG_OPEN_REPARSE_POINT then lets us reject a
    # junction/symlink before Windows can redirect the final file read.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME),
            ("written", wintypes.FILETIME),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    generic_read = 0x80000000
    read_attributes = 0x00000080
    share_read = 0x00000001
    open_existing = 3
    attribute_directory = 0x00000010
    attribute_reparse = 0x00000400
    flag_open_reparse = 0x00200000
    flag_backup_semantics = 0x02000000
    flag_sequential_scan = 0x08000000
    invalid_handle = ctypes.c_void_p(-1).value

    def extended(path: Path) -> str:
        value = str(path)
        if value.startswith("\\\\?\\"):
            return value
        if value.startswith("\\\\"):
            return "\\\\?\\UNC\\" + value[2:]
        return "\\\\?\\" + value

    def information(handle) -> FileInformation:
        result = FileInformation()
        if not get_information(handle, ctypes.byref(result)):
            raise OSError(ctypes.get_last_error(), "file information unavailable")
        return result

    def signature(value: FileInformation) -> tuple[int, ...]:
        return (
            value.attributes,
            value.links,
            value.volume_serial,
            value.size_high,
            value.size_low,
            value.written.dwHighDateTime,
            value.written.dwLowDateTime,
            value.index_high,
            value.index_low,
        )

    directory_handles: list[int] = []
    file_handle = None
    try:
        current = root
        for component in (None, *relpath.split("/")[:-1]):
            if component is not None:
                current = current / component
            handle = create_file(
                extended(current),
                read_attributes,
                share_read,
                None,
                open_existing,
                flag_open_reparse | flag_backup_semantics,
                None,
            )
            if handle == invalid_handle:
                raise OSError(ctypes.get_last_error(), "directory open failed")
            directory_handles.append(handle)
            info = information(handle)
            if (
                info.attributes & attribute_reparse
                or not info.attributes & attribute_directory
            ):
                raise GitError("backup path safety check failed")

        file_path = current / relpath.split("/")[-1]
        file_handle = create_file(
            extended(file_path),
            generic_read,
            share_read,
            None,
            open_existing,
            flag_open_reparse | flag_sequential_scan,
            None,
        )
        if file_handle == invalid_handle:
            file_handle = None
            raise OSError(ctypes.get_last_error(), "file open failed")
        before = information(file_handle)
        if (
            before.attributes & (attribute_reparse | attribute_directory)
            or before.links != 1
        ):
            raise GitError("backup path safety check failed")
        descriptor = msvcrt.open_osfhandle(
            file_handle,
            os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT,
        )
        file_handle = None  # the Python descriptor owns the Windows handle now
        with os.fdopen(descriptor, "rb") as stream:
            data = stream.read()
            after = information(msvcrt.get_osfhandle(stream.fileno()))
        size = (before.size_high << 32) | before.size_low
        if signature(before) != signature(after) or len(data) != size:
            raise GitError("backup file changed during safety check")
        return data
    except OSError:
        raise GitError("backup path safety check failed") from None
    finally:
        if file_handle is not None:
            close_handle(file_handle)
        for handle in reversed(directory_handles):
            close_handle(handle)


def _read_backup_file_no_follow(repo, relpath: str) -> bytes:
    """Read one managed JSONL file without following nested filesystem links."""

    try:
        validated = jsonl_store.validated_daily_relpath(relpath)
        root = Path(repo).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise GitError("backup path safety check failed") from None
    if os.name == "nt":
        return _read_regular_file_windows(root, validated)
    return _read_regular_file_posix(root, validated)


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


def _raw_stdout(result: subprocess.CompletedProcess, operation: str) -> bytes:
    if result.returncode != 0:
        raise GitError(operation, result.returncode)
    return result.stdout.encode("utf-8", errors="surrogateescape")


def _local_ref_commit(repo, branch_ref: str) -> str:
    return _object_id(
        _run(repo, ["rev-parse", "--verify", f"{branch_ref}^{{commit}}"]),
        "backup branch commit inspection failed",
    )


def _remote_branch_tip(repo, target: str, branch_ref: str) -> str | None:
    """Resolve exactly one full branch ref without exposing its target."""

    result = _run(
        repo,
        ["ls-remote", "--refs", "--", target, branch_ref],
    )
    if result.returncode != 0:
        raise GitError("backup remote branch inspection failed", result.returncode)
    records = [line for line in result.stdout.splitlines() if line]
    if not records:
        return None
    if len(records) != 1:
        raise GitError("backup remote branch inspection failed")
    fields = records[0].split("\t")
    if (
        len(fields) != 2
        or fields[1] != branch_ref
        or _OBJECT_ID_RE.fullmatch(fields[0]) is None
    ):
        raise GitError("backup remote branch inspection failed")
    return fields[0]


def _assert_local_ancestor(repo, base: str | None, candidate: str) -> None:
    candidate_result = _run(repo, ["cat-file", "-e", f"{candidate}^{{commit}}"])
    if candidate_result.returncode != 0:
        raise GitError(
            "backup candidate commit is unavailable",
            candidate_result.returncode,
        )
    if base is None:
        return
    base_result = _run(repo, ["cat-file", "-e", f"{base}^{{commit}}"])
    if base_result.returncode != 0:
        raise GitError("backup remote base is unavailable", base_result.returncode)
    ancestor = _run(repo, ["merge-base", "--is-ancestor", base, candidate])
    if ancestor.returncode != 0:
        raise GitError("backup remote base is not a candidate ancestor")


def _linear_unpublished_commits(
    repo,
    base: str | None,
    candidate: str,
) -> list[tuple[str | None, str]]:
    if base == candidate:
        return []
    revision = candidate if base is None else f"{base}..{candidate}"
    result = _run(repo, ["rev-list", "--reverse", "--topo-order", revision])
    if result.returncode != 0:
        raise GitError("backup candidate enumeration failed", result.returncode)
    commits = [line for line in result.stdout.splitlines() if line]
    if not commits or any(_OBJECT_ID_RE.fullmatch(value) is None for value in commits):
        raise GitError("backup candidate enumeration failed")

    expected_parent = base
    linear: list[tuple[str | None, str]] = []
    for commit in commits:
        parent_result = _run(repo, ["rev-list", "--parents", "-n", "1", commit])
        fields = parent_result.stdout.split()
        if (
            parent_result.returncode != 0
            or not fields
            or fields[0] != commit
            or any(_OBJECT_ID_RE.fullmatch(value) is None for value in fields)
        ):
            raise GitError("backup commit parent inspection failed", parent_result.returncode)
        parents = fields[1:]
        expected = [] if expected_parent is None else [expected_parent]
        if parents != expected:
            raise GitError("backup unpublished history is not linear")
        linear.append((expected_parent, commit))
        expected_parent = commit
    if expected_parent != candidate:
        raise GitError("backup candidate enumeration failed")
    return linear


def _empty_tree(repo) -> str:
    return _object_id(
        _run(
            repo,
            ["hash-object", "-w", "-t", "tree", "--stdin"],
            input_bytes=b"",
        ),
        "backup empty tree inspection failed",
    )


def _commit_changes(
    repo,
    parent: str | None,
    commit: str,
) -> list[tuple[str, str]]:
    parent_tree = parent if parent is not None else _empty_tree(repo)
    result = _run(
        repo,
        [
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "-z",
            "-r",
            "--no-renames",
            parent_tree,
            commit,
        ],
        input_bytes=b"",
    )
    raw = _raw_stdout(result, "backup commit diff inspection failed")
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if not fields or len(fields) % 2:
        raise GitError("backup commit diff inspection failed")
    changes: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index in range(0, len(fields), 2):
        try:
            status = fields[index].decode("ascii", errors="strict")
            path = fields[index + 1].decode("utf-8", errors="strict")
            path = jsonl_store.validated_daily_relpath(path)
        except (UnicodeError, TypeError, ValueError):
            raise GitError("backup commit path validation failed") from None
        if status not in {"A", "M"} or path in seen:
            raise GitError("backup commit is not append-only")
        seen.add(path)
        changes.append((status, path))
    return changes


def _tree_blob(repo, commit: str, path: str, *, required: bool) -> str | None:
    result = _run(
        repo,
        ["ls-tree", "-z", commit, "--", path],
        input_bytes=b"",
    )
    raw = _raw_stdout(result, "backup tree entry inspection failed")
    if raw == b"":
        if required:
            raise GitError("backup tree entry inspection failed")
        return None
    if not raw.endswith(b"\0") or raw.count(b"\0") != 1:
        raise GitError("backup tree entry inspection failed")
    entry = raw[:-1]
    try:
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, raw_object_id = metadata.split(b" ", 2)
        decoded_path = raw_path.decode("utf-8", errors="strict")
        object_id = raw_object_id.decode("ascii", errors="strict")
    except (UnicodeError, ValueError):
        raise GitError("backup tree entry inspection failed") from None
    if (
        mode != b"100644"
        or kind != b"blob"
        or decoded_path != path
        or _OBJECT_ID_RE.fullmatch(object_id) is None
    ):
        raise GitError("backup tree entry inspection failed")
    return object_id


def _blob_bytes(repo, object_id: str) -> bytes:
    return _raw_stdout(
        _run(repo, ["cat-file", "blob", object_id], input_bytes=b""),
        "backup blob inspection failed",
    )


def commit_latest_clip_lines(
    repo,
    commit: str,
    relpath: str,
    clip_ids: Sequence[str],
) -> dict[str, str]:
    """Read exact latest clip lines from one durable commit blob.

    Queue acknowledgement uses this proof instead of assuming that a successful
    branch update necessarily contains every concurrently prepared worktree row.
    """

    try:
        path = jsonl_store.validated_daily_relpath(relpath)
        if _OBJECT_ID_RE.fullmatch(commit) is None:
            raise GitError("backup durable commit validation failed")
        blob = _tree_blob(repo, commit, path, required=False)
        if blob is None:
            return {}
        return jsonl_store.latest_clip_lines_bytes(
            _blob_bytes(repo, blob),
            clip_ids,
        )
    except (GitError, TypeError, ValueError) as exc:
        raise GitError(
            "backup durable content verification failed",
            getattr(exc, "returncode", None),
        ) from None


def assert_managed_worktree_append_only(repo) -> None:
    """Reject a tracked managed rewrite before a normal backup commit starts.

    Only paths reported dirty by Git are read, so the steady-state cost does not
    scale with the complete backup history. A partially scrubbed recovery is a
    rewrite relative to the old contaminated ref and is routed back to recovery
    instead of becoming a non-append child commit.
    """

    branch_ref = _symbolic_branch_ref(repo)
    head_result = _run(
        repo,
        ["rev-parse", "--verify", "-q", f"{branch_ref}^{{commit}}"],
    )
    if head_result.returncode == 1:
        return
    head = _object_id(head_result, "managed backup worktree inspection failed")
    paths: set[str] = set()
    for args in (
        ["diff-files", "--name-only", "-z", "--no-renames", "--"],
        [
            "diff-index",
            "--cached",
            "--name-only",
            "-z",
            "--no-renames",
            head,
            "--",
        ],
    ):
        result = _run(repo, args)
        if result.returncode != 0:
            raise GitError("managed backup worktree inspection failed", result.returncode)
        for raw_path in result.stdout.split("\0"):
            if not raw_path:
                continue
            try:
                paths.add(jsonl_store.validated_daily_relpath(raw_path))
            except (TypeError, ValueError):
                # Unrelated staged/worktree state is outside the dedicated
                # managed path set and remains untouched by normal commits.
                continue
    index_repairs: dict[str, str] = {}
    worktree_repairs: dict[str, bytes] = {}
    for path in sorted(paths):
        parent_blob = _tree_blob(repo, head, path, required=True)
        if parent_blob is None:  # pragma: no cover - required=True owns this
            raise GitError("managed backup worktree inspection failed")
        parent = _blob_bytes(repo, parent_blob)
        try:
            indexed = _index_blob(repo, path)
            target = jsonl_store.daily_target_path(repo, path)
            try:
                target.lstat()
            except FileNotFoundError:
                if indexed != parent_blob:
                    raise GitError("managed backup worktree is unavailable")
                # The durable ref and index agree. A lost directory entry can
                # therefore be restored without guessing about user content.
                jsonl_store.latest_clip_lines_bytes(parent, ())
                worktree_repairs[path] = parent
                continue

            child = _read_backup_file_no_follow(repo, path)
            if child == parent:
                if indexed != parent_blob:
                    # update-ref is the commit durability point. If the later
                    # best-effort real-index sync was interrupted, the exact
                    # HEAD worktree proves which managed entry is safe to heal.
                    index_repairs[path] = parent_blob
                continue

            try:
                # The normal case: a complete, uncommitted append remains in
                # place for add_commit(); never replace it with HEAD.
                _strict_appended_lines(parent, child)
                continue
            except GitError:
                pass

            if indexed != parent_blob:
                raise GitError("managed backup worktree is not owned by HEAD")
            # A crash after an atomic replace can leave a missing/older
            # directory entry on platforms without directory fsync. Restore
            # only a complete validated prefix of the exact durable HEAD blob.
            jsonl_store.latest_clip_lines_bytes(child, ())
            jsonl_store.latest_clip_lines_bytes(parent, ())
            _strict_appended_lines(child, parent)
            worktree_repairs[path] = parent
        except (GitError, OSError, RuntimeError, TypeError, ValueError):
            raise GitWorktreeRecoveryRequired(
                "managed backup worktree requires recovery"
            ) from None

    # Validate every dirty managed path before changing any of them. Repairs
    # are intentionally path-scoped; a repository-wide read-tree would destroy
    # unrelated staged state.
    try:
        for path, content in sorted(worktree_repairs.items()):
            jsonl_store.replace_file_contents(repo, path, content)
        if index_repairs:
            _sync_real_index(repo, branch_ref, index_repairs)
    except (GitError, OSError, RuntimeError, TypeError, ValueError):
        raise GitWorktreeRecoveryRequired(
            "managed backup worktree requires recovery"
        ) from None


def _strict_appended_lines(parent: bytes, child: bytes) -> list[str]:
    if (
        child == parent
        or not child.startswith(parent)
        or (parent and not parent.endswith(b"\n"))
    ):
        raise GitError("backup JSONL modification is not an append")
    suffix = child[len(parent):]
    if not suffix.endswith(b"\n"):
        raise GitError("backup JSONL suffix is incomplete")
    raw_lines = suffix[:-1].split(b"\n")
    if not raw_lines or any(not line or b"\r" in line or b"\0" in line for line in raw_lines):
        raise GitError("backup JSONL suffix is invalid")
    try:
        return [line.decode("utf-8", errors="strict") for line in raw_lines]
    except UnicodeDecodeError:
        raise GitError("backup JSONL suffix is invalid") from None


def _visit_unpublished_lines(
    repo,
    base: str | None,
    candidate: str,
    visitor: Callable[[str, str], None],
) -> None:
    _assert_local_ancestor(repo, base, candidate)
    commits = _linear_unpublished_commits(repo, base, candidate)
    for parent, commit in commits:
        for status, path in _commit_changes(repo, parent, commit):
            child_blob = _tree_blob(repo, commit, path, required=True)
            if child_blob is None:  # pragma: no cover - required=True owns this
                raise GitError("backup tree entry inspection failed")
            if status == "A":
                if parent is not None and _tree_blob(
                    repo, parent, path, required=False
                ) is not None:
                    raise GitError("backup commit is not append-only")
                parent_bytes = b""
            else:
                if parent is None:
                    raise GitError("backup commit is not append-only")
                parent_blob = _tree_blob(repo, parent, path, required=True)
                if parent_blob is None:  # pragma: no cover - required=True owns this
                    raise GitError("backup tree entry inspection failed")
                parent_bytes = _blob_bytes(repo, parent_blob)
            child_bytes = _blob_bytes(repo, child_blob)
            for line in _strict_appended_lines(parent_bytes, child_bytes):
                visitor(path, line)


def prepare_push(repo, remote: str = "origin") -> PushCandidate:
    """Snapshot the exact unpublished branch and its exact remote base."""

    try:
        resolved_repo = str(Path(repo).resolve(strict=True))
        branch_ref = _symbolic_branch_ref(repo)
        candidate_sha = _local_ref_commit(repo, branch_ref)
        audited_head = _assert_backup_only_history(repo, branch_ref)
        if audited_head != candidate_sha:
            raise GitError("backup history validation failed")
        target = _resolved_push_target(repo, remote)
        remote_base = _remote_branch_tip(repo, target, branch_ref)
    except (GitError, OSError, RuntimeError) as exc:
        raise GitPushError(
            "backup push candidate preparation failed",
            getattr(exc, "returncode", None),
        ) from None
    return PushCandidate(
        resolved_repo,
        target,
        branch_ref,
        remote_base,
        candidate_sha,
    )


def inspect_unpublished_lines(
    candidate: PushCandidate,
    *,
    visitor: Callable[[str, str], None],
) -> None:
    """Structurally inspect every unpublished append without authorizing push.

    This deliberately returns no authorization token. The backup worker uses it
    to decide whether a semantically contaminated *local-only* suffix can be
    rebuilt from the exact remote base. A malformed or non-append history still
    fails closed and is never auto-rewritten.
    """

    if not isinstance(candidate, PushCandidate) or not callable(visitor):
        raise GitPushError("backup candidate inspection failed")
    try:
        _visit_unpublished_lines(
            candidate._repo,
            candidate.remote_base,
            candidate.candidate_sha,
            visitor,
        )
    except Exception as exc:
        raise GitPushError(
            "backup candidate inspection failed",
            getattr(exc, "returncode", None),
        ) from None


def inspect_published_lines(
    candidate: PushCandidate,
    *,
    visitor: Callable[[str, str], None],
) -> None:
    """Inspect the complete exact remote-base ancestry without mutating it."""

    if not isinstance(candidate, PushCandidate) or not callable(visitor):
        raise OwnerRemediationRequired(
            "published backup history requires owner remediation"
        )
    if candidate.remote_base is None:
        return
    try:
        _visit_unpublished_lines(
            candidate._repo,
            None,
            candidate.remote_base,
            visitor,
        )
    except Exception as exc:
        raise OwnerRemediationRequired(
            "published backup history requires owner remediation",
            getattr(exc, "returncode", None),
        ) from None


def authorize_push(
    candidate: PushCandidate,
    *,
    validator: Callable[[str, str], bool],
) -> PushAuthorization:
    """Validate every complete line added by every unpublished commit."""

    if not isinstance(candidate, PushCandidate) or not callable(validator):
        raise GitPushError("backup push authorization failed")
    repo = candidate._repo
    try:
        def validate(path: str, line: str) -> None:
            try:
                accepted = validator(path, line)
            except Exception:
                raise GitError("backup line validation failed") from None
            if accepted is not True:
                raise GitError("backup line validation failed")

        _visit_unpublished_lines(
            repo,
            candidate.remote_base,
            candidate.candidate_sha,
            validate,
        )
    except GitError as exc:
        raise GitPushError(
            "backup push authorization failed",
            exc.returncode,
        ) from None
    return PushAuthorization(candidate, _AUTHORIZATION_SEAL)


def _tree_blobs(repo, treeish: str) -> dict[str, str]:
    result = _run(
        repo,
        ["ls-tree", "-r", "-z", treeish],
        input_bytes=b"",
    )
    raw = _raw_stdout(result, "backup tree enumeration failed")
    entries = raw.split(b"\0")
    if entries and entries[-1] == b"":
        entries.pop()
    blobs: dict[str, str] = {}
    for entry in entries:
        try:
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, raw_object_id = metadata.split(b" ", 2)
            path = jsonl_store.validated_daily_relpath(
                raw_path.decode("utf-8", errors="strict")
            )
            object_id = raw_object_id.decode("ascii", errors="strict")
        except (UnicodeError, TypeError, ValueError):
            raise GitError("backup tree enumeration failed") from None
        if (
            mode != b"100644"
            or kind != b"blob"
            or _OBJECT_ID_RE.fullmatch(object_id) is None
            or path in blobs
        ):
            raise GitError("backup tree enumeration failed")
        blobs[path] = object_id
    return blobs


def _sync_real_index_exact(
    repo,
    branch_ref: str,
    changed_paths: set[str],
    new_blobs: Mapping[str, str],
) -> None:
    current_ref = _run(repo, ["symbolic-ref", "--quiet", "HEAD"])
    if (
        current_ref.returncode != 0
        or current_ref.stdout.strip() != branch_ref
    ):
        raise GitError("backup branch changed during recovery", current_ref.returncode)
    for path in sorted(changed_paths):
        object_id = new_blobs.get(path)
        args = (
            _cacheinfo_args(path, object_id)
            if object_id is not None
            else ["update-index", "--force-remove", "--", path]
        )
        result = _run(repo, args)
        if result.returncode != 0:
            raise GitError("git index recovery failed", result.returncode)


def _index_blob(repo, path: str) -> str | None:
    result = _run(repo, ["ls-files", "--stage", "-z", "--", path])
    raw = _raw_stdout(result, "git index recovery inspection failed")
    if raw == b"":
        return None
    if not raw.endswith(b"\0") or raw.count(b"\0") != 1:
        raise GitError("git index recovery inspection failed")
    try:
        metadata, raw_path = raw[:-1].split(b"\t", 1)
        mode, raw_object_id, stage = metadata.split(b" ", 2)
        indexed_path = raw_path.decode("utf-8", errors="strict")
        object_id = raw_object_id.decode("ascii", errors="strict")
    except (UnicodeError, ValueError):
        raise GitError("git index recovery inspection failed") from None
    if (
        mode != b"100644"
        or stage != b"0"
        or indexed_path != path
        or _OBJECT_ID_RE.fullmatch(object_id) is None
    ):
        raise GitError("git index recovery inspection failed")
    return object_id


def _assert_recovery_paths_owned(
    repo,
    changed_paths: set[str],
    old_blobs: Mapping[str, str],
    new_blobs: Mapping[str, str],
) -> None:
    """Allow only the old or already-scrubbed state for each touched path."""

    for path in sorted(changed_paths):
        allowed_blob_ids = {
            blob
            for blob in (old_blobs.get(path), new_blobs.get(path))
            if blob is not None
        }
        absence_allowed = path not in old_blobs or path not in new_blobs

        indexed = _index_blob(repo, path)
        if indexed is None:
            if not absence_allowed:
                raise GitError("managed backup index has unrelated changes")
        elif indexed not in allowed_blob_ids:
            raise GitError("managed backup index has unrelated changes")

        try:
            target = jsonl_store.daily_target_path(repo, path)
            info = target.lstat()
        except FileNotFoundError:
            if not absence_allowed:
                raise GitError("managed backup worktree has unrelated changes")
            continue
        except (OSError, RuntimeError, TypeError, ValueError):
            raise GitError("managed backup worktree has unrelated changes") from None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise GitError("managed backup worktree has unrelated changes")
        content = _read_backup_file_no_follow(repo, path)
        allowed_contents = {
            _blob_bytes(repo, object_id)
            for object_id in allowed_blob_ids
        }
        if content not in allowed_contents:
            raise GitError("managed backup worktree has unrelated changes")


def _assert_recovery_paths_exact(
    repo,
    changed_paths: set[str],
    new_blobs: Mapping[str, str],
) -> None:
    for path in sorted(changed_paths):
        expected_blob = new_blobs.get(path)
        if _index_blob(repo, path) != expected_blob:
            raise GitError("managed backup index recovery verification failed")
        target = jsonl_store.daily_target_path(repo, path)
        if expected_blob is None:
            try:
                target.lstat()
            except FileNotFoundError:
                continue
            raise GitError("managed backup worktree recovery verification failed")
        try:
            content = _read_backup_file_no_follow(repo, path)
        except GitError:
            raise GitError("managed backup worktree recovery verification failed") from None
        if content != _blob_bytes(repo, expected_blob):
            raise GitError("managed backup worktree recovery verification failed")


def _rebuild_unpublished_candidate(
    candidate: PushCandidate,
    replacements: Mapping[str, Sequence[str]],
) -> str | None:
    """Replace only the local unpublished suffix with a safe linear candidate.

    The exact published remote base is retained and is never rewritten. Invalid
    local commits become unreachable from the branch; only a new append-only
    candidate (or the published base itself) remains eligible for exact-ref push.
    """

    if not isinstance(candidate, PushCandidate) or not isinstance(replacements, Mapping):
        raise GitPushError("backup candidate recovery failed")
    repo = candidate._repo
    try:
        if _local_ref_commit(repo, candidate.branch_ref) != candidate.candidate_sha:
            raise GitError("backup branch moved before recovery")
        if _remote_branch_tip(repo, candidate._target, candidate.branch_ref) != candidate.remote_base:
            raise GitError("backup remote branch moved before recovery")
        if _assert_backup_only_history(repo, candidate.branch_ref) != candidate.candidate_sha:
            raise GitError("backup history changed before recovery")

        # Recovery is allowed only for a structurally valid append-only suffix.
        # Malformed/manual history remains fail-closed for owner inspection.
        def validate_existing(path: str, line: str) -> None:
            try:
                clip = jsonl_store.deserialize_clip(line)
                if (
                    jsonl_store.serialize_clip(clip) != line
                    or jsonl_store.daily_relpath(clip.created_at) != path
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise GitError("backup recovery source validation failed") from None

        _visit_unpublished_lines(
            repo,
            candidate.remote_base,
            candidate.candidate_sha,
            validate_existing,
        )

        validated_replacements: dict[str, list[str]] = {}
        path_by_id: dict[str, str] = {}
        for raw_path, raw_lines in sorted(replacements.items()):
            path = jsonl_store.validated_daily_relpath(raw_path)
            if not isinstance(raw_lines, Sequence) or isinstance(raw_lines, (str, bytes)):
                raise GitError("backup recovery replacement validation failed")
            lines: list[str] = []
            for line in raw_lines:
                try:
                    clip = jsonl_store.deserialize_clip(line)
                    if (
                        clip.is_secret
                        or jsonl_store.serialize_clip(clip) != line
                        or jsonl_store.daily_relpath(clip.created_at) != path
                        or (
                            clip.id in path_by_id
                            and path_by_id[clip.id] != path
                        )
                    ):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    raise GitError("backup recovery replacement validation failed") from None
                path_by_id[clip.id] = path
                lines.append(line)
            if lines:
                validated_replacements[path] = lines

        base = candidate.remote_base
        if base is not None:
            _assert_local_ancestor(repo, base, candidate.candidate_sha)
            base_tree = _object_id(
                _run(repo, ["rev-parse", f"{base}^{{tree}}"]),
                "backup base tree lookup failed",
            )
        else:
            base_tree = _empty_tree(repo)

        with tempfile.TemporaryDirectory(prefix="clipvault-backup-recovery-") as temp_dir:
            index_env = {"GIT_INDEX_FILE": str(Path(temp_dir) / "index")}
            read_result = _run(
                repo,
                ["read-tree", base] if base is not None else ["read-tree", "--empty"],
                env=index_env,
            )
            if read_result.returncode != 0:
                raise GitError("backup recovery index initialization failed", read_result.returncode)

            for path, lines in sorted(validated_replacements.items()):
                base_blob = (
                    _tree_blob(repo, base, path, required=False)
                    if base is not None
                    else None
                )
                base_bytes = _blob_bytes(repo, base_blob) if base_blob is not None else b""
                rebuilt_bytes = jsonl_store.append_latest_clip_states_bytes(
                    base_bytes,
                    lines,
                )
                if rebuilt_bytes == base_bytes:
                    continue
                object_id = _object_id(
                    _run(
                        repo,
                        ["hash-object", "-w", "--no-filters", "--stdin"],
                        input_bytes=rebuilt_bytes,
                    ),
                    "backup recovery object write failed",
                )
                update_result = _run(
                    repo,
                    _cacheinfo_args(path, object_id),
                    env=index_env,
                )
                if update_result.returncode != 0:
                    raise GitError("backup recovery index update failed", update_result.returncode)

            tree = _object_id(
                _run(repo, ["write-tree"], env=index_env),
                "backup recovery tree write failed",
            )

        if tree == base_tree:
            recovered_tip = base
        else:
            commit_args = ["commit-tree", tree]
            if base is not None:
                commit_args.extend(["-p", base])
            commit_args.extend(["-m", "backup: rebuild unpublished safe state"])
            recovered_tip = _object_id(
                _run(repo, commit_args),
                "backup recovery commit write failed",
            )

        old_blobs = _tree_blobs(repo, candidate.candidate_sha)
        new_blobs = _tree_blobs(repo, tree)
        changed_paths = {
            path
            for path in set(old_blobs) | set(new_blobs)
            if old_blobs.get(path) != new_blobs.get(path)
        }
        if changed_paths:
            _assert_no_content_attributes(repo, sorted(changed_paths))
            _assert_recovery_paths_owned(
                repo,
                changed_paths,
                old_blobs,
                new_blobs,
            )
        if _local_ref_commit(repo, candidate.branch_ref) != candidate.candidate_sha:
            raise GitError("backup branch moved during recovery")
        # Put the managed worktree and index in the safe state before moving the
        # branch. A crash can therefore leave the old branch dirty but can never
        # make the new safe branch point at a contaminated worktree snapshot.
        for path in sorted(changed_paths):
            blob = new_blobs.get(path)
            data = _blob_bytes(repo, blob) if blob is not None else None
            jsonl_store.replace_file_contents(repo, path, data)
        _sync_real_index_exact(
            repo,
            candidate.branch_ref,
            changed_paths,
            new_blobs,
        )
        _assert_recovery_paths_exact(repo, changed_paths, new_blobs)
        if _remote_branch_tip(
            repo,
            candidate._target,
            candidate.branch_ref,
        ) != candidate.remote_base:
            raise GitError("backup remote branch moved during recovery")

        if recovered_tip is None:
            update_result = _run(
                repo,
                ["update-ref", "-d", candidate.branch_ref, candidate.candidate_sha],
            )
        else:
            update_result = _run(
                repo,
                [
                    "update-ref",
                    candidate.branch_ref,
                    recovered_tip,
                    candidate.candidate_sha,
                ],
            )
        if update_result.returncode != 0:
            raise GitError("backup branch recovery failed", update_result.returncode)
        return recovered_tip
    except (GitError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise GitPushError(
            "backup candidate recovery failed",
            getattr(exc, "returncode", None),
        ) from None


def add_commit(
    repo,
    message: str,
    *,
    paths: Sequence[str],
) -> str | None:
    """Commit raw JSONL bytes without filters or unrelated index state."""

    validated = _validated_backup_paths(paths)
    branch_ref = _symbolic_branch_ref(repo)
    head = _assert_backup_only_history(repo, branch_ref)
    _assert_no_content_attributes(repo, validated)

    with tempfile.TemporaryDirectory(prefix="clipvault-backup-index-") as temp_dir:
        index_env = {"GIT_INDEX_FILE": str(Path(temp_dir) / "index")}
        read_tree_args = (
            ["read-tree", head]
            if head is not None
            else ["read-tree", "--empty"]
        )
        result = _run(repo, read_tree_args, env=index_env)
        if result.returncode != 0:
            raise GitError("git temporary index initialization failed", result.returncode)

        blobs: dict[str, str] = {}
        for path in validated:
            content = _read_backup_file_no_follow(repo, path)
            parent_blob = (
                _tree_blob(repo, head, path, required=False)
                if head is not None
                else None
            )
            parent_content = (
                _blob_bytes(repo, parent_blob)
                if parent_blob is not None
                else b""
            )
            if content != parent_content:
                try:
                    _strict_appended_lines(parent_content, content)
                except GitError:
                    raise GitWorktreeRecoveryRequired(
                        "managed backup worktree requires recovery"
                    ) from None
            object_result = _run(
                repo,
                ["hash-object", "-w", "--no-filters", "--stdin"],
                input_bytes=content,
            )
            blobs[path] = _object_id(object_result, "git raw object write failed")
        for path, blob in blobs.items():
            result = _run(repo, _cacheinfo_args(path, blob), env=index_env)
            if result.returncode != 0:
                raise GitError("git temporary index update failed", result.returncode)

        tree = _object_id(
            _run(repo, ["write-tree"], env=index_env),
            "git tree write failed",
        )
        if head is not None:
            head_tree = _object_id(
                _run(repo, ["rev-parse", f"{head}^{{tree}}"]),
                "git head tree lookup failed",
            )
            if tree == head_tree:
                _sync_real_index(repo, branch_ref, blobs)
                return None

        commit_args = ["commit-tree", tree]
        if head is not None:
            commit_args.extend(["-p", head])
        commit_args.extend(["-m", message])
        committed = _object_id(
            _run(repo, commit_args),
            "git commit object write failed",
        )
        expected_head = head if head is not None else "0" * len(committed)
        result = _run(repo, ["update-ref", branch_ref, committed, expected_head])
        if result.returncode != 0:
            raise GitError("git branch update failed", result.returncode)
        try:
            _sync_real_index(repo, branch_ref, blobs)
        except GitError:
            # update-ref is the durability boundary.  Reporting failure after it
            # succeeded makes a retry look like a no-op and can strand the queue.
            # The isolated-index commit is valid even if this best-effort status
            # cleanup must wait for a later managed-path update.
            pass
        return committed


def push(
    authorization: PushAuthorization,
    *,
    final_validator: Callable[[PushCandidate], bool] | None = None,
) -> bool:
    """Publish exactly one authorized candidate, or safely no-op if present.

    Returns True only when this call executed a successful network/local-remote
    push.  A remote already at the candidate is a successful False no-op.
    """

    if (
        not isinstance(authorization, PushAuthorization)
        or authorization._seal is not _AUTHORIZATION_SEAL
    ):
        raise GitPushError("backup push authorization required")
    if final_validator is not None and not callable(final_validator):
        raise GitPushError("backup final validation failed")
    candidate = authorization.candidate
    repo = candidate._repo
    try:
        local_tip = _local_ref_commit(repo, candidate.branch_ref)
        if local_tip != candidate.candidate_sha:
            raise GitError("backup branch moved after authorization")
        remote_tip = _remote_branch_tip(repo, candidate._target, candidate.branch_ref)
    except GitError as exc:
        raise GitPushError(
            "backup push preflight failed",
            exc.returncode,
        ) from None
    if remote_tip == candidate.candidate_sha:
        return False
    if remote_tip != candidate.remote_base:
        raise GitPushError("backup remote branch moved after authorization")
    if final_validator is not None:
        try:
            accepted = final_validator(candidate)
        except Exception:
            raise GitPushError("backup final validation failed") from None
        if accepted is not True:
            raise GitPushError("backup final validation failed")
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
            candidate._target,
            f"{candidate.candidate_sha}:{candidate.branch_ref}",
        ],
    )
    if result.returncode != 0:
        raise GitPushError("git push failed", result.returncode)
    return True


def has_remote(repo, name: str = "origin") -> bool:
    result = _run(repo, ["remote"])
    return name in result.stdout.split()
