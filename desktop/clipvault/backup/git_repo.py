"""Thin git CLI wrapper for the backup repo (GHB-1).

Deliberately offers only append-style operations. There is NO pull, force,
rebase, or amend function — the backup repo is an append-only log
(ADR-0003, GATES G3). test_backup_git.py::test_c7 enforces this absence.
"""

import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from clipvault.backup import jsonl_store

_TIMEOUT = 60
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
    process_env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    if env:
        process_env.update(env)
    try:
        text_mode = input_bytes is None
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL if text_mode else None,
            input=input_bytes,
            capture_output=True,
            text=text_mode,
            encoding="utf-8" if text_mode else None,
            errors="surrogateescape" if text_mode else None,
            timeout=timeout,
            env=process_env,
        )
        if input_bytes is None:
            return result
        return subprocess.CompletedProcess(
            result.args,
            result.returncode,
            stdout=result.stdout.decode("utf-8", errors="surrogateescape"),
            stderr=result.stderr.decode("utf-8", errors="replace"),
        )
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


def push(repo, remote: str = "origin") -> None:
    # Push the current branch explicitly so behaviour never depends on
    # push.default, a configured upstream, or remote-specific ref settings.
    try:
        branch_ref = _symbolic_branch_ref(repo)
        verified_head = _assert_backup_only_history(repo, branch_ref)
    except GitError as exc:
        raise GitPushError(
            "backup history validation failed",
            exc.returncode,
        ) from None
    if verified_head is None:
        raise GitPushError("backup history validation failed")
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
            f"{verified_head}:{branch_ref}",
        ],
    )
    if result.returncode != 0:
        raise GitPushError("git push failed", result.returncode)


def has_remote(repo, name: str = "origin") -> bool:
    result = _run(repo, ["remote"])
    return name in result.stdout.split()
