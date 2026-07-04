"""Unit tests for the local release artifact evidence helper."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load_script(rel):
    script = _ROOT / rel
    spec = importlib.util.spec_from_file_location(script.stem, script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release_candidate_manifest = _load_script("scripts/release_candidate_manifest.py")
release_artifact_evidence = _load_script("tools/release_artifact_evidence.py")


def _build_windows_release_fixture(path, *, version="1.6.0", commit="a" * 40):
    path.mkdir()
    (path / f"ClipVault-Desktop-v{version}-portable.exe").write_bytes(b"portable")
    (path / f"ClipVault-Setup-v{version}.exe").write_bytes(b"installer")
    release_candidate_manifest.build_manifest(
        path,
        kind="release",
        platform="windows",
        version=version,
        commit=commit,
    )


def _build_android_signed_fixture(
    path,
    *,
    version="1.6.0",
    commit="a" * 40,
    apksigner_body="Signer #1 certificate SHA-256 digest: abc123\n",
):
    path.mkdir()
    (path / f"ClipVault-Android-v{version}-release-signed.apk").write_bytes(b"signed")
    (path / "ANDROID_APKSIGNER_VERIFY.txt").write_text(apksigner_body, encoding="utf-8")
    release_candidate_manifest.build_manifest(
        path,
        kind="release",
        platform="android",
        version=version,
        commit=commit,
        signed=True,
    )


def test_release_artifact_evidence_validates_downloaded_windows_and_android_artifacts(tmp_path):
    commit = "b" * 40
    windows_dir = tmp_path / "windows"
    android_dir = tmp_path / "android"
    _build_windows_release_fixture(windows_dir, commit=commit)
    _build_android_signed_fixture(android_dir, commit=commit)

    report = release_artifact_evidence.validate_evidence(
        windows_dir=windows_dir,
        android_dir=android_dir,
        version="v1.6.0",
        commit=commit,
        run_url="https://github.com/selinyi123/clipvault-personal/actions/runs/123",
    )
    comment = release_artifact_evidence.render_issue_comment(report)

    assert report["status"] == "pass"
    assert report["windows_artifacts"] == [
        "ClipVault-Desktop-v1.6.0-portable.exe",
        "ClipVault-Setup-v1.6.0.exe",
    ]
    assert report["android_artifacts"] == [
        "ANDROID_APKSIGNER_VERIFY.txt",
        "ClipVault-Android-v1.6.0-release-signed.apk",
    ]
    assert "Issue #36" in comment
    assert "does not replace manual QA evidence" in comment
    assert "gh attestation" in comment


def test_release_artifact_evidence_rejects_wrong_repo_run_url(tmp_path):
    commit = "c" * 40
    windows_dir = tmp_path / "windows"
    android_dir = tmp_path / "android"
    _build_windows_release_fixture(windows_dir, commit=commit)
    _build_android_signed_fixture(android_dir, commit=commit)

    with pytest.raises(ValueError, match="run-url repo mismatch"):
        release_artifact_evidence.validate_evidence(
            windows_dir=windows_dir,
            android_dir=android_dir,
            version="v1.6.0",
            commit=commit,
            run_url="https://github.com/other/repo/actions/runs/123",
        )


def test_release_artifact_evidence_rejects_dry_run_android_manifest(tmp_path):
    commit = "d" * 40
    windows_dir = tmp_path / "windows"
    android_dir = tmp_path / "android"
    _build_windows_release_fixture(windows_dir, commit=commit)
    android_dir.mkdir()
    (android_dir / "ClipVault-Android-v1.6.0-debug.apk").write_bytes(b"debug")
    (android_dir / "ClipVault-Android-v1.6.0-release-unsigned.apk").write_bytes(b"unsigned")
    release_candidate_manifest.build_manifest(
        android_dir,
        platform="android",
        version="1.6.0",
        commit=commit,
    )

    with pytest.raises(ValueError, match="signed release manifest"):
        release_artifact_evidence.validate_evidence(
            windows_dir=windows_dir,
            android_dir=android_dir,
            version="v1.6.0",
            commit=commit,
            run_url="https://github.com/selinyi123/clipvault-personal/actions/runs/123",
        )


def test_release_artifact_evidence_rejects_weak_apksigner_evidence(tmp_path):
    commit = "e" * 40
    windows_dir = tmp_path / "windows"
    android_dir = tmp_path / "android"
    _build_windows_release_fixture(windows_dir, commit=commit)
    _build_android_signed_fixture(android_dir, commit=commit, apksigner_body="not empty\n")

    with pytest.raises(ValueError, match="apksigner --print-certs"):
        release_artifact_evidence.validate_evidence(
            windows_dir=windows_dir,
            android_dir=android_dir,
            version="v1.6.0",
            commit=commit,
            run_url="https://github.com/selinyi123/clipvault-personal/actions/runs/123",
        )


def test_cli_writes_rendered_comment(tmp_path):
    commit = "f" * 40
    windows_dir = tmp_path / "windows"
    android_dir = tmp_path / "android"
    output = tmp_path / "comment.md"
    _build_windows_release_fixture(windows_dir, commit=commit)
    _build_android_signed_fixture(android_dir, commit=commit)

    rc = release_artifact_evidence.main([
        "--windows-dir",
        str(windows_dir),
        "--android-dir",
        str(android_dir),
        "--version",
        "v1.6.0",
        "--commit",
        commit,
        "--run-url",
        "https://github.com/selinyi123/clipvault-personal/actions/runs/123",
        "--output",
        str(output),
    ])

    assert rc == 0
    assert "Release artifact evidence draft" in output.read_text(encoding="utf-8")


def test_cli_json_output_contains_artifact_names(tmp_path, capsys):
    commit = "1" * 40
    windows_dir = tmp_path / "windows"
    android_dir = tmp_path / "android"
    _build_windows_release_fixture(windows_dir, commit=commit)
    _build_android_signed_fixture(android_dir, commit=commit)

    rc = release_artifact_evidence.main([
        "--windows-dir",
        str(windows_dir),
        "--android-dir",
        str(android_dir),
        "--commit",
        commit,
        "--run-url",
        "https://github.com/selinyi123/clipvault-personal/actions/runs/123",
        "--json",
    ])

    captured = capsys.readouterr()
    assert rc == 0
    data = json.loads(captured.out)
    assert data["android_artifacts"] == [
        "ANDROID_APKSIGNER_VERIFY.txt",
        "ClipVault-Android-v1.6.0-release-signed.apk",
    ]
