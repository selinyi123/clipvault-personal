"""Unit tests for release-candidate manifest/checksum generation."""

import importlib.util
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release_candidate_manifest.py"
_spec = importlib.util.spec_from_file_location("release_candidate_manifest", _SCRIPT)
release_candidate_manifest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_candidate_manifest)


def test_build_manifest_writes_checksums_and_unsigned_manifest(tmp_path):
    (tmp_path / "ClipVault-Desktop-v1.6.0-portable.exe").write_bytes(b"portable")
    (tmp_path / "ClipVault-Setup-v1.6.0.exe").write_bytes(b"installer")
    expected_artifacts = [
        {
            "name": "ClipVault-Desktop-v1.6.0-portable.exe",
            "bytes": 8,
            "sha256": "01e782826ae5182220bd6158f883d01ceb1bce659dc020e7c511f802a9aa7737",
        },
        {
            "name": "ClipVault-Setup-v1.6.0.exe",
            "bytes": 9,
            "sha256": "9c0d294c05fc1d88d698034609bb81c0c69196327594e4c69d2915c80fd9850c",
        },
    ]

    release_candidate_manifest.build_manifest(
        tmp_path,
        platform="windows",
        version="1.6.0",
        commit="abc123",
    )

    checksums = (tmp_path / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
    assert checksums == [
        f"{row['sha256']}  {row['name']}"
        for row in expected_artifacts
    ]

    manifest = json.loads((tmp_path / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["kind"] == "release-candidate-dry-run"
    assert manifest["platform"] == "windows"
    assert manifest["version"] == "1.6.0"
    assert manifest["commit"] == "abc123"
    assert manifest["signed"] is False
    assert manifest["published"] is False
    assert manifest["artifacts"] == expected_artifacts


def test_existing_manifest_files_are_not_hashed_again(tmp_path):
    (tmp_path / "ClipVault-Android-v1.6.0-debug.apk").write_bytes(b"debug")
    (tmp_path / "SHA256SUMS.txt").write_text("old\n", encoding="ascii")
    (tmp_path / "RELEASE_MANIFEST.json").write_text("old\n", encoding="utf-8")

    release_candidate_manifest.main([
        "--artifact-dir", str(tmp_path),
        "--platform", "android",
        "--version", "1.6.0",
        "--commit", "def456",
    ])

    manifest = json.loads((tmp_path / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    assert [row["name"] for row in manifest["artifacts"]] == ["ClipVault-Android-v1.6.0-debug.apk"]
    assert "SHA256SUMS.txt" not in (tmp_path / "SHA256SUMS.txt").read_text(encoding="ascii")
