#!/usr/bin/env python3
"""Validate v1.6 release artifacts and render Issue #36 evidence.

The default mode remains a local structural precheck for compatibility. This tool
does not download GitHub Actions artifacts. The explicit live-final-draft mode
additionally verifies GitHub run metadata,
artifact attestations, draft Release bytes, and the Android signer. Neither mode
performs manual QA, publishes a Release, posts to GitHub, or closes Issue #36.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "selinyi123/clipvault-personal"
DEFAULT_VERSION = "v1.6.0"
ISSUE_NUMBER = 36
BRANCH = "main"
SOURCE_REF = "refs/heads/main"
WORKFLOW_NAME = "Release artifact build"
WORKFLOW_PATH = ".github/workflows/release.yml"
RELEASE_ENVIRONMENT = "release"
RELEASE_CERT_VARIABLE = "ANDROID_RELEASE_CERT_SHA256"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
OIDC_ISSUER = "https://token.actions.githubusercontent.com"
MAX_COMMAND_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_APKSIGNER_OUTPUT_BYTES = 64 * 1024
VERSION_RE = re.compile(r"^v(?P<numeric>[0-9]+\.[0-9]+\.[0-9]+)$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_URL_RE = re.compile(
    r"^https://github\.com/(?P<repo>[^/\s]+/[^/\s]+)/actions/runs/(?P<run_id>[0-9]+)$"
)


def _load_source_module(name: str, script: Path):
    """Load trusted repository source without consulting ignored bytecode caches."""

    candidate = script
    if candidate.is_symlink():
        raise RuntimeError(f"trusted source must not be a symlink: {candidate}")
    script = candidate.resolve(strict=True)
    if not script.is_file():
        raise RuntimeError(f"trusted source must be a regular file: {script}")
    module = types.ModuleType(name)
    module.__file__ = str(script)
    module.__package__ = ""
    module.__cached__ = None
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        code = compile(script.read_bytes(), str(script), "exec", dont_inherit=True)
        exec(code, module.__dict__)
    except BaseException:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module


def _load_verify_release_manifest():
    script = ROOT / "scripts" / "verify_release_manifest.py"
    return _load_source_module("verify_release_manifest", script)


verify_release_manifest = _load_verify_release_manifest()


@dataclass(frozen=True)
class AssetSpec:
    role: str
    platform: str
    workflow_bundle: str
    workflow_name: str
    release_name: str


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def run(self, argv: Sequence[str], *, timeout: int) -> CommandResult: ...


class SubprocessRunner:
    """Execute the fixed read-only command set without a shell."""

    def run(self, argv: Sequence[str], *, timeout: int) -> CommandResult:
        env = os.environ.copy()
        for name in (
            "GH_FORCE_TTY",
            "JAVA_TOOL_OPTIONS",
            "_JAVA_OPTIONS",
            "JDK_JAVA_OPTIONS",
            "CLASSPATH",
        ):
            env.pop(name, None)
        env.update({
            "GH_PROMPT_DISABLED": "1",
            "GH_PAGER": "",
            "PAGER": "",
            "NO_COLOR": "1",
        })
        try:
            completed = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                shell=False,
                timeout=timeout,
                env=env,
                cwd=Path(str(argv[0])).resolve().parent,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
            raise ValueError("external verification command could not be executed") from exc
        if len(completed.stdout.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES:
            raise ValueError("external verification command output is too large")
        if len(completed.stderr.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES:
            raise ValueError("external verification command error output is too large")
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _asset_specs(version: str) -> tuple[AssetSpec, ...]:
    numeric = numeric_version(version)
    return (
        AssetSpec(
            "windows_portable",
            "windows",
            "clipvault-windows-release-artifacts",
            f"ClipVault-Desktop-v{numeric}-portable.exe",
            f"ClipVault-Desktop-v{numeric}-portable.exe",
        ),
        AssetSpec(
            "windows_installer",
            "windows",
            "clipvault-windows-release-artifacts",
            f"ClipVault-Setup-v{numeric}.exe",
            f"ClipVault-Setup-v{numeric}.exe",
        ),
        AssetSpec(
            "windows_checksums",
            "windows",
            "clipvault-windows-release-artifacts",
            "SHA256SUMS.txt",
            "windows-SHA256SUMS.txt",
        ),
        AssetSpec(
            "windows_manifest",
            "windows",
            "clipvault-windows-release-artifacts",
            "RELEASE_MANIFEST.json",
            "windows-RELEASE_MANIFEST.json",
        ),
        AssetSpec(
            "android_signed_apk",
            "android",
            "clipvault-android-signed-release-artifacts",
            f"ClipVault-Android-{version}-release-signed.apk",
            f"ClipVault-Android-{version}-release-signed.apk",
        ),
        AssetSpec(
            "android_apksigner_evidence",
            "android",
            "clipvault-android-signed-release-artifacts",
            "ANDROID_APKSIGNER_VERIFY.txt",
            "ANDROID_APKSIGNER_VERIFY.txt",
        ),
        AssetSpec(
            "android_checksums",
            "android",
            "clipvault-android-signed-release-artifacts",
            "SHA256SUMS.txt",
            "android-SHA256SUMS.txt",
        ),
        AssetSpec(
            "android_manifest",
            "android",
            "clipvault-android-signed-release-artifacts",
            "RELEASE_MANIFEST.json",
            "android-RELEASE_MANIFEST.json",
        ),
    )


def numeric_version(version: str) -> str:
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise ValueError("version must be a release tag like v1.6.0")
    return match.group("numeric")


def validate_commit(commit: str) -> str:
    commit = commit.strip()
    if not COMMIT_RE.fullmatch(commit):
        raise ValueError("commit must be a full 40-character lowercase hex SHA")
    return commit


def validate_run_url(run_url: str, repo: str) -> str:
    run_url = run_url.strip()
    match = RUN_URL_RE.fullmatch(run_url)
    if not match:
        raise ValueError("run-url must be a GitHub Actions run URL")
    if match.group("repo") != repo:
        raise ValueError(f"run-url repo mismatch: expected {repo!r}")
    return run_url


def _run_id(run_url: str, repo: str) -> int:
    validated = validate_run_url(run_url, repo)
    match = RUN_URL_RE.fullmatch(validated)
    assert match is not None
    return int(match.group("run_id"))


def _artifact_names(manifest: dict[str, Any]) -> list[str]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return []
    names: list[str] = []
    for row in artifacts:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            names.append(row["name"])
    return sorted(names)


def _verify_platform_dir(
    artifact_dir: Path,
    *,
    platform: str,
    version: str,
    commit: str,
    require_signed: bool = False,
    expected_android_cert_sha256: str | None = None,
) -> dict[str, Any]:
    manifest = verify_release_manifest.verify_manifest(
        artifact_dir,
        platform=platform,
        version=version,
        commit=commit,
        require_signed=require_signed,
        expected_android_cert_sha256=expected_android_cert_sha256,
    )
    if manifest.get("kind") != "release":
        raise ValueError(f"{platform} artifacts must have manifest kind=release")
    if platform == "android" and manifest.get("signed") is not True:
        raise ValueError("Android artifact manifest must record signed=true")
    return manifest


def validate_evidence(
    *,
    windows_dir: Path,
    android_dir: Path,
    version: str = DEFAULT_VERSION,
    commit: str,
    run_url: str,
    expected_android_cert_sha256: str,
    repo: str = DEFAULT_REPO,
) -> dict[str, Any]:
    """Run the backward-compatible local structural precheck."""

    numeric = numeric_version(version)
    commit = validate_commit(commit)
    run_url = validate_run_url(run_url, repo)
    owner_cert_sha256 = verify_release_manifest.normalize_android_cert_sha256(
        expected_android_cert_sha256
    )

    windows_manifest = _verify_platform_dir(
        windows_dir,
        platform="windows",
        version=numeric,
        commit=commit,
    )
    android_manifest = _verify_platform_dir(
        android_dir,
        platform="android",
        version=numeric,
        commit=commit,
        require_signed=True,
        expected_android_cert_sha256=owner_cert_sha256,
    )

    return {
        "status": "structural_precheck_pass",
        "repo": repo,
        "issue": ISSUE_NUMBER,
        "version": version,
        "numeric_version": numeric,
        "commit": commit,
        "run_url": run_url,
        "android_owner_cert_sha256": owner_cert_sha256,
        "windows_dir": str(windows_dir),
        "android_dir": str(android_dir),
        "windows_artifacts": _artifact_names(windows_manifest),
        "android_artifacts": _artifact_names(android_manifest),
        "scope_note": (
            "This structural precheck validates downloaded artifact directories and "
            "binds captured signer evidence to the supplied Owner certificate. It does "
            "not verify GitHub run provenance. It does not replace manual QA evidence, "
            "release environment policy, Owner approval, or final GitHub Release publication."
        ),
    }


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _decode_json(text: str, *, label: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_object_pairs)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} did not return valid unambiguous JSON") from exc


def _run_json(runner: Runner, argv: Sequence[str], *, label: str, timeout: int = 60) -> Any:
    result = runner.run(argv, timeout=timeout)
    if result.returncode != 0:
        raise ValueError(f"{label} failed")
    if len(result.stdout.encode("utf-8")) > MAX_COMMAND_OUTPUT_BYTES:
        raise ValueError(f"{label} output is too large")
    return _decode_json(result.stdout, label=label)


def _gh_get(runner: Runner, gh: Path, endpoint: str, *, label: str) -> Any:
    return _run_json(
        runner,
        [str(gh), "api", "-X", "GET", "--hostname", "github.com", endpoint],
        label=label,
    )


def _require_dict(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_positive_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _normalize_api_digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError(f"{label} must be a sha256 digest")
    digest = value.removeprefix("sha256:")
    if SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{label} must be a canonical lowercase sha256 digest")
    return digest


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _regular_file_row(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"release artifact must be a regular non-symlink file: {path.name!r}")
    size, digest = _sha256_file(path)
    if size <= 0:
        raise ValueError(f"release artifact must not be empty: {path.name!r}")
    return {"name": path.name, "size_bytes": size, "sha256": digest}


def _exact_directory_rows(directory: Path, expected_names: set[str]) -> dict[str, dict[str, Any]]:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("artifact directory must be a regular directory")
    actual_names = {entry.name for entry in directory.iterdir()}
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        raise ValueError(f"artifact directory inventory mismatch; missing={missing!r} extra={extra!r}")
    return {name: _regular_file_row(directory / name) for name in sorted(expected_names)}


def _local_asset_rows(
    *,
    windows_dir: Path,
    android_dir: Path,
    draft_release_dir: Path,
    specs: Sequence[AssetSpec],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    platform_dirs = {"windows": windows_dir, "android": android_dir}
    action_rows: dict[str, dict[str, Any]] = {}
    for platform, directory in platform_dirs.items():
        names = {spec.workflow_name for spec in specs if spec.platform == platform}
        rows = _exact_directory_rows(directory, names)
        for spec in specs:
            if spec.platform == platform:
                action_rows[spec.role] = rows[spec.workflow_name]

    release_names = {spec.release_name for spec in specs}
    release_by_name = _exact_directory_rows(draft_release_dir, release_names)
    release_rows = {spec.role: release_by_name[spec.release_name] for spec in specs}
    for spec in specs:
        action = action_rows[spec.role]
        release = release_rows[spec.role]
        if (action["size_bytes"], action["sha256"]) != (
            release["size_bytes"],
            release["sha256"],
        ):
            raise ValueError(f"Actions and draft Release bytes differ for {spec.release_name!r}")
    return action_rows, release_rows


def _validate_main_record(record: Any, *, commit: str) -> dict[str, Any]:
    data = _require_dict(record, label="main branch response")
    branch_commit = _require_dict(data.get("commit"), label="main branch commit")
    if branch_commit.get("sha") != commit:
        raise ValueError("target commit is not the current main commit")
    return {"sha": commit}


def _lookup_release_tag_state(
    runner: Runner,
    gh: Path,
    *,
    repo: str,
    version: str,
) -> dict[str, Any]:
    """Resolve the exact release tag through annotated tags, or report absence."""

    exact_ref = f"refs/tags/{version}"
    endpoint = f"repos/{repo}/git/matching-refs/tags/{version}"
    records = _gh_get(runner, gh, endpoint, label="release tag ref lookup")
    if not isinstance(records, list):
        raise ValueError("release tag ref lookup did not return a list")
    exact: list[dict[str, Any]] = []
    for index, raw in enumerate(records):
        row = _require_dict(raw, label=f"release tag ref #{index}")
        ref = row.get("ref")
        if not isinstance(ref, str):
            raise ValueError("release tag ref is missing its ref name")
        if ref == exact_ref:
            exact.append(row)
    if not exact:
        return {"ref": exact_ref, "state": "absent", "commit_sha": None}
    if len(exact) != 1:
        raise ValueError("release tag lookup returned duplicate exact refs")

    obj = _require_dict(exact[0].get("object"), label="release tag object")
    seen: set[str] = set()
    for _ in range(8):
        object_type = obj.get("type")
        object_sha = obj.get("sha")
        if not isinstance(object_sha, str) or COMMIT_RE.fullmatch(object_sha) is None:
            raise ValueError("release tag object has an invalid Git SHA")
        if object_type == "commit":
            return {"ref": exact_ref, "state": "present", "commit_sha": object_sha}
        if object_type != "tag":
            raise ValueError("release tag does not resolve to a commit")
        if object_sha in seen:
            raise ValueError("release tag contains an object cycle")
        seen.add(object_sha)
        tag_record = _require_dict(
            _gh_get(
                runner,
                gh,
                f"repos/{repo}/git/tags/{object_sha}",
                label="annotated release tag lookup",
            ),
            label="annotated release tag",
        )
        obj = _require_dict(tag_record.get("object"), label="annotated release tag object")
    raise ValueError("release tag annotation chain is too deep")


def _validate_release_tag_state(state: dict[str, Any], *, commit: str) -> dict[str, Any]:
    if state["state"] == "present" and state["commit_sha"] != commit:
        raise ValueError("release tag points to a different commit")
    return state


def _validate_release_cert_variable(
    record: Any, *, owner_cert_sha256: str
) -> dict[str, str]:
    data = _require_dict(record, label="release environment certificate variable")
    if data.get("name") != RELEASE_CERT_VARIABLE:
        raise ValueError("release environment certificate variable name mismatch")
    raw_value = data.get("value")
    if not isinstance(raw_value, str):
        raise ValueError("release environment certificate variable value is invalid")
    live_cert = verify_release_manifest.normalize_android_cert_sha256(raw_value)
    if live_cert != owner_cert_sha256:
        raise ValueError("release environment and Owner Android certificates must match")
    return {
        "environment": RELEASE_ENVIRONMENT,
        "name": RELEASE_CERT_VARIABLE,
        "value": live_cert,
    }


def _validate_run_record(
    record: Any,
    *,
    run_id: int,
    run_url: str,
    repo: str,
    version: str,
    commit: str,
) -> dict[str, Any]:
    data = _require_dict(record, label="workflow run response")
    expected = {
        "id": run_id,
        "html_url": run_url,
        "name": WORKFLOW_NAME,
        "path": WORKFLOW_PATH,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_branch": BRANCH,
        "head_sha": commit,
        "display_title": f"Release artifacts {version} from main draft=true",
    }
    for key, value in expected.items():
        if data.get(key) != value:
            raise ValueError(f"workflow run {key} mismatch")
    head_repo = _require_dict(data.get("head_repository"), label="workflow run head repository")
    if head_repo.get("full_name") != repo:
        raise ValueError("workflow run head repository mismatch")
    attempt = _require_positive_int(data.get("run_attempt"), label="workflow run attempt")
    return {
        "id": run_id,
        "url": run_url,
        "attempt": attempt,
        "workflow": WORKFLOW_NAME,
        "path": WORKFLOW_PATH,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_branch": BRANCH,
        "head_sha": commit,
        "display_title": expected["display_title"],
    }


def _validate_workflow_artifacts(record: Any) -> list[dict[str, Any]]:
    data = _require_dict(record, label="workflow artifacts response")
    rows = data.get("artifacts")
    if data.get("total_count") != 2 or not isinstance(rows, list) or len(rows) != 2:
        raise ValueError("workflow run must contain exactly two artifact bundles")
    expected = {
        "clipvault-windows-release-artifacts",
        "clipvault-android-signed-release-artifacts",
    }
    names = [row.get("name") for row in rows if isinstance(row, dict)]
    if len(names) != 2 or set(names) != expected or len(set(names)) != 2:
        raise ValueError("workflow artifact bundle inventory mismatch")
    result: list[dict[str, Any]] = []
    for row in rows:
        item = _require_dict(row, label="workflow artifact bundle")
        if item.get("expired") is not False:
            raise ValueError("workflow artifact bundle is expired")
        size = _require_positive_int(item.get("size_in_bytes"), label="workflow artifact size")
        result.append({
            "id": _require_positive_int(item.get("id"), label="workflow artifact id"),
            "name": item["name"],
            "size_bytes": size,
            "api_archive_sha256": _normalize_api_digest(
                item.get("digest"), label="workflow artifact digest"
            ),
        })
    return sorted(result, key=lambda row: row["name"])


def _validate_draft_release_record(
    record: Any,
    *,
    repo: str,
    version: str,
    commit: str,
    release_rows: dict[str, dict[str, Any]],
    specs: Sequence[AssetSpec],
) -> dict[str, Any]:
    data = _require_dict(record, label="draft Release response")
    if data.get("tag_name") != version:
        raise ValueError("draft Release tag mismatch")
    if data.get("name") != f"ClipVault Personal {version}":
        raise ValueError("draft Release title mismatch")
    if data.get("draft") is not True or data.get("prerelease") is not False:
        raise ValueError("GitHub Release must be a non-prerelease draft")
    if data.get("target_commitish") != commit:
        raise ValueError("draft Release target commit mismatch")
    release_url = data.get("html_url")
    if not isinstance(release_url, str) or not release_url.startswith(
        f"https://github.com/{repo}/releases/"
    ):
        raise ValueError("draft Release URL mismatch")
    assets = data.get("assets")
    if not isinstance(assets, list) or len(assets) != len(specs):
        raise ValueError("draft Release asset inventory mismatch")
    by_name: dict[str, dict[str, Any]] = {}
    for raw in assets:
        asset = _require_dict(raw, label="draft Release asset")
        name = asset.get("name")
        if not isinstance(name, str) or name in by_name:
            raise ValueError("draft Release contains an invalid or duplicate asset name")
        by_name[name] = asset
    expected_names = {spec.release_name for spec in specs}
    if set(by_name) != expected_names:
        raise ValueError("draft Release asset inventory mismatch")

    asset_rows: dict[str, dict[str, Any]] = {}
    for spec in specs:
        live = by_name[spec.release_name]
        local = release_rows[spec.role]
        if live.get("state") != "uploaded":
            raise ValueError("draft Release asset is not fully uploaded")
        size = _require_positive_int(live.get("size"), label="draft Release asset size")
        digest = _normalize_api_digest(live.get("digest"), label="draft Release asset digest")
        if (size, digest) != (local["size_bytes"], local["sha256"]):
            raise ValueError(f"draft Release API bytes differ for {spec.release_name!r}")
        asset_rows[spec.role] = {
            "release_asset_id": _require_positive_int(
                live.get("id"), label="draft Release asset id"
            ),
            "size_bytes": size,
            "sha256": digest,
        }
    return {
        "id": _require_positive_int(data.get("id"), label="draft Release id"),
        "url": release_url,
        "tag_name": version,
        "name": f"ClipVault Personal {version}",
        "is_draft": True,
        "is_prerelease": False,
        "target_commitish": commit,
        "assets": asset_rows,
    }


def _select_draft_release(record: Any, *, version: str) -> dict[str, Any]:
    if not isinstance(record, list):
        raise ValueError("release listing must be a JSON array")
    matches = [
        row
        for row in record
        if isinstance(row, dict) and row.get("tag_name") == version
    ]
    if len(matches) != 1:
        raise ValueError("release listing must contain exactly one matching v1.6.0 draft")
    return matches[0]


def _validate_tool_path(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    if os.name == "nt":
        windows_path = str(path).replace("/", "\\")
        if windows_path.startswith(("\\\\?\\", "\\\\.\\")):
            raise ValueError(f"{label} path must not use a Windows device namespace")
        if windows_path.startswith("\\\\"):
            raise ValueError(f"{label} path must be on a local fixed drive, not UNC")
        drive, tail = os.path.splitdrive(windows_path)
        if re.fullmatch(r"[A-Za-z]:", drive) is None or not tail.startswith("\\"):
            raise ValueError(f"{label} path must be a fully qualified drive path")
        import ctypes

        if ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") != 3:
            raise ValueError(f"{label} path must be on a local fixed drive")
    for component in [*reversed(path.parents), path]:
        try:
            metadata = component.lstat()
        except OSError as exc:
            raise ValueError(f"{label} path component is not accessible") from exc
        is_junction = getattr(component, "is_junction", None)
        if (
            component.is_symlink()
            or (callable(is_junction) and is_junction())
            or (
                os.name == "nt"
                and getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
        ):
            raise ValueError(f"{label} path must not traverse a reparse point")
    if not path.is_file():
        raise ValueError(f"{label} must be an explicit regular non-symlink file")
    resolved = path.resolve(strict=True)
    workspace = ROOT.resolve(strict=True)
    if resolved == workspace or workspace in resolved.parents:
        raise ValueError(f"{label} must not be loaded from the repository workspace")
    return resolved


def _validate_gh_path(gh: Path) -> Path:
    gh = _validate_tool_path(gh, label="GitHub CLI")
    if gh.suffix.lower() in {".bat", ".cmd"}:
        raise ValueError("GitHub CLI must be a real executable, not a batch file")
    return gh


def _apksigner_command(apksigner: Path, java: Path | None) -> list[str]:
    apksigner = _validate_tool_path(apksigner, label="apksigner")
    suffix = apksigner.suffix.lower()
    if suffix in {".bat", ".cmd"}:
        raise ValueError(
            "batch apksigner launchers are not accepted; use apksigner.jar with explicit Java"
        )
    if suffix == ".jar":
        if java is None:
            raise ValueError("apksigner.jar requires an explicit Java executable")
        java = _validate_tool_path(java, label="Java executable")
        if java.suffix.lower() in {".bat", ".cmd", ".jar"}:
            raise ValueError("Java must be a real executable, not a batch file or jar")
        return [str(java), "-jar", str(apksigner)]
    if java is not None:
        raise ValueError("--java is only valid when --apksigner points to apksigner.jar")
    return [str(apksigner)]


def _verify_live_apksigner(
    runner: Runner,
    *,
    apksigner: Path,
    java: Path | None,
    apk: Path,
    captured_evidence: Path,
    owner_cert_sha256: str,
) -> str:
    command = _apksigner_command(apksigner, java)
    before = _regular_file_row(apk)
    result = runner.run(
        [*command, "verify", "--verbose", "-Werr", "--print-certs", str(apk)],
        timeout=120,
    )
    if result.returncode != 0:
        raise ValueError("independent apksigner verification failed")
    if len(result.stdout.encode("utf-8")) > MAX_APKSIGNER_OUTPUT_BYTES:
        raise ValueError("independent apksigner output is too large")
    live_cert = verify_release_manifest.parse_android_signer_cert_sha256(result.stdout)
    try:
        captured_text = captured_evidence.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("captured apksigner evidence is not valid UTF-8") from exc
    captured_cert = verify_release_manifest.parse_android_signer_cert_sha256(captured_text)
    if live_cert != owner_cert_sha256 or captured_cert != owner_cert_sha256:
        raise ValueError("live, captured, and Owner Android certificates must match")
    if _regular_file_row(apk) != before:
        raise ValueError("Android APK changed during apksigner verification")
    return live_cert


def _statement_has_digest(statement: Any, digest: str) -> bool:
    if not isinstance(statement, dict):
        return False
    subjects = statement.get("subject")
    if not isinstance(subjects, list):
        return False
    for subject in subjects:
        if not isinstance(subject, dict):
            continue
        digests = subject.get("digest")
        if isinstance(digests, dict) and digests.get("sha256") == digest:
            return True
    return False


def _verify_attestation(
    runner: Runner,
    *,
    gh: Path,
    path: Path,
    digest: str,
    repo: str,
    commit: str,
    run_url: str,
    run_attempt: int,
) -> int:
    before = _regular_file_row(path)
    if before["sha256"] != digest:
        raise ValueError("artifact changed before attestation verification")
    cert_identity = f"https://github.com/{repo}/{WORKFLOW_PATH}@{SOURCE_REF}"
    argv = [
        str(gh),
        "attestation",
        "verify",
        str(path),
        "--repo",
        repo,
        "--hostname",
        "github.com",
        "--cert-identity",
        cert_identity,
        "--cert-oidc-issuer",
        OIDC_ISSUER,
        "--source-ref",
        SOURCE_REF,
        "--source-digest",
        commit,
        "--signer-digest",
        commit,
        "--deny-self-hosted-runners",
        "--predicate-type",
        PREDICATE_TYPE,
        "--limit",
        "100",
        "--format",
        "json",
    ]
    results = _run_json(runner, argv, label="artifact attestation verification", timeout=120)
    if not isinstance(results, list) or not results:
        raise ValueError("artifact attestation verification returned no results")
    expected_invocation = f"{run_url}/attempts/{run_attempt}"
    matching = 0
    for item in results:
        if not isinstance(item, dict):
            continue
        verification = item.get("verificationResult")
        if not isinstance(verification, dict):
            continue
        signature = verification.get("signature")
        certificate = signature.get("certificate") if isinstance(signature, dict) else None
        if not isinstance(certificate, dict):
            continue
        if certificate.get("runInvocationURI") != expected_invocation:
            continue
        if not _statement_has_digest(verification.get("statement"), digest):
            continue
        matching += 1
    if matching < 1:
        raise ValueError("no attestation is bound to the exact workflow run attempt")
    if _regular_file_row(path) != before:
        raise ValueError("artifact changed during attestation verification")
    return matching


def _binding_projection(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo": report["repo"],
        "version": report["version"],
        "target_commit": report["target_commit"],
        "workflow_run": {
            "id": report["workflow_run"]["id"],
            "attempt": report["workflow_run"]["attempt"],
            "path": report["workflow_run"]["path"],
        },
        "workflow_artifacts": [
            {
                "id": row["id"],
                "name": row["name"],
                "size_bytes": row["size_bytes"],
                "api_archive_sha256": row["api_archive_sha256"],
            }
            for row in report["workflow_artifacts"]
        ],
        "draft_release_id": report["draft_release"]["id"],
        "release_tag": report["release_tag"],
        "android_signer": report["android_signer"],
        "artifacts": [
            {
                "role": row["role"],
                "workflow_bundle": row["workflow_bundle"],
                "workflow_name": row["workflow_name"],
                "release_name": row["release_name"],
                "release_asset_id": row["release_asset_id"],
                "size_bytes": row["size_bytes"],
                "sha256": row["sha256"],
            }
            for row in report["artifacts"]
        ],
    }


def _compute_binding_sha256(report: dict[str, Any]) -> str:
    encoded = json.dumps(
        _binding_projection(report), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def build_owner_approved_publication_projection(
    report: dict[str, Any], *, owner_approved_binding: str
) -> dict[str, Any]:
    """Return the in-memory fields permitted to drive exact-ID publication."""

    if SHA256_RE.fullmatch(owner_approved_binding) is None:
        raise ValueError("Owner-approved artifact binding must be 64 lowercase hex characters")
    recomputed = _compute_binding_sha256(report)
    if report.get("artifact_binding_sha256") != recomputed:
        raise ValueError("artifact snapshot binding does not match its canonical contents")
    if owner_approved_binding != recomputed:
        raise ValueError("live artifact binding does not match the Owner approval")
    return {
        "projection_status": "owner_approved_live_snapshot",
        "artifact_binding_sha256": recomputed,
        "target_commit": report["target_commit"],
        "workflow_run": {
            "id": report["workflow_run"]["id"],
            "url": report["workflow_run"]["url"],
            "attempt": report["workflow_run"]["attempt"],
        },
        "draft_release": {
            "id": report["draft_release"]["id"],
            "is_draft": report["draft_release"]["is_draft"],
        },
        "release_tag": report["release_tag"],
        "android_signer": {
            "expected_cert_sha256": report["android_signer"]["expected_cert_sha256"],
            "release_environment": report["android_signer"]["release_environment"],
            "release_environment_variable": report["android_signer"][
                "release_environment_variable"
            ],
        },
        "artifacts": [
            {
                "release_name": row["release_name"],
                "release_asset_id": row["release_asset_id"],
                "size_bytes": row["size_bytes"],
                "sha256": row["sha256"],
            }
            for row in report["artifacts"]
        ],
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def collect_final_draft_evidence(
    *,
    windows_dir: Path,
    android_dir: Path,
    draft_release_dir: Path,
    gh: Path,
    apksigner: Path,
    java: Path | None = None,
    version: str,
    commit: str,
    run_url: str,
    expected_android_cert_sha256: str,
    repo: str = DEFAULT_REPO,
    runner: Runner,
    now_fn: Callable[[], str] = _utc_now,
) -> dict[str, Any]:
    """Collect fail-closed evidence for one exact successful draft=true run."""

    if repo != DEFAULT_REPO or version != DEFAULT_VERSION:
        raise ValueError("live final-draft evidence is fixed to ClipVault v1.6.0")
    commit = validate_commit(commit)
    run_url = validate_run_url(run_url, repo)
    run_id = _run_id(run_url, repo)
    owner_cert = verify_release_manifest.normalize_android_cert_sha256(
        expected_android_cert_sha256
    )
    gh = _validate_gh_path(gh)
    windows_dir = windows_dir.resolve(strict=True)
    android_dir = android_dir.resolve(strict=True)
    draft_release_dir = draft_release_dir.resolve(strict=True)

    validate_evidence(
        windows_dir=windows_dir,
        android_dir=android_dir,
        version=version,
        commit=commit,
        run_url=run_url,
        expected_android_cert_sha256=owner_cert,
        repo=repo,
    )
    specs = _asset_specs(version)
    action_rows, release_rows = _local_asset_rows(
        windows_dir=windows_dir,
        android_dir=android_dir,
        draft_release_dir=draft_release_dir,
        specs=specs,
    )
    initial_action_rows = json.loads(json.dumps(action_rows))
    initial_release_rows = json.loads(json.dumps(release_rows))

    branch_endpoint = f"repos/{repo}/branches/{BRANCH}"
    run_endpoint = f"repos/{repo}/actions/runs/{run_id}"
    artifacts_endpoint = f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"
    cert_variable_endpoint = (
        f"repos/{repo}/environments/{RELEASE_ENVIRONMENT}/variables/"
        f"{RELEASE_CERT_VARIABLE}"
    )
    # The REST "release by tag" endpoint only returns published releases.
    # Authenticated users with push access receive drafts from the list endpoint.
    release_endpoint = f"repos/{repo}/releases?per_page=100"

    _validate_main_record(
        _gh_get(runner, gh, branch_endpoint, label="current main lookup"), commit=commit
    )
    release_tag = _validate_release_tag_state(
        _lookup_release_tag_state(runner, gh, repo=repo, version=version),
        commit=commit,
    )
    cert_variable = _validate_release_cert_variable(
        _gh_get(
            runner,
            gh,
            cert_variable_endpoint,
            label="release environment certificate variable lookup",
        ),
        owner_cert_sha256=owner_cert,
    )
    run = _validate_run_record(
        _gh_get(runner, gh, run_endpoint, label="workflow run lookup"),
        run_id=run_id,
        run_url=run_url,
        repo=repo,
        version=version,
        commit=commit,
    )
    workflow_artifacts = _validate_workflow_artifacts(
        _gh_get(runner, gh, artifacts_endpoint, label="workflow artifact lookup")
    )
    draft_release = _validate_draft_release_record(
        _select_draft_release(
            _gh_get(runner, gh, release_endpoint, label="draft Release lookup"),
            version=version,
        ),
        repo=repo,
        version=version,
        commit=commit,
        release_rows=release_rows,
        specs=specs,
    )

    draft_apk = draft_release_dir / next(
        spec.release_name for spec in specs if spec.role == "android_signed_apk"
    )
    captured_evidence = draft_release_dir / "ANDROID_APKSIGNER_VERIFY.txt"
    observed_cert = _verify_live_apksigner(
        runner,
        apksigner=apksigner,
        java=java,
        apk=draft_apk,
        captured_evidence=captured_evidence,
        owner_cert_sha256=owner_cert,
    )

    artifact_rows: list[dict[str, Any]] = []
    for spec in sorted(specs, key=lambda value: value.release_name):
        local = action_rows[spec.role]
        matching = _verify_attestation(
            runner,
            gh=gh,
            path={"windows": windows_dir, "android": android_dir}[spec.platform]
            / spec.workflow_name,
            digest=local["sha256"],
            repo=repo,
            commit=commit,
            run_url=run_url,
            run_attempt=run["attempt"],
        )
        live_asset = draft_release["assets"][spec.role]
        artifact_rows.append({
            "role": spec.role,
            "workflow_bundle": spec.workflow_bundle,
            "workflow_name": spec.workflow_name,
            "release_name": spec.release_name,
            "size_bytes": local["size_bytes"],
            "sha256": local["sha256"],
            "attestation_verified": True,
            "matching_invocation_count": matching,
            "release_asset_id": live_asset["release_asset_id"],
        })

    _validate_main_record(
        _gh_get(runner, gh, branch_endpoint, label="final current main lookup"), commit=commit
    )
    final_release_tag = _validate_release_tag_state(
        _lookup_release_tag_state(runner, gh, repo=repo, version=version),
        commit=commit,
    )
    if final_release_tag != release_tag:
        raise ValueError("release tag changed during evidence collection")
    final_cert_variable = _validate_release_cert_variable(
        _gh_get(
            runner,
            gh,
            cert_variable_endpoint,
            label="final release environment certificate variable lookup",
        ),
        owner_cert_sha256=owner_cert,
    )
    if final_cert_variable != cert_variable:
        raise ValueError("release environment certificate variable changed during collection")
    final_run = _validate_run_record(
        _gh_get(runner, gh, run_endpoint, label="final workflow run lookup"),
        run_id=run_id,
        run_url=run_url,
        repo=repo,
        version=version,
        commit=commit,
    )
    if final_run != run:
        raise ValueError("workflow run changed during evidence collection")
    final_workflow_artifacts = _validate_workflow_artifacts(
        _gh_get(runner, gh, artifacts_endpoint, label="final workflow artifact lookup")
    )
    if final_workflow_artifacts != workflow_artifacts:
        raise ValueError("workflow artifact bundles changed during evidence collection")
    final_release = _validate_draft_release_record(
        _select_draft_release(
            _gh_get(runner, gh, release_endpoint, label="final draft Release lookup"),
            version=version,
        ),
        repo=repo,
        version=version,
        commit=commit,
        release_rows=release_rows,
        specs=specs,
    )
    if final_release != draft_release:
        raise ValueError("draft Release changed during evidence collection")
    final_action_rows, final_release_rows = _local_asset_rows(
        windows_dir=windows_dir,
        android_dir=android_dir,
        draft_release_dir=draft_release_dir,
        specs=specs,
    )
    if final_action_rows != initial_action_rows or final_release_rows != initial_release_rows:
        raise ValueError("local artifact bytes changed during evidence collection")

    report: dict[str, Any] = {
        "schema_version": 1,
        "evidence_type": "clipvault.issue36.final_draft_artifacts",
        "artifact_gate_status": "snapshot_verified_live_revalidation_required",
        "repo": repo,
        "issue": ISSUE_NUMBER,
        "version": version,
        "branch": BRANCH,
        "target_commit": commit,
        "workflow_run": run,
        "workflow_artifacts": workflow_artifacts,
        "draft_release": {key: value for key, value in draft_release.items() if key != "assets"},
        "release_tag": release_tag,
        "android_signer": {
            "expected_cert_sha256": owner_cert,
            "observed_cert_sha256": observed_cert,
            "signer_count": 1,
            "apksigner_verified": True,
            "trust_anchor_source": "github_release_environment_variable_and_owner_input_match",
            "release_environment": cert_variable["environment"],
            "release_environment_variable": cert_variable["name"],
        },
        "artifacts": artifact_rows,
        "validated_at": now_fn(),
        "assurance": "live_exact_run_and_draft_snapshot",
        "consumer_requirement": "rerun_or_live_cross_check_before_release_readiness",
        "scope_note": (
            "This evidence verifies one exact successful draft=true run, eight attested "
            "artifact files, matching draft Release bytes, the exact release-tag state, "
            "and the Owner Android signer. "
            "This JSON is not self-authenticating: a readiness consumer must rerun these "
            "checks or independently cross-check the binding and live GitHub state. It does "
            "not replace manual QA, "
            "Owner publication approval, final publication, or Issue #36 closure."
        ),
    }
    report["artifact_binding_sha256"] = _compute_binding_sha256(report)
    return report


def render_issue_comment(report: dict[str, Any]) -> str:
    """Render the backward-compatible structural-precheck comment."""

    lines = [
        f"Release artifact evidence draft for Issue #{report['issue']}",
        "",
        f"- Repository: `{report['repo']}`",
        f"- Version: `{report['version']}`",
        f"- Target commit: `{report['commit']}`",
        f"- Release artifact workflow run: {report['run_url']}",
        f"- Expected Owner Android certificate SHA-256: `{report['android_owner_cert_sha256']}`",
        "",
        "Validated downloaded artifact directories:",
        "",
        "- Windows release artifacts:",
    ]
    for name in report["windows_artifacts"]:
        lines.append(f"  - `{name}`")
    lines.append("- Android signed release artifacts:")
    for name in report["android_artifacts"]:
        lines.append(f"  - `{name}`")
    lines.extend([
        "",
        "Local validation performed by:",
        "",
        "```powershell",
        "python tools/release_artifact_evidence.py `",
        f"  --windows-dir \"{report['windows_dir']}\" `",
        f"  --android-dir \"{report['android_dir']}\" `",
        f"  --version {report['version']} `",
        f"  --commit {report['commit']} `",
        f"  --run-url {report['run_url']} `",
        "  --expected-android-cert-sha256 $env:ANDROID_RELEASE_CERT_SHA256",
        "```",
        "",
        "The helper verified `RELEASE_MANIFEST.json`, `SHA256SUMS.txt`, exact",
        "required release artifact names, and the sole Android signer certificate",
        "in `ANDROID_APKSIGNER_VERIFY.txt` against the supplied Owner SHA-256",
        "for downloaded artifacts.",
        "",
        "Mandatory provenance follow-up: use `--require-live-final-draft` to validate",
        "the exact GitHub run, `gh attestation verify` for all eight files, draft Release bytes, and an",
        "independent APK signer check before this can satisfy Issue #36.",
        "",
        str(report["scope_note"]),
    ])
    return "\n".join(lines) + "\n"


def render_final_draft_issue_comment(report: dict[str, Any]) -> str:
    lines = [
        f"Live-verified final draft artifact snapshot for Issue #{report['issue']}",
        "",
        f"- Repository: `{report['repo']}`",
        f"- Version: `{report['version']}`",
        f"- Target commit: `{report['target_commit']}`",
        f"- Workflow run: {report['workflow_run']['url']} (attempt {report['workflow_run']['attempt']})",
        f"- Draft Release: {report['draft_release']['url']}",
        f"- Release tag state: `{report['release_tag']['state']}` "
        f"(`{report['release_tag']['ref']}`)",
        f"- Artifact binding SHA-256: `{report['artifact_binding_sha256']}`",
        f"- Owner Android certificate SHA-256: `{report['android_signer']['expected_cert_sha256']}`",
        "",
        "| Platform | File | Bytes | SHA-256 | Attestation |",
        "|---|---|---:|---|---|",
    ]
    for row in report["artifacts"]:
        platform = "Android" if row["role"].startswith("android_") else "Windows"
        lines.append(
            f"| {platform} | `{row['release_name']}` | {row['size_bytes']} | "
            f"`{row['sha256']}` | exact run attempt verified |"
        )
    lines.extend(["", str(report["scope_note"])])
    return "\n".join(lines) + "\n"


def _write_new_outputs(outputs: Sequence[tuple[Path, str]]) -> None:
    paths = [path for path, _ in outputs]
    if len(set(paths)) != len(paths):
        raise ValueError("evidence output paths must be distinct")
    for path in paths:
        if path.exists() or path.is_symlink():
            raise ValueError(f"refusing to overwrite evidence output: {path.name!r}")
        if not path.parent.is_dir():
            raise ValueError(f"evidence output parent does not exist: {path.parent.name!r}")
    created: list[Path] = []
    try:
        for path, content in outputs:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            created.append(path)
    except OSError as exc:
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        raise ValueError("could not write evidence outputs") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate release artifacts and render Issue #36 evidence.",
    )
    parser.add_argument("--windows-dir", required=True, type=Path)
    parser.add_argument("--android-dir", required=True, type=Path)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--expected-android-cert-sha256", required=True)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--json", action="store_true", help="emit structural precheck JSON")
    parser.add_argument("--output", type=Path, help="write the structural-precheck output")
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="return exit code 0 after structural-precheck validation errors",
    )
    parser.add_argument("--require-live-final-draft", action="store_true")
    parser.add_argument("--draft-release-dir", type=Path)
    parser.add_argument("--gh", type=Path)
    parser.add_argument("--apksigner", type=Path)
    parser.add_argument("--java", type=Path)
    parser.add_argument("--evidence-output", type=Path)
    parser.add_argument("--comment-output", type=Path)
    parser.add_argument("--owner-approved-binding")
    parser.add_argument("--publication-projection-stdout", action="store_true")
    args = parser.parse_args(argv)

    if args.require_live_final_draft:
        if args.no_fail or args.output is not None or args.json:
            parser.error("live final-draft mode forbids --no-fail, --output, and --json")
        missing = [
            name
            for name, value in (
                ("--draft-release-dir", args.draft_release_dir),
                ("--gh", args.gh),
                ("--apksigner", args.apksigner),
                ("--evidence-output", args.evidence_output),
                ("--comment-output", args.comment_output),
            )
            if value is None
        ]
        if missing:
            parser.error("live final-draft mode requires " + ", ".join(missing))
        if args.publication_projection_stdout != (args.owner_approved_binding is not None):
            parser.error(
                "--publication-projection-stdout and --owner-approved-binding must be used together"
            )
        try:
            report = collect_final_draft_evidence(
                windows_dir=args.windows_dir,
                android_dir=args.android_dir,
                draft_release_dir=args.draft_release_dir,
                gh=args.gh,
                apksigner=args.apksigner,
                java=args.java,
                version=args.version,
                commit=args.commit,
                run_url=args.run_url,
                expected_android_cert_sha256=args.expected_android_cert_sha256,
                repo=args.repo,
                runner=SubprocessRunner(),
            )
            publication_projection = None
            if args.publication_projection_stdout:
                publication_projection = build_owner_approved_publication_projection(
                    report,
                    owner_approved_binding=args.owner_approved_binding,
                )
            _write_new_outputs([
                (
                    args.evidence_output,
                    json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
                ),
                (args.comment_output, render_final_draft_issue_comment(report)),
            ])
            if publication_projection is not None:
                print(
                    json.dumps(
                        publication_projection,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    )
                )
        except (ValueError, OSError, UnicodeError) as exc:
            detail = str(exc) if isinstance(exc, ValueError) else "local filesystem validation failed"
            print(f"live final-draft evidence validation failed: {detail}", file=sys.stderr)
            return 1
        return 0

    if any(
        value is not None
        for value in (
            args.draft_release_dir,
            args.gh,
            args.apksigner,
            args.java,
            args.evidence_output,
            args.comment_output,
            args.owner_approved_binding,
        )
    ) or args.publication_projection_stdout:
        parser.error("live final-draft arguments require --require-live-final-draft")
    try:
        report = validate_evidence(
            windows_dir=args.windows_dir,
            android_dir=args.android_dir,
            version=args.version,
            commit=args.commit,
            run_url=args.run_url,
            expected_android_cert_sha256=args.expected_android_cert_sha256,
            repo=args.repo,
        )
    except ValueError as exc:
        print(f"release artifact evidence validation failed: {exc}", file=sys.stderr)
        return 0 if args.no_fail else 1

    if args.json:
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    else:
        rendered = render_issue_comment(report)
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
