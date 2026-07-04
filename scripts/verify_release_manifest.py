#!/usr/bin/env python3
"""Verify staged release artifact checksums and manifest metadata.

This is an evidence-integrity gate. It proves that the files in a staged
artifact directory match the generated manifest and SHA256SUMS file. It does
not prove platform signing by itself; signed release verification remains a
separate owner-controlled gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

EXCLUDED_NAMES = {"SHA256SUMS.txt", "RELEASE_MANIFEST.json"}
ANDROID_APKSIGNER_EVIDENCE = "ANDROID_APKSIGNER_VERIFY.txt"


def _validate_artifact_name(name: str) -> None:
    if not name:
        raise ValueError("artifact name must not be empty")
    if not name.isascii():
        raise ValueError(f"artifact name must be ASCII: {name!r}")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ValueError(f"artifact name must not contain control characters: {name!r}")


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError("manifest root must be an object")
    return data


def _artifact_path(artifact_dir: Path, name: str) -> Path:
    _validate_artifact_name(name)
    candidate = Path(name)
    if candidate.name != name or candidate.is_absolute():
        raise ValueError(f"artifact name must be a plain file name: {name!r}")
    return artifact_dir / name


def _actual_row(path: Path) -> dict[str, str | int]:
    data = path.read_bytes()
    return {
        "name": path.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _actual_artifacts(artifact_dir: Path) -> dict[str, dict[str, str | int]]:
    actual: dict[str, dict[str, str | int]] = {}
    for path in sorted(artifact_dir.iterdir(), key=lambda p: p.name):
        if path.name in EXCLUDED_NAMES:
            continue
        if path.is_symlink():
            raise ValueError(f"artifact must not be a symlink: {path.name}")
        if path.is_dir():
            raise ValueError(f"artifact directory must be flat; unexpected subdirectory: {path.name}")
        if not path.is_file():
            raise ValueError(f"artifact must be a regular file: {path.name}")
        _validate_artifact_name(path.name)
        actual[path.name] = _actual_row(path)
    return actual


def _manifest_artifacts(manifest: dict[str, Any]) -> list[dict[str, str | int]]:
    rows = manifest.get("artifacts")
    if not isinstance(rows, list) or not rows:
        raise ValueError("manifest artifacts must be a non-empty list")

    names: set[str] = set()
    normalized: list[dict[str, str | int]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"artifact row {index} must be an object")
        name = row.get("name")
        size = row.get("bytes")
        digest = row.get("sha256")
        if not isinstance(name, str):
            raise ValueError(f"artifact row {index} has invalid name")
        _artifact_path(Path("."), name)
        if not isinstance(size, int) or size < 0:
            raise ValueError(f"artifact row {index} has invalid bytes")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"artifact row {index} has invalid sha256")
        if name in names:
            raise ValueError(f"duplicate artifact in manifest: {name}")
        names.add(name)
        normalized.append({"name": name, "bytes": size, "sha256": digest})
    if [row["name"] for row in normalized] != sorted(row["name"] for row in normalized):
        raise ValueError("manifest artifacts must be sorted by name")
    return normalized


def _verify_checksums(artifact_dir: Path, rows: list[dict[str, str | int]]) -> None:
    expected = "\n".join(f"{row['sha256']}  {row['name']}" for row in rows) + "\n"
    path = artifact_dir / "SHA256SUMS.txt"
    try:
        actual = path.read_text(encoding="ascii")
    except FileNotFoundError as exc:
        raise ValueError(f"checksums file not found: {path}") from exc
    if actual != expected:
        raise ValueError("SHA256SUMS.txt does not match manifest artifacts")


def _verify_android_signed_evidence(
    actual: dict[str, dict[str, str | int]],
) -> None:
    if not any(name.endswith(".apk") for name in actual):
        raise ValueError("signed Android manifest must include an APK artifact")
    evidence = actual.get(ANDROID_APKSIGNER_EVIDENCE)
    if evidence is None:
        raise ValueError(
            f"signed Android manifest must include {ANDROID_APKSIGNER_EVIDENCE}"
        )
    if int(evidence["bytes"]) <= 0:
        raise ValueError(f"{ANDROID_APKSIGNER_EVIDENCE} must not be empty")


def verify_manifest(
    artifact_dir: Path,
    *,
    platform: str | None = None,
    version: str | None = None,
    commit: str | None = None,
    expect_dry_run: bool = False,
    require_signed: bool = False,
    require_published: bool = False,
) -> dict[str, Any]:
    artifact_dir = artifact_dir.resolve()
    if not artifact_dir.is_dir():
        raise ValueError(f"artifact directory does not exist or is not a directory: {artifact_dir}")
    manifest = _read_manifest(artifact_dir / "RELEASE_MANIFEST.json")

    if manifest.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    if platform is not None and manifest.get("platform") != platform:
        raise ValueError(f"manifest platform mismatch: expected {platform!r}, got {manifest.get('platform')!r}")
    if version is not None and manifest.get("version") != version:
        raise ValueError(f"manifest version mismatch: expected {version!r}, got {manifest.get('version')!r}")
    if commit is not None and manifest.get("commit") != commit:
        raise ValueError(f"manifest commit mismatch: expected {commit!r}, got {manifest.get('commit')!r}")

    if expect_dry_run:
        if manifest.get("kind") != "release-candidate-dry-run":
            raise ValueError("dry-run manifest kind must be release-candidate-dry-run")
        if manifest.get("signed") is not False:
            raise ValueError("dry-run manifest must be unsigned")
        if manifest.get("published") is not False:
            raise ValueError("dry-run manifest must be unpublished")
    if require_signed and manifest.get("signed") is not True:
        raise ValueError("signed release manifest must record signed=true")
    if require_published and manifest.get("published") is not True:
        raise ValueError("published release manifest must record published=true")

    rows = _manifest_artifacts(manifest)
    actual = _actual_artifacts(artifact_dir)
    manifest_names = [row["name"] for row in rows]
    actual_names = sorted(actual)
    if manifest_names != actual_names:
        raise ValueError(f"artifact set mismatch: manifest={manifest_names!r}, actual={actual_names!r}")

    for row in rows:
        name = str(row["name"])
        _artifact_path(artifact_dir, name)
        actual_row = actual[name]
        if row["bytes"] != actual_row["bytes"]:
            raise ValueError(f"bytes mismatch for {name}: manifest={row['bytes']}, actual={actual_row['bytes']}")
        if row["sha256"] != actual_row["sha256"]:
            raise ValueError(f"sha256 mismatch for {name}: manifest={row['sha256']}, actual={actual_row['sha256']}")

    if manifest.get("platform") == "android" and require_signed:
        _verify_android_signed_evidence(actual)

    _verify_checksums(artifact_dir, rows)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify RELEASE_MANIFEST.json and SHA256SUMS.txt for staged artifacts.")
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--platform", choices=("windows", "android"))
    parser.add_argument("--version")
    parser.add_argument("--commit")
    parser.add_argument("--expect-dry-run", action="store_true")
    parser.add_argument("--require-signed", action="store_true")
    parser.add_argument("--require-published", action="store_true")
    args = parser.parse_args(argv)

    try:
        manifest = verify_manifest(
            args.artifact_dir,
            platform=args.platform,
            version=args.version,
            commit=args.commit,
            expect_dry_run=args.expect_dry_run,
            require_signed=args.require_signed,
            require_published=args.require_published,
        )
    except ValueError as exc:
        print(f"release manifest verification failed: {exc}", file=sys.stderr)
        return 1

    print(
        "verified release manifest: "
        f"{manifest.get('platform')} {manifest.get('version')} "
        f"({len(manifest.get('artifacts', []))} artifacts)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
