"""Unit tests for release-candidate manifest/checksum generation."""

import importlib.util
import json
from pathlib import Path

import pytest

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


def test_cli_can_record_signed_release_manifest(tmp_path):
    (tmp_path / "ClipVault-Android-v1.6.0-release-signed.apk").write_bytes(b"signed apk")

    release_candidate_manifest.main([
        "--artifact-dir", str(tmp_path),
        "--platform", "android",
        "--version", "1.6.0",
        "--commit", "123release",
        "--kind", "release",
        "--signed",
    ])

    manifest = json.loads((tmp_path / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "release"
    assert manifest["platform"] == "android"
    assert manifest["version"] == "1.6.0"
    assert manifest["commit"] == "123release"
    assert manifest["signed"] is True
    assert manifest["published"] is False
    assert manifest["artifacts"] == [{
        "name": "ClipVault-Android-v1.6.0-release-signed.apk",
        "bytes": 10,
        "sha256": "230d29b45dd68dffa113b7384d853b8b690b8fce660d2d88b6a30f5d243d3dc7",
    }]


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"signed": True}, "dry-run manifest must not be marked signed"),
    ({"published": True}, "dry-run manifest must not be marked published"),
])
def test_build_manifest_rejects_dry_run_release_state_flags(tmp_path, kwargs, message):
    (tmp_path / "ClipVault-Android-v1.6.0-debug.apk").write_bytes(b"debug apk")

    with pytest.raises(ValueError, match=message):
        release_candidate_manifest.build_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="dryrun",
            **kwargs,
        )


def test_build_manifest_rejects_missing_artifact_directory(tmp_path):
    with pytest.raises(ValueError, match="artifact directory"):
        release_candidate_manifest.build_manifest(
            tmp_path / "missing",
            platform="windows",
            version="1.6.0",
            commit="abc123",
        )


@pytest.mark.parametrize(("field", "kwargs", "message"), [
    ("version", {"version": "", "commit": "abc123"}, "version must not be empty"),
    ("version", {"version": " 1.6.0", "commit": "abc123"}, "version must not have leading"),
    ("commit", {"version": "1.6.0", "commit": "abc\n123"}, "commit must not contain control"),
])
def test_build_manifest_rejects_invalid_manifest_metadata(tmp_path, field, kwargs, message):
    (tmp_path / "ClipVault-Desktop-v1.6.0-portable.exe").write_bytes(b"portable")

    with pytest.raises(ValueError, match=message):
        release_candidate_manifest.build_manifest(
            tmp_path,
            platform="windows",
            **kwargs,
        )


def test_build_manifest_rejects_non_ascii_artifact_names(tmp_path):
    (tmp_path / "ClipVault-v1.6.0-portablé.exe").write_bytes(b"portable")

    with pytest.raises(ValueError, match="artifact name must be ASCII"):
        release_candidate_manifest.build_manifest(
            tmp_path,
            platform="windows",
            version="1.6.0",
            commit="abc123",
        )


def test_build_manifest_rejects_hidden_artifact_names(tmp_path):
    (tmp_path / ".ClipVault-Desktop-v1.6.0-portable.exe").write_bytes(b"portable")

    with pytest.raises(ValueError, match="artifact name must not be hidden"):
        release_candidate_manifest.build_manifest(
            tmp_path,
            platform="windows",
            version="1.6.0",
            commit="abc123",
        )


def test_build_manifest_rejects_symlink_artifacts(tmp_path):
    target = tmp_path / "real.exe"
    target.write_bytes(b"portable")
    link = tmp_path / "ClipVault-Desktop-v1.6.0-portable.exe"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable in this environment: {exc}")

    with pytest.raises(ValueError, match="must not be a symlink"):
        release_candidate_manifest.build_manifest(
            tmp_path,
            platform="windows",
            version="1.6.0",
            commit="abc123",
        )


def test_build_manifest_rejects_nested_artifact_directories(tmp_path):
    (tmp_path / "ClipVault-Desktop-v1.6.0-portable.exe").write_bytes(b"portable")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "unexpected.txt").write_text("not covered by manifest\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected subdirectory"):
        release_candidate_manifest.build_manifest(
            tmp_path,
            platform="windows",
            version="1.6.0",
            commit="abc123",
        )
