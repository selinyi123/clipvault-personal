#!/usr/bin/env python3
"""Verify staged release artifact checksums and manifest metadata.

This is an evidence-integrity gate. It proves that the files in a staged
artifact directory match the generated manifest and SHA256SUMS file. For a
signed Android release it strictly parses the captured apksigner certificate
and can bind it to an Owner-provided trust anchor. It does not independently
run apksigner or prove where downloaded bytes came from.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

EXCLUDED_NAMES = {"SHA256SUMS.txt", "RELEASE_MANIFEST.json"}
ANDROID_APKSIGNER_EVIDENCE = "ANDROID_APKSIGNER_VERIFY.txt"
MAX_ANDROID_APKSIGNER_EVIDENCE_BYTES = 64 * 1024
ANDROID_CERT_SHA256_RE = re.compile(r"[0-9A-Fa-f]{64}")
ANDROID_SIGNER_CERT_LINE_RE = re.compile(
    r"Signer #(?P<index>[1-9][0-9]*) certificate SHA-256 digest: "
    r"(?P<digest>[0-9A-Fa-f]{64})"
)
KINDS = {"release-candidate-dry-run", "release"}
PLATFORMS = {"windows", "android"}


def normalize_android_cert_sha256(value: str) -> str:
    """Return a canonical Owner certificate digest or reject ambiguous input."""

    if ANDROID_CERT_SHA256_RE.fullmatch(value) is None:
        raise ValueError(
            "expected Android certificate SHA-256 must be exactly 64 unseparated hex characters"
        )
    return value.lower()


def parse_android_signer_cert_sha256(text: str) -> str:
    """Return the sole Signer #1 certificate SHA-256 digest from apksigner."""

    if len(text.encode("utf-8")) > MAX_ANDROID_APKSIGNER_EVIDENCE_BYTES:
        raise ValueError(
            f"{ANDROID_APKSIGNER_EVIDENCE} exceeds "
            f"{MAX_ANDROID_APKSIGNER_EVIDENCE_BYTES} bytes"
        )

    digests: list[str] = []
    for line in text.splitlines():
        match = ANDROID_SIGNER_CERT_LINE_RE.fullmatch(line)
        looks_like_target = (
            "signer #" in line.lower()
            and "certificate" in line.lower()
            and "sha-256" in line.lower()
            and "digest" in line.lower()
        )
        if match is None:
            if looks_like_target:
                raise ValueError(
                    f"{ANDROID_APKSIGNER_EVIDENCE} contains a malformed signer certificate SHA-256 line"
                )
            continue
        if match.group("index") != "1":
            raise ValueError(
                f"{ANDROID_APKSIGNER_EVIDENCE} must contain exactly one Signer #1 certificate"
            )
        digests.append(normalize_android_cert_sha256(match.group("digest")))

    if len(digests) != 1:
        raise ValueError(
            f"{ANDROID_APKSIGNER_EVIDENCE} must contain exactly one "
            "Signer #1 certificate SHA-256 digest"
        )
    return digests[0]


def _validate_artifact_name(name: str) -> None:
    if not name:
        raise ValueError("artifact name must not be empty")
    if name.startswith("."):
        raise ValueError(f"artifact name must not be hidden: {name!r}")
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
    candidate = Path(name)
    if candidate.name != name or candidate.is_absolute():
        raise ValueError(f"artifact name must be a plain file name: {name!r}")
    _validate_artifact_name(name)
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


def _expected_artifact_names(manifest: dict[str, Any]) -> set[str]:
    platform = manifest.get("platform")
    kind = manifest.get("kind")
    version = manifest.get("version")
    signed = manifest.get("signed")
    if platform not in PLATFORMS:
        raise ValueError(f"manifest platform must be one of {sorted(PLATFORMS)!r}")
    if not isinstance(version, str) or not version:
        raise ValueError("manifest version must be a non-empty string")

    if platform == "windows":
        return {
            f"ClipVault-Desktop-v{version}-portable.exe",
            f"ClipVault-Setup-v{version}.exe",
            f"ClipVault-v{version}-LGPL-relink-kit.zip",
        }
    if kind == "release-candidate-dry-run":
        return {
            f"ClipVault-Android-v{version}-debug.apk",
            f"ClipVault-Android-v{version}-release-unsigned.apk",
        }
    if kind == "release":
        if signed is not True:
            raise ValueError("Android release manifest must record signed=true")
        return {
            ANDROID_APKSIGNER_EVIDENCE,
            f"ClipVault-Android-v{version}-release-signed.apk",
        }
    return set()


def _verify_expected_artifacts(
    manifest: dict[str, Any],
    actual: dict[str, dict[str, str | int]],
) -> None:
    expected = _expected_artifact_names(manifest)
    actual_names = set(actual)
    missing = sorted(expected - actual_names)
    if missing:
        raise ValueError(f"missing expected artifact(s): {missing!r}")
    unexpected = sorted(actual_names - expected)
    if unexpected:
        raise ValueError(f"unexpected release artifact(s): {unexpected!r}")


