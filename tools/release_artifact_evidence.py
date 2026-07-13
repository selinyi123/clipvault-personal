#!/usr/bin/env python3
"""Validate downloaded v1.6 release artifacts and render Issue #36 evidence.

This is a local evidence helper for the Owner-controlled release gate. It does
not call GitHub, and it does not download GitHub Actions artifacts, trigger
workflows, read secret values, publish releases, post to GitHub, complete
manual QA, or close Issue #36. It does not replace manual QA evidence.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "selinyi123/clipvault-personal"
DEFAULT_VERSION = "v1.6.0"
ISSUE_NUMBER = 36
VERSION_RE = re.compile(r"^v(?P<numeric>[0-9]+\.[0-9]+\.[0-9]+)$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_URL_RE = re.compile(
    r"^https://github\.com/(?P<repo>[^/\s]+/[^/\s]+)/actions/runs/(?P<run_id>[0-9]+)$"
)


def _load_verify_release_manifest():
    script = ROOT / "scripts" / "verify_release_manifest.py"
    spec = importlib.util.spec_from_file_location("verify_release_manifest", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify_release_manifest = _load_verify_release_manifest()


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


def render_issue_comment(report: dict[str, Any]) -> str:
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
    lines.extend([
        "- Android signed release artifacts:",
    ])
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
        "Mandatory provenance follow-up: validate the exact GitHub run metadata and",
        "run `gh attestation verify` for every final binary with repository, workflow, branch, and",
        "exact source/signer commit constraints before this can satisfy Issue #36.",
        "",
        str(report["scope_note"]),
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate downloaded release artifact directories and render an Issue #36 evidence comment.",
    )
    parser.add_argument("--windows-dir", required=True, type=Path)
    parser.add_argument("--android-dir", required=True, type=Path)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--expected-android-cert-sha256", required=True)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--output", type=Path, help="write the rendered Markdown comment to a UTF-8 file")
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="return exit code 0 after printing validation errors",
    )
    args = parser.parse_args(argv)

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
