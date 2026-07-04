#!/usr/bin/env python3
"""Create release artifact checksums and a machine-readable manifest.

The default mode stays safe for release-candidate dry runs: artifacts are
recorded as unsigned and unpublished unless the caller explicitly marks the
manifest otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

EXCLUDED_NAMES = {"SHA256SUMS.txt", "RELEASE_MANIFEST.json"}
KINDS = {"release-candidate-dry-run", "release"}
PLATFORMS = {"windows", "android"}


def validate_manifest_value(field: str, value: str) -> None:
    if not value:
        raise ValueError(f"{field} must not be empty")
    if value != value.strip():
        raise ValueError(f"{field} must not have leading or trailing whitespace")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{field} must not contain control characters")


def validate_artifact_name(name: str) -> None:
    if not name:
        raise ValueError("artifact name must not be empty")
    if not name.isascii():
        raise ValueError(f"artifact name must be ASCII: {name!r}")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ValueError(f"artifact name must not contain control characters: {name!r}")


def artifact_rows(artifact_dir: Path) -> list[dict[str, str | int]]:
    if not artifact_dir.is_dir():
        raise ValueError(f"artifact directory does not exist or is not a directory: {artifact_dir}")
    rows: list[dict[str, str | int]] = []
    for path in sorted(artifact_dir.iterdir(), key=lambda p: p.name):
        if path.name in EXCLUDED_NAMES:
            continue
        if path.is_symlink():
            raise ValueError(f"artifact must not be a symlink: {path.name}")
        if path.is_dir():
            raise ValueError(f"artifact directory must be flat; unexpected subdirectory: {path.name}")
        if not path.is_file():
            raise ValueError(f"artifact must be a regular file: {path.name}")
        validate_artifact_name(path.name)
        data = path.read_bytes()
        rows.append({
            "name": path.name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    if not rows:
        raise ValueError(f"no artifact files found in {artifact_dir}")
    return rows


def write_sha256sums(artifact_dir: Path, rows: list[dict[str, str | int]]) -> Path:
    out = artifact_dir / "SHA256SUMS.txt"
    lines = [f"{row['sha256']}  {row['name']}" for row in rows]
    out.write_text("\n".join(lines) + "\n", encoding="ascii")
    return out


def write_manifest(
    artifact_dir: Path,
    *,
    kind: str = "release-candidate-dry-run",
    platform: str,
    version: str,
    commit: str,
    rows: list[dict[str, str | int]],
    signed: bool = False,
    published: bool = False,
) -> Path:
    out = artifact_dir / "RELEASE_MANIFEST.json"
    manifest = {
        "schema_version": 1,
        "kind": kind,
        "platform": platform,
        "version": version,
        "commit": commit,
        "signed": signed,
        "published": published,
        "artifacts": rows,
    }
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def build_manifest(
    artifact_dir: Path,
    *,
    kind: str = "release-candidate-dry-run",
    platform: str,
    version: str,
    commit: str,
    signed: bool = False,
    published: bool = False,
) -> dict[str, Path]:
    if kind not in KINDS:
        raise ValueError(f"unsupported manifest kind: {kind}")
    if platform not in PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")
    validate_manifest_value("version", version)
    validate_manifest_value("commit", commit)
    artifact_dir = artifact_dir.resolve()
    rows = artifact_rows(artifact_dir)
    sums = write_sha256sums(artifact_dir, rows)
    manifest = write_manifest(
        artifact_dir,
        kind=kind,
        platform=platform,
        version=version,
        commit=commit,
        rows=rows,
        signed=signed,
        published=published,
    )
    return {"checksums": sums, "manifest": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write SHA256SUMS.txt and RELEASE_MANIFEST.json for staged artifacts.")
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--platform", required=True, choices=("windows", "android"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--kind", default="release-candidate-dry-run", choices=("release-candidate-dry-run", "release"))
    parser.add_argument("--signed", action="store_true", help="record that staged artifacts are signed")
    parser.add_argument("--published", action="store_true", help="record that staged artifacts are already published")
    args = parser.parse_args(argv)
    build_manifest(
        args.artifact_dir,
        kind=args.kind,
        platform=args.platform,
        version=args.version,
        commit=args.commit,
        signed=args.signed,
        published=args.published,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
