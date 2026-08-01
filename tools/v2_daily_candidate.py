#!/usr/bin/env python3
"""Write and verify the provenance receipt for a v2 daily-use candidate.

The writer is intended for the clean GitHub aggregation checkout. It refuses
tracked source changes and binds the candidate version plus locked Rime inputs
to the exact workflow run. The verifier is offline and never signs, installs,
publishes, or reads credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RECEIPT_NAME = "BUILD_RECEIPT.json"
VERSION_FILE = "contracts/v2_candidate_version.json"
LOCK_FILES = (
    "THIRD_PARTY_MANIFEST.yaml",
    "android/app/PLAY_SERVICES_SMS_USER_CONSENT_LOCK.json",
    "android/rime-engine-android/RIME_PRODUCTION_LOCK.json",
    "shared-input/rime/RIME_ASSET_LOCK.json",
    "windows/ime/rime/RIME_SDK_LOCK.json",
)
JOB_NAMES = (
    "desktop_candidate",
    "android_native",
    "windows_native",
    "automated_readiness",
)
RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "candidate_kind",
        "source_commit",
        "source_ref",
        "version",
        "workflow",
        "jobs",
        "locked_inputs",
        "restricted_artifacts_packaged",
    }
)
WORKFLOW_FIELDS = frozenset({"repository", "run_id", "run_attempt", "url"})
VERSION_FIELDS = frozenset(
    {
        "version_name",
        "android_runtime_version_code",
        "android_ime_version_code",
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {path}") from exc


def _candidate_version(root: Path) -> dict[str, Any]:
    value = _read_json(root / VERSION_FILE)
    if not isinstance(value, dict):
        raise ValueError(f"{VERSION_FILE} must be an object")
    version = {
        "version_name": value.get("version_name"),
        "android_runtime_version_code": value.get("android_runtime_version_code"),
        "android_ime_version_code": value.get("android_ime_version_code"),
    }
    if (
        not isinstance(version["version_name"], str)
        or not version["version_name"]
        or not isinstance(version["android_runtime_version_code"], int)
        or version["android_runtime_version_code"] <= 0
        or not isinstance(version["android_ime_version_code"], int)
        or version["android_ime_version_code"] <= 0
    ):
        raise ValueError(f"{VERSION_FILE} has invalid release fields")
    return version


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip().lower()
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None


def _git_tracked_clean(root: Path) -> bool | None:
    commands = (
        ["git", "-C", str(root), "diff", "--quiet", "HEAD", "--"],
        ["git", "-C", str(root), "diff", "--cached", "--quiet", "--"],
    )
    try:
        return all(
            subprocess.run(command, timeout=10, check=False).returncode == 0
            for command in commands
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _validate_commit(value: str) -> str:
    normalized = value.strip().lower()
    if re.fullmatch(r"(?!0{40})[0-9a-f]{40}", normalized) is None:
        raise ValueError("source_commit must be one non-zero 40-character Git SHA")
    return normalized


def _load_release_verifier(root: Path):
    path = root / "scripts/verify_release_manifest.py"
    spec = importlib.util.spec_from_file_location("v2_release_manifest_verifier", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load release verifier: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_receipt(
    root: Path,
    artifact_dir: Path,
    *,
    source_commit: str,
    source_ref: str,
    repository: str,
    run_id: str,
    run_attempt: int,
) -> Path:
    root = root.resolve()
    artifact_dir = artifact_dir.resolve()
    commit = _validate_commit(source_commit)
    if _git_head(root) != commit:
        raise ValueError("source_commit does not match checkout HEAD")
    if _git_tracked_clean(root) is not True:
        raise ValueError("tracked candidate sources must exactly match HEAD")
    if not artifact_dir.is_dir():
        raise ValueError(f"artifact directory does not exist: {artifact_dir}")
    if not source_ref.strip() or not repository.strip():
        raise ValueError("source_ref and repository must not be empty")
    if re.fullmatch(r"[1-9][0-9]*", run_id) is None or run_attempt < 1:
        raise ValueError("workflow run id and attempt must be positive")

    locked_inputs = {}
    for relative in LOCK_FILES:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"locked input is missing: {relative}")
        locked_inputs[relative] = _sha256(path)

    receipt = {
        "schema_version": 1,
        "candidate_kind": "v2-daily-unsigned-internal",
        "source_commit": commit,
        "source_ref": source_ref.strip(),
        "version": _candidate_version(root),
        "workflow": {
            "repository": repository.strip(),
            "run_id": run_id,
            "run_attempt": run_attempt,
            "url": f"https://github.com/{repository.strip()}/actions/runs/{run_id}",
        },
        "jobs": {name: "success" for name in JOB_NAMES},
        "locked_inputs": locked_inputs,
        "restricted_artifacts_packaged": False,
    }
    output = artifact_dir / RECEIPT_NAME
    output.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def verify_bundle(
    root: Path,
    artifact_dir: Path,
    *,
    expected_commit: str | None = None,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    artifact_dir = artifact_dir.resolve()
    receipt = _read_json(artifact_dir / RECEIPT_NAME)
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS:
        raise ValueError("BUILD_RECEIPT.json has unexpected fields")
    if receipt.get("schema_version") != 1:
        raise ValueError("BUILD_RECEIPT.json schema_version must be 1")
    if receipt.get("candidate_kind") != "v2-daily-unsigned-internal":
        raise ValueError("BUILD_RECEIPT.json candidate_kind is invalid")
    commit = _validate_commit(str(receipt.get("source_commit", "")))
    if expected_commit is not None and commit != _validate_commit(expected_commit):
        raise ValueError("BUILD_RECEIPT.json source_commit mismatch")
    if not isinstance(receipt.get("source_ref"), str) or not receipt["source_ref"].strip():
        raise ValueError("BUILD_RECEIPT.json source_ref is invalid")
    if receipt.get("version") != _candidate_version(root):
        raise ValueError("BUILD_RECEIPT.json candidate version mismatch")

    workflow = receipt.get("workflow")
    if not isinstance(workflow, dict) or set(workflow) != WORKFLOW_FIELDS:
        raise ValueError("BUILD_RECEIPT.json workflow is invalid")
    repository = workflow.get("repository")
    run_id = workflow.get("run_id")
    run_attempt = workflow.get("run_attempt")
    if (
        not isinstance(repository, str)
        or not repository.strip()
        or not isinstance(run_id, str)
        or re.fullmatch(r"[1-9][0-9]*", run_id) is None
        or not isinstance(run_attempt, int)
        or run_attempt < 1
        or workflow.get("url")
        != f"https://github.com/{repository}/actions/runs/{run_id}"
    ):
        raise ValueError("BUILD_RECEIPT.json workflow identity is invalid")
    if expected_run_id is not None and run_id != expected_run_id:
        raise ValueError("BUILD_RECEIPT.json workflow run mismatch")
    if receipt.get("jobs") != {name: "success" for name in JOB_NAMES}:
        raise ValueError("BUILD_RECEIPT.json job results are incomplete")
    expected_locks = {relative: _sha256(root / relative) for relative in LOCK_FILES}
    if receipt.get("locked_inputs") != expected_locks:
        raise ValueError("BUILD_RECEIPT.json locked input digest mismatch")
    if receipt.get("restricted_artifacts_packaged") is not False:
        raise ValueError("restricted Android artifacts must not be packaged")

    verifier = _load_release_verifier(root)
    manifest = verifier.verify_manifest(
        artifact_dir,
        platform="v2-daily",
        version=receipt["version"]["version_name"],
        commit=commit,
        expect_dry_run=True,
    )
    return {"receipt": receipt, "manifest": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)

    writer = subparsers.add_parser("write-receipt")
    writer.add_argument("--artifact-dir", type=Path, required=True)
    writer.add_argument("--source-commit", required=True)
    writer.add_argument("--source-ref", required=True)
    writer.add_argument("--repository", required=True)
    writer.add_argument("--run-id", required=True)
    writer.add_argument("--run-attempt", type=int, required=True)

    verifier = subparsers.add_parser("verify")
    verifier.add_argument("--artifact-dir", type=Path, required=True)
    verifier.add_argument("--expected-commit")
    verifier.add_argument("--expected-run-id")
    args = parser.parse_args(argv)

    try:
        if args.command == "write-receipt":
            write_receipt(
                args.root,
                args.artifact_dir,
                source_commit=args.source_commit,
                source_ref=args.source_ref,
                repository=args.repository,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
            )
            print("wrote v2 daily candidate build receipt")
        else:
            verify_bundle(
                args.root,
                args.artifact_dir,
                expected_commit=args.expected_commit,
                expected_run_id=args.expected_run_id,
            )
            print("verified v2 daily candidate receipt, manifest, and checksums")
    except ValueError as exc:
        print(f"v2 daily candidate verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
