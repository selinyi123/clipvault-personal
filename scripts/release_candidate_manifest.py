#!/usr/bin/env python3
"""Create release-candidate checksums and a machine-readable manifest.

This is intentionally a dry-run helper: it records what the workflow built, but
also records that the artifacts are not signed and not published. The signed
release gate remains separate from this packaging preflight.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

EXCLUDED_NAMES = {"SHA256SUMS.txt", "RELEASE_MANIFEST.json"}


def artifact_rows(artifact_dir: Path) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for path in sorted(p for p in artifact_dir.iterdir() if p.is_file() and p.name not in EXCLUDED_NAMES):
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
        "kind": "release-candidate-dry-run",
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
    platform: str,
    version: str,
    commit: str,
    signed: bool = False,
    published: bool = False,
) -> dict[str, Path]:
    artifact_dir = artifact_dir.resolve()
    rows = artifact_rows(artifact_dir)
    sums = write_sha256sums(artifact_dir, rows)
    manifest = write_manifest(
        artifact_dir,
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
    args = parser.parse_args(argv)
    build_manifest(args.artifact_dir, platform=args.platform, version=args.version, commit=args.commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