def _verify_android_signed_evidence(
    artifact_dir: Path,
    actual: dict[str, dict[str, str | int]],
    version: str,
) -> str:
    signed_apk = f"ClipVault-Android-v{version}-release-signed.apk"
    if signed_apk not in actual:
        raise ValueError(f"signed Android manifest must include {signed_apk}")
    evidence = actual.get(ANDROID_APKSIGNER_EVIDENCE)
    if evidence is None:
        raise ValueError(
            f"signed Android manifest must include {ANDROID_APKSIGNER_EVIDENCE}"
        )
    evidence_bytes = int(evidence["bytes"])
    if evidence_bytes <= 0:
        raise ValueError(f"{ANDROID_APKSIGNER_EVIDENCE} must not be empty")
    if evidence_bytes > MAX_ANDROID_APKSIGNER_EVIDENCE_BYTES:
        raise ValueError(
            f"{ANDROID_APKSIGNER_EVIDENCE} exceeds "
            f"{MAX_ANDROID_APKSIGNER_EVIDENCE_BYTES} bytes"
        )
    raw_evidence = (artifact_dir / ANDROID_APKSIGNER_EVIDENCE).read_bytes()
    try:
        evidence_text = raw_evidence.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{ANDROID_APKSIGNER_EVIDENCE} must be valid UTF-8"
        ) from exc
    return parse_android_signer_cert_sha256(evidence_text)


def verify_manifest(
    artifact_dir: Path,
    *,
    platform: str | None = None,
    version: str | None = None,
    commit: str | None = None,
    expect_dry_run: bool = False,
    require_signed: bool = False,
    require_published: bool = False,
    expected_android_cert_sha256: str | None = None,
) -> dict[str, Any]:
    artifact_dir = artifact_dir.resolve()
    if not artifact_dir.is_dir():
        raise ValueError(f"artifact directory does not exist or is not a directory: {artifact_dir}")
    manifest = _read_manifest(artifact_dir / "RELEASE_MANIFEST.json")

    if manifest.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    kind = manifest.get("kind")
    if kind not in KINDS:
        raise ValueError(f"manifest kind must be one of {sorted(KINDS)!r}")
    if manifest.get("platform") not in PLATFORMS:
        raise ValueError(f"manifest platform must be one of {sorted(PLATFORMS)!r}")
    if (
        manifest.get("platform") == "android"
        and kind == "release"
        and expected_android_cert_sha256 is None
    ):
        raise ValueError(
            "Android release verification requires expected_android_cert_sha256"
        )
    if platform is not None and manifest.get("platform") != platform:
        raise ValueError(f"manifest platform mismatch: expected {platform!r}, got {manifest.get('platform')!r}")
    if version is not None and manifest.get("version") != version:
        raise ValueError(f"manifest version mismatch: expected {version!r}, got {manifest.get('version')!r}")
    if commit is not None and manifest.get("commit") != commit:
        raise ValueError(f"manifest commit mismatch: expected {commit!r}, got {manifest.get('commit')!r}")

    if kind == "release-candidate-dry-run":
        if manifest.get("signed") is not False:
            raise ValueError("dry-run manifest must be unsigned")
        if manifest.get("published") is not False:
            raise ValueError("dry-run manifest must be unpublished")
    if expect_dry_run:
        if kind != "release-candidate-dry-run":
            raise ValueError("dry-run manifest kind must be release-candidate-dry-run")
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
    _verify_expected_artifacts(manifest, actual)

    for row in rows:
        name = str(row["name"])
        _artifact_path(artifact_dir, name)
        actual_row = actual[name]
        if row["bytes"] != actual_row["bytes"]:
            raise ValueError(f"bytes mismatch for {name}: manifest={row['bytes']}, actual={actual_row['bytes']}")
        if row["sha256"] != actual_row["sha256"]:
            raise ValueError(f"sha256 mismatch for {name}: manifest={row['sha256']}, actual={actual_row['sha256']}")

    if manifest.get("platform") == "android" and (kind == "release" or require_signed):
        assert isinstance(manifest.get("version"), str)
        actual_cert_sha256 = _verify_android_signed_evidence(
            artifact_dir, actual, manifest["version"]
        )
        if expected_android_cert_sha256 is None:
            raise ValueError(
                "signed Android verification requires expected_android_cert_sha256"
            )
        expected_cert_sha256 = normalize_android_cert_sha256(
            expected_android_cert_sha256
        )
        if actual_cert_sha256 != expected_cert_sha256:
            raise ValueError(
                "Android signer certificate SHA-256 does not match the Owner trust anchor"
            )

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
    parser.add_argument("--expected-android-cert-sha256")
    args = parser.parse_args(argv)

    try:
        if args.require_signed and args.expected_android_cert_sha256 is None:
            raise ValueError(
                "--require-signed requires --expected-android-cert-sha256"
            )
        manifest = verify_manifest(
            args.artifact_dir,
            platform=args.platform,
            version=args.version,
            commit=args.commit,
            expect_dry_run=args.expect_dry_run,
            require_signed=args.require_signed,
            require_published=args.require_published,
            expected_android_cert_sha256=args.expected_android_cert_sha256,
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
