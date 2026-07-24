#!/usr/bin/env python3
"""Validate v1.6 release artifacts and render Issue #36 evidence.

The default mode remains a local structural precheck for compatibility. This tool
does not download GitHub Actions artifacts. The explicit live final-draft and
published-release modes additionally verify GitHub run metadata, artifact
attestations, Release bytes, and the Android signer. No mode performs manual QA,
publishes a Release, posts to GitHub, or closes Issue #36.
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
GITHUB_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
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
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
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
            "windows_lgpl_relink_kit",
            "windows",
            "clipvault-windows-release-artifacts",
            f"ClipVault-v{numeric}-LGPL-relink-kit.zip",
            f"ClipVault-v{numeric}-LGPL-relink-kit.zip",
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


def _validate_github_timestamp(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or GITHUB_TIMESTAMP_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be an RFC3339 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label} must be an RFC3339 UTC timestamp")
    return value


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
    release_dir: Path,
    specs: Sequence[AssetSpec],
    release_label: str = "draft Release",
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
    release_by_name = _exact_directory_rows(release_dir, release_names)
    release_rows = {spec.role: release_by_name[spec.release_name] for spec in specs}
    for spec in specs:
        action = action_rows[spec.role]
        release = release_rows[spec.role]
        if (action["size_bytes"], action["sha256"]) != (
            release["size_bytes"],
            release["sha256"],
        ):
            raise ValueError(f"Actions and {release_label} bytes differ for {spec.release_name!r}")
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
    include_object_identity: bool = False,
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
    ref_object_type = obj.get("type")
    ref_object_sha = obj.get("sha")
    seen: set[str] = set()
    for _ in range(8):
        object_type = obj.get("type")
        object_sha = obj.get("sha")
        if not isinstance(object_sha, str) or COMMIT_RE.fullmatch(object_sha) is None:
            raise ValueError("release tag object has an invalid Git SHA")
        if object_type == "commit":
            result = {"ref": exact_ref, "state": "present", "commit_sha": object_sha}
            if include_object_identity:
                result.update({
                    "ref_object_type": ref_object_type,
                    "ref_object_sha": ref_object_sha,
                })
            return result
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
    # With a top-level `run-name`, the Actions run API reports the rendered
    # run title in both `name` and `display_title`. The static workflow
    # identity remains bound independently by the exact workflow path.
    expected_run_title = f"Release artifacts {version} from main draft=true"
    expected = {
        "id": run_id,
        "html_url": run_url,
        "name": expected_run_title,
        "path": WORKFLOW_PATH,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_branch": BRANCH,
        "head_sha": commit,
        "display_title": expected_run_title,
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


def _validate_release_record(
    record: Any,
    *,
    repo: str,
    version: str,
    commit: str,
    release_rows: dict[str, dict[str, Any]],
    specs: Sequence[AssetSpec],
    expected_draft: bool,
    release_label: str,
) -> dict[str, Any]:
    data = _require_dict(record, label=f"{release_label} response")
    if data.get("tag_name") != version:
        raise ValueError(f"{release_label} tag mismatch")
    if data.get("name") != f"ClipVault Personal {version}":
        raise ValueError(f"{release_label} title mismatch")
    if data.get("draft") is not expected_draft or data.get("prerelease") is not False:
        state = "a non-prerelease draft" if expected_draft else "published and non-prerelease"
        raise ValueError(f"GitHub Release must be {state}")
    if data.get("target_commitish") != commit:
        raise ValueError(f"{release_label} target commit mismatch")
    release_url = data.get("html_url")
    expected_published_url = f"https://github.com/{repo}/releases/tag/{version}"
    if expected_draft:
        valid_url = isinstance(release_url, str) and release_url.startswith(
            f"https://github.com/{repo}/releases/"
        )
    else:
        valid_url = release_url == expected_published_url
    if not valid_url:
        raise ValueError(f"{release_label} URL mismatch")
    published_at: str | None = None
    if not expected_draft:
        published_at = _validate_github_timestamp(
            data.get("published_at"), label="published Release publication timestamp"
        )
    assets = data.get("assets")
    if not isinstance(assets, list) or len(assets) != len(specs):
        raise ValueError(f"{release_label} asset inventory mismatch")
    by_name: dict[str, dict[str, Any]] = {}
    for raw in assets:
        asset = _require_dict(raw, label=f"{release_label} asset")
        name = asset.get("name")
        if not isinstance(name, str) or name in by_name:
            raise ValueError(f"{release_label} contains an invalid or duplicate asset name")
        by_name[name] = asset
    expected_names = {spec.release_name for spec in specs}
    if set(by_name) != expected_names:
        raise ValueError(f"{release_label} asset inventory mismatch")

    asset_rows: dict[str, dict[str, Any]] = {}
    for spec in specs:
        live = by_name[spec.release_name]
        local = release_rows[spec.role]
        if live.get("state") != "uploaded":
            raise ValueError(f"{release_label} asset is not fully uploaded")
        size = _require_positive_int(live.get("size"), label=f"{release_label} asset size")
        digest = _normalize_api_digest(live.get("digest"), label=f"{release_label} asset digest")
        if (size, digest) != (local["size_bytes"], local["sha256"]):
            raise ValueError(f"{release_label} API bytes differ for {spec.release_name!r}")
        asset_rows[spec.role] = {
            "release_asset_id": _require_positive_int(
                live.get("id"), label=f"{release_label} asset id"
            ),
            "size_bytes": size,
            "sha256": digest,
        }
    result = {
        "id": _require_positive_int(data.get("id"), label=f"{release_label} id"),
        "url": release_url,
        "tag_name": version,
        "name": f"ClipVault Personal {version}",
        "is_draft": expected_draft,
        "is_prerelease": False,
        "target_commitish": commit,
        "assets": asset_rows,
    }
    if published_at is not None:
        result["published_at"] = published_at
    return result


def _validate_draft_release_record(
    record: Any,
    *,
    repo: str,
    version: str,
    commit: str,
    release_rows: dict[str, dict[str, Any]],
    specs: Sequence[AssetSpec],
) -> dict[str, Any]:
    return _validate_release_record(
        record,
        repo=repo,
        version=version,
        commit=commit,
        release_rows=release_rows,
        specs=specs,
        expected_draft=True,
        release_label="draft Release",
    )


def _validate_published_release_record(
    record: Any,
    *,
    repo: str,
    version: str,
    commit: str,
    release_rows: dict[str, dict[str, Any]],
    specs: Sequence[AssetSpec],
) -> dict[str, Any]:
    return _validate_release_record(
        record,
        repo=repo,
        version=version,
        commit=commit,
        release_rows=release_rows,
        specs=specs,
        expected_draft=False,
        release_label="published Release",
    )


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


def build_final_draft_manual_qa_binding_projection(
    report: dict[str, Any],
) -> dict[str, Any]:
    """Validate one canonical final-draft snapshot and extract its QA identity.

    This is an offline integrity and consistency check. The snapshot remains
    non-self-authenticating and must still be revalidated against live GitHub
    state before publication.
    """

    data = _require_dict(report, label="final-draft artifact evidence")
    if data.get("schema_version") != 1 or isinstance(data.get("schema_version"), bool):
        raise ValueError("final-draft artifact evidence schema must be 1")
    if data.get("evidence_type") != "clipvault.issue36.final_draft_artifacts":
        raise ValueError("artifact evidence must be a final-draft Issue #36 report")
    if data.get("artifact_gate_status") != "snapshot_verified_live_revalidation_required":
        raise ValueError("final-draft artifact evidence status is invalid")
    if (
        data.get("repo") != DEFAULT_REPO
        or data.get("issue") != ISSUE_NUMBER
        or isinstance(data.get("issue"), bool)
        or data.get("version") != DEFAULT_VERSION
        or data.get("branch") != BRANCH
    ):
        raise ValueError("final-draft artifact evidence scope mismatch")

    raw_target_commit = data.get("target_commit")
    if not isinstance(raw_target_commit, str):
        raise ValueError("target_commit must be a canonical lowercase Git SHA")
    target_commit = validate_commit(raw_target_commit)
    if raw_target_commit != target_commit:
        raise ValueError("target_commit must be a canonical lowercase Git SHA")

    workflow_run = _require_dict(data.get("workflow_run"), label="workflow_run")
    expected_run_keys = {
        "id",
        "url",
        "attempt",
        "workflow",
        "path",
        "event",
        "status",
        "conclusion",
        "head_branch",
        "head_sha",
        "display_title",
    }
    if set(workflow_run) != expected_run_keys:
        raise ValueError("workflow_run must use the canonical final-draft shape")
    run_id = _require_positive_int(workflow_run.get("id"), label="workflow_run.id")
    run_attempt = _require_positive_int(
        workflow_run.get("attempt"), label="workflow_run.attempt"
    )
    raw_run_url = workflow_run.get("url")
    if not isinstance(raw_run_url, str):
        raise ValueError("workflow_run.url must be a GitHub Actions run URL")
    run_url = validate_run_url(raw_run_url, DEFAULT_REPO)
    if raw_run_url != run_url or _run_id(run_url, DEFAULT_REPO) != run_id:
        raise ValueError("workflow_run.url does not exactly match workflow_run.id")
    expected_run = {
        "workflow": WORKFLOW_NAME,
        "path": WORKFLOW_PATH,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_branch": BRANCH,
        "head_sha": target_commit,
        "display_title": f"Release artifacts {DEFAULT_VERSION} from main draft=true",
    }
    for field, expected in expected_run.items():
        if workflow_run.get(field) != expected:
            raise ValueError(f"workflow_run.{field} mismatch")

    bundles = data.get("workflow_artifacts")
    if not isinstance(bundles, list) or len(bundles) != 2:
        raise ValueError("final-draft evidence must contain exactly two workflow bundles")
    expected_bundle_names = sorted({
        "clipvault-windows-release-artifacts",
        "clipvault-android-signed-release-artifacts",
    })
    observed_bundle_names: list[str] = []
    observed_bundle_ids: set[int] = set()
    for index, raw_bundle in enumerate(bundles):
        bundle = _require_dict(raw_bundle, label=f"workflow_artifacts[{index}]")
        if set(bundle) != {"id", "name", "size_bytes", "api_archive_sha256"}:
            raise ValueError("workflow artifact bundle must use the canonical shape")
        name = bundle.get("name")
        if not isinstance(name, str):
            raise ValueError("workflow artifact bundle names must be strings")
        observed_bundle_names.append(name)
        bundle_id = _require_positive_int(
            bundle.get("id"), label=f"workflow_artifacts[{index}].id"
        )
        if bundle_id in observed_bundle_ids:
            raise ValueError("workflow artifact bundle IDs must be unique")
        observed_bundle_ids.add(bundle_id)
        _require_positive_int(
            bundle.get("size_bytes"), label=f"workflow_artifacts[{index}].size_bytes"
        )
        digest = bundle.get("api_archive_sha256")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise ValueError(
                f"workflow_artifacts[{index}].api_archive_sha256 must be lowercase SHA-256"
            )
    if observed_bundle_names != expected_bundle_names:
        raise ValueError("workflow artifact bundle inventory/order mismatch")

    draft_release = _require_dict(data.get("draft_release"), label="draft_release")
    expected_release_keys = {
        "id",
        "url",
        "tag_name",
        "name",
        "is_draft",
        "is_prerelease",
        "target_commitish",
    }
    if set(draft_release) != expected_release_keys:
        raise ValueError("draft_release must use the canonical final-draft shape")
    release_id = _require_positive_int(draft_release.get("id"), label="draft_release.id")
    release_url = draft_release.get("url")
    release_prefix = f"https://github.com/{DEFAULT_REPO}/releases/tag/"
    valid_release_urls = {
        f"{release_prefix}{DEFAULT_VERSION}",
    }
    if not isinstance(release_url, str) or (
        release_url not in valid_release_urls
        and re.fullmatch(
            re.escape(release_prefix) + r"untagged-[A-Za-z0-9._-]+", release_url
        )
        is None
    ):
        raise ValueError("draft_release.url mismatch")
    if (
        draft_release.get("tag_name") != DEFAULT_VERSION
        or draft_release.get("name") != f"ClipVault Personal {DEFAULT_VERSION}"
        or draft_release.get("is_draft") is not True
        or draft_release.get("is_prerelease") is not False
        or draft_release.get("target_commitish") != target_commit
    ):
        raise ValueError("draft_release identity mismatch")

    release_tag = _require_dict(data.get("release_tag"), label="release_tag")
    if set(release_tag) != {"ref", "state", "commit_sha"}:
        raise ValueError("release_tag must use the canonical final-draft shape")
    if release_tag.get("ref") != f"refs/tags/{DEFAULT_VERSION}":
        raise ValueError("release_tag ref mismatch")
    tag_state = release_tag.get("state")
    tag_commit = release_tag.get("commit_sha")
    if tag_state == "absent":
        if tag_commit is not None:
            raise ValueError("absent release_tag must not claim a commit")
    elif tag_state == "present":
        if tag_commit != target_commit:
            raise ValueError("present release_tag must resolve to target_commit")
    else:
        raise ValueError("release_tag state must be absent or present")

    signer = _require_dict(data.get("android_signer"), label="android_signer")
    expected_signer_keys = {
        "expected_cert_sha256",
        "observed_cert_sha256",
        "signer_count",
        "apksigner_verified",
        "trust_anchor_source",
        "release_environment",
        "release_environment_variable",
    }
    if set(signer) != expected_signer_keys:
        raise ValueError("android_signer must use the canonical final-draft shape")
    expected_cert = signer.get("expected_cert_sha256")
    observed_cert = signer.get("observed_cert_sha256")
    if (
        not isinstance(expected_cert, str)
        or SHA256_RE.fullmatch(expected_cert) is None
        or observed_cert != expected_cert
    ):
        raise ValueError("android_signer certificate identity mismatch")
    if (
        _require_positive_int(signer.get("signer_count"), label="android_signer.signer_count")
        != 1
        or signer.get("apksigner_verified") is not True
        or signer.get("trust_anchor_source")
        != "github_release_environment_variable_and_owner_input_match"
        or signer.get("release_environment") != RELEASE_ENVIRONMENT
        or signer.get("release_environment_variable") != RELEASE_CERT_VARIABLE
    ):
        raise ValueError("android_signer verification identity mismatch")

    specs = {spec.role: spec for spec in _asset_specs(DEFAULT_VERSION)}
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(specs):
        raise ValueError(
            "final-draft evidence must contain the exact canonical artifact set "
            f"(expected {len(specs)})"
        )
    by_role: dict[str, dict[str, Any]] = {}
    release_asset_ids: set[int] = set()
    observed_release_names: list[str] = []
    expected_artifact_keys = {
        "role",
        "workflow_bundle",
        "workflow_name",
        "release_name",
        "size_bytes",
        "sha256",
        "attestation_verified",
        "matching_invocation_count",
        "release_asset_id",
    }
    for index, raw_artifact in enumerate(artifacts):
        artifact = _require_dict(raw_artifact, label=f"artifacts[{index}]")
        if set(artifact) != expected_artifact_keys:
            raise ValueError(f"artifacts[{index}] must use the canonical final-draft shape")
        role = artifact.get("role")
        if not isinstance(role, str) or role not in specs or role in by_role:
            raise ValueError("artifact roles must be the unique expected release roles")
        spec = specs[role]
        if (
            artifact.get("workflow_bundle") != spec.workflow_bundle
            or artifact.get("workflow_name") != spec.workflow_name
            or artifact.get("release_name") != spec.release_name
        ):
            raise ValueError(f"artifact identity mismatch for role {role!r}")
        observed_release_names.append(spec.release_name)
        release_asset_id = _require_positive_int(
            artifact.get("release_asset_id"), label=f"artifacts[{index}].release_asset_id"
        )
        if release_asset_id in release_asset_ids:
            raise ValueError("release asset IDs must be unique")
        release_asset_ids.add(release_asset_id)
        _require_positive_int(
            artifact.get("size_bytes"), label=f"artifacts[{index}].size_bytes"
        )
        digest = artifact.get("sha256")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise ValueError(f"artifacts[{index}].sha256 must be lowercase SHA-256")
        if artifact.get("attestation_verified") is not True:
            raise ValueError(f"artifacts[{index}] must record verified attestation")
        _require_positive_int(
            artifact.get("matching_invocation_count"),
            label=f"artifacts[{index}].matching_invocation_count",
        )
        by_role[role] = artifact
    if observed_release_names != sorted(spec.release_name for spec in specs.values()):
        raise ValueError("artifact inventory/order mismatch")

    claimed_binding = data.get("artifact_binding_sha256")
    if not isinstance(claimed_binding, str) or SHA256_RE.fullmatch(claimed_binding) is None:
        raise ValueError("artifact_binding_sha256 must be lowercase SHA-256")
    try:
        recomputed_binding = _compute_binding_sha256(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("final-draft artifact binding projection is malformed") from exc
    if claimed_binding != recomputed_binding:
        raise ValueError("final-draft artifact binding does not match report contents")

    signed_apk = by_role["android_signed_apk"]
    return {
        "artifact_evidence_type": data["evidence_type"],
        "artifact_binding_sha256": claimed_binding,
        "target_commit": target_commit,
        "workflow_run": {
            "id": run_id,
            "attempt": run_attempt,
            "url": run_url,
        },
        "draft_release": {
            "id": release_id,
            "url": release_url,
            "tag_name": DEFAULT_VERSION,
        },
        "android_signed_apk": {
            "name": signed_apk["release_name"],
            "sha256": signed_apk["sha256"],
        },
    }


def _owner_binding_candidate(
    *,
    repo: str,
    version: str,
    target_commit: str,
    workflow_run: dict[str, Any],
    workflow_artifacts: list[dict[str, Any]],
    release_id: int,
    prepublication_release_tag: dict[str, Any],
    android_signer: dict[str, Any],
    artifacts: list[dict[str, Any]],
) -> str:
    """Recreate one #111-compatible pre-publication binding candidate."""

    return _compute_binding_sha256({
        "repo": repo,
        "version": version,
        "target_commit": target_commit,
        "workflow_run": workflow_run,
        "workflow_artifacts": workflow_artifacts,
        "draft_release": {"id": release_id},
        "release_tag": prepublication_release_tag,
        "android_signer": android_signer,
        "artifacts": artifacts,
    })


def _publication_closure_projection(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo": report["repo"],
        "version": report["version"],
        "target_commit": report["target_commit"],
        "owner_approved_artifact_binding_sha256": report[
            "owner_approved_artifact_binding_sha256"
        ],
        "owner_approved_prepublication_release_tag": report[
            "owner_approved_prepublication_release_tag"
        ],
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
        "published_release": {
            key: report["published_release"][key]
            for key in (
                "id",
                "url",
                "tag_name",
                "name",
                "is_draft",
                "is_prerelease",
                "target_commitish",
                "published_at",
            )
        },
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
                "attestation_verified": row["attestation_verified"],
                "matching_invocation_count": row["matching_invocation_count"],
            }
            for row in report["artifacts"]
        ],
    }


def _compute_publication_closure_sha256(report: dict[str, Any]) -> str:
    encoded = json.dumps(
        _publication_closure_projection(report),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def build_owner_approved_publication_projection(
    report: dict[str, Any], *, owner_approved_binding: str
) -> dict[str, Any]:
    """Return the in-memory fields permitted to drive exact-ID publication."""

    if (
        not isinstance(owner_approved_binding, str)
        or SHA256_RE.fullmatch(owner_approved_binding) is None
    ):
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
        release_dir=draft_release_dir,
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
        release_dir=draft_release_dir,
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
            f"This evidence verifies one exact successful draft=true run, {len(specs)} attested "
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


def collect_published_release_evidence(
    *,
    windows_dir: Path,
    android_dir: Path,
    published_release_dir: Path,
    gh: Path,
    apksigner: Path,
    java: Path | None = None,
    version: str,
    commit: str,
    run_url: str,
    expected_android_cert_sha256: str,
    owner_approved_binding: str,
    repo: str = DEFAULT_REPO,
    runner: Runner,
    now_fn: Callable[[], str] = _utc_now,
) -> dict[str, Any]:
    """Revalidate one published Release against the Owner-approved draft binding."""

    if repo != DEFAULT_REPO or version != DEFAULT_VERSION:
        raise ValueError("live published evidence is fixed to ClipVault v1.6.0")
    if (
        not isinstance(owner_approved_binding, str)
        or SHA256_RE.fullmatch(owner_approved_binding) is None
    ):
        raise ValueError("Owner-approved artifact binding must be 64 lowercase hex characters")
    commit = validate_commit(commit)
    run_url = validate_run_url(run_url, repo)
    run_id = _run_id(run_url, repo)
    owner_cert = verify_release_manifest.normalize_android_cert_sha256(
        expected_android_cert_sha256
    )
    gh = _validate_gh_path(gh)
    windows_dir = windows_dir.resolve(strict=True)
    android_dir = android_dir.resolve(strict=True)
    published_release_dir = published_release_dir.resolve(strict=True)

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
        release_dir=published_release_dir,
        specs=specs,
        release_label="published Release",
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
    release_endpoint = f"repos/{repo}/releases/tags/{version}"

    _validate_main_record(
        _gh_get(runner, gh, branch_endpoint, label="current main lookup"), commit=commit
    )
    release_tag = _validate_release_tag_state(
        _lookup_release_tag_state(
            runner,
            gh,
            repo=repo,
            version=version,
            include_object_identity=True,
        ),
        commit=commit,
    )
    if release_tag["state"] != "present":
        raise ValueError("published release tag is absent")
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
    published_release = _validate_published_release_record(
        _gh_get(runner, gh, release_endpoint, label="published Release lookup"),
        repo=repo,
        version=version,
        commit=commit,
        release_rows=release_rows,
        specs=specs,
    )

    published_apk = published_release_dir / next(
        spec.release_name for spec in specs if spec.role == "android_signed_apk"
    )
    captured_evidence = published_release_dir / "ANDROID_APKSIGNER_VERIFY.txt"
    observed_cert = _verify_live_apksigner(
        runner,
        apksigner=apksigner,
        java=java,
        apk=published_apk,
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
        live_asset = published_release["assets"][spec.role]
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

    android_signer = {
        "expected_cert_sha256": owner_cert,
        "observed_cert_sha256": observed_cert,
        "signer_count": 1,
        "apksigner_verified": True,
        "trust_anchor_source": "github_release_environment_variable_and_owner_input_match",
        "release_environment": cert_variable["environment"],
        "release_environment_variable": cert_variable["name"],
    }
    exact_ref = f"refs/tags/{version}"
    prepublication_candidates = (
        {"ref": exact_ref, "state": "absent", "commit_sha": None},
        {"ref": exact_ref, "state": "present", "commit_sha": commit},
    )
    matching_candidates = [
        candidate
        for candidate in prepublication_candidates
        if _owner_binding_candidate(
            repo=repo,
            version=version,
            target_commit=commit,
            workflow_run=run,
            workflow_artifacts=workflow_artifacts,
            release_id=published_release["id"],
            prepublication_release_tag=candidate,
            android_signer=android_signer,
            artifacts=artifact_rows,
        )
        == owner_approved_binding
    ]
    if len(matching_candidates) != 1:
        raise ValueError(
            "live published state does not reproduce the Owner-approved artifact binding"
        )
    owner_prepublication_tag = matching_candidates[0]

    _validate_main_record(
        _gh_get(runner, gh, branch_endpoint, label="final current main lookup"), commit=commit
    )
    final_release_tag = _validate_release_tag_state(
        _lookup_release_tag_state(
            runner,
            gh,
            repo=repo,
            version=version,
            include_object_identity=True,
        ),
        commit=commit,
    )
    if final_release_tag != release_tag:
        raise ValueError("release tag changed during published evidence collection")
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
        raise ValueError("workflow run changed during published evidence collection")
    final_workflow_artifacts = _validate_workflow_artifacts(
        _gh_get(runner, gh, artifacts_endpoint, label="final workflow artifact lookup")
    )
    if final_workflow_artifacts != workflow_artifacts:
        raise ValueError("workflow artifact bundles changed during published evidence collection")
    final_published_release = _validate_published_release_record(
        _gh_get(runner, gh, release_endpoint, label="final published Release lookup"),
        repo=repo,
        version=version,
        commit=commit,
        release_rows=release_rows,
        specs=specs,
    )
    if final_published_release != published_release:
        raise ValueError("published Release changed during evidence collection")
    final_action_rows, final_release_rows = _local_asset_rows(
        windows_dir=windows_dir,
        android_dir=android_dir,
        release_dir=published_release_dir,
        specs=specs,
        release_label="published Release",
    )
    if final_action_rows != initial_action_rows or final_release_rows != initial_release_rows:
        raise ValueError("local artifact bytes changed during published evidence collection")

    report: dict[str, Any] = {
        "schema_version": 1,
        "evidence_type": "clipvault.issue36.published_release",
        "publication_gate_status": "published_release_verified_live_revalidation_required",
        "repo": repo,
        "issue": ISSUE_NUMBER,
        "version": version,
        "branch": BRANCH,
        "target_commit": commit,
        "workflow_run": run,
        "workflow_artifacts": workflow_artifacts,
        "owner_approved_artifact_binding_sha256": owner_approved_binding,
        "owner_approved_prepublication_release_tag": owner_prepublication_tag,
        "published_release": {
            key: value for key, value in published_release.items() if key != "assets"
        },
        "release_tag": release_tag,
        "android_signer": android_signer,
        "artifacts": artifact_rows,
        "validated_at": now_fn(),
        "assurance": "live_exact_run_and_published_release_snapshot",
        "consumer_requirement": "rerun_or_live_cross_check_before_release_readiness",
        "scope_note": (
            f"This evidence revalidates the exact successful draft=true run, {len(specs)} attested "
            "artifact files, published Release ID and bytes, exact current release tag, and "
            "Owner Android signer against the Owner-approved pre-publication binding. This "
            "JSON is not self-authenticating and does not replace manual QA, Owner closure "
            "approval, or Issue #36 closure."
        ),
    }
    report["publication_closure_binding_sha256"] = _compute_publication_closure_sha256(
        report
    )
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
        "the exact GitHub run, `gh attestation verify` for all "
        f"{len(_asset_specs(str(report['version'])))} files, draft Release bytes, and an",
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


def render_published_release_issue_comment(report: dict[str, Any]) -> str:
    lines = [
        f"Live-verified published Release snapshot for Issue #{report['issue']}",
        "",
        f"- Repository: `{report['repo']}`",
        f"- Version: `{report['version']}`",
        f"- Target commit: `{report['target_commit']}`",
        f"- Workflow run: {report['workflow_run']['url']} (attempt {report['workflow_run']['attempt']})",
        f"- Published Release: {report['published_release']['url']} "
        f"(ID {report['published_release']['id']})",
        f"- Release tag: `{report['release_tag']['ref']}` -> "
        f"`{report['release_tag']['commit_sha']}`",
        f"- Owner-approved artifact binding SHA-256: "
        f"`{report['owner_approved_artifact_binding_sha256']}`",
        f"- Publication closure binding SHA-256: "
        f"`{report['publication_closure_binding_sha256']}`",
        f"- Owner Android certificate SHA-256: "
        f"`{report['android_signer']['expected_cert_sha256']}`",
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


def _validate_output_locations(
    outputs: Sequence[Path],
    *,
    artifact_dirs: Sequence[Path],
) -> None:
    roots = [directory.resolve(strict=True) for directory in artifact_dirs]
    for output in outputs:
        candidate = output.resolve(strict=False)
        if any(candidate == root or root in candidate.parents for root in roots):
            raise ValueError("evidence outputs must be outside verified artifact directories")


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
            stream = path.open("x", encoding="utf-8", newline="\n")
            created.append(path)
            with stream:
                stream.write(content)
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
    parser.add_argument("--require-live-published-release", action="store_true")
    parser.add_argument("--draft-release-dir", type=Path)
    parser.add_argument("--published-release-dir", type=Path)
    parser.add_argument("--gh", type=Path)
    parser.add_argument("--apksigner", type=Path)
    parser.add_argument("--java", type=Path)
    parser.add_argument("--evidence-output", type=Path)
    parser.add_argument("--comment-output", type=Path)
    parser.add_argument("--owner-approved-binding")
    parser.add_argument("--publication-projection-stdout", action="store_true")
    args = parser.parse_args(argv)

    if args.require_live_final_draft and args.require_live_published_release:
        parser.error("live final-draft and published-release modes are mutually exclusive")

    if args.require_live_published_release:
        if args.no_fail or args.output is not None or args.json:
            parser.error("live published-release mode forbids --no-fail, --output, and --json")
        if args.draft_release_dir is not None or args.publication_projection_stdout:
            parser.error(
                "live published-release mode forbids --draft-release-dir and "
                "--publication-projection-stdout"
            )
        missing = [
            name
            for name, value in (
                ("--published-release-dir", args.published_release_dir),
                ("--gh", args.gh),
                ("--apksigner", args.apksigner),
                ("--evidence-output", args.evidence_output),
                ("--comment-output", args.comment_output),
                ("--owner-approved-binding", args.owner_approved_binding),
            )
            if value is None
        ]
        if missing:
            parser.error("live published-release mode requires " + ", ".join(missing))
        try:
            _validate_output_locations(
                [args.evidence_output, args.comment_output],
                artifact_dirs=[
                    args.windows_dir,
                    args.android_dir,
                    args.published_release_dir,
                ],
            )
            report = collect_published_release_evidence(
                windows_dir=args.windows_dir,
                android_dir=args.android_dir,
                published_release_dir=args.published_release_dir,
                gh=args.gh,
                apksigner=args.apksigner,
                java=args.java,
                version=args.version,
                commit=args.commit,
                run_url=args.run_url,
                expected_android_cert_sha256=args.expected_android_cert_sha256,
                owner_approved_binding=args.owner_approved_binding,
                repo=args.repo,
                runner=SubprocessRunner(),
            )
            _validate_output_locations(
                [args.evidence_output, args.comment_output],
                artifact_dirs=[
                    args.windows_dir,
                    args.android_dir,
                    args.published_release_dir,
                ],
            )
            _write_new_outputs([
                (
                    args.evidence_output,
                    json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
                ),
                (args.comment_output, render_published_release_issue_comment(report)),
            ])
        except (ValueError, OSError, UnicodeError) as exc:
            detail = str(exc) if isinstance(exc, ValueError) else "local filesystem validation failed"
            print(f"live published-release evidence validation failed: {detail}", file=sys.stderr)
            return 1
        return 0

    if args.require_live_final_draft:
        if args.no_fail or args.output is not None or args.json:
            parser.error("live final-draft mode forbids --no-fail, --output, and --json")
        if args.published_release_dir is not None:
            parser.error("live final-draft mode forbids --published-release-dir")
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
            _validate_output_locations(
                [args.evidence_output, args.comment_output],
                artifact_dirs=[args.windows_dir, args.android_dir, args.draft_release_dir],
            )
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
            _validate_output_locations(
                [args.evidence_output, args.comment_output],
                artifact_dirs=[args.windows_dir, args.android_dir, args.draft_release_dir],
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
            args.published_release_dir,
            args.gh,
            args.apksigner,
            args.java,
            args.evidence_output,
            args.comment_output,
            args.owner_approved_binding,
        )
    ) or args.publication_projection_stdout:
        parser.error(
            "live evidence arguments require --require-live-final-draft or "
            "--require-live-published-release"
        )
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
