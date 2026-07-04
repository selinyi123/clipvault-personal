"""Unit tests for release manifest/checksum verification."""

import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name):
    script = _ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_candidate_manifest = _load_script("release_candidate_manifest")
verify_release_manifest = _load_script("verify_release_manifest")


def _build_fixture(tmp_path):
    (tmp_path / "ClipVault-Desktop-v1.6.0-portable.exe").write_bytes(b"portable")
    (tmp_path / "ClipVault-Setup-v1.6.0.exe").write_bytes(b"installer")
    release_candidate_manifest.build_manifest(
        tmp_path,
        platform="windows",
        version="1.6.0",
        commit="abc123",
    )


def _build_signed_android_fixture(
    tmp_path,
    *,
    include_evidence=True,
    evidence_body=b"Signer #1 certificate SHA-256 digest: abc123\n",
):
    (tmp_path / "ClipVault-Android-v1.6.0-release-signed.apk").write_bytes(b"signed apk")
    if include_evidence:
        (tmp_path / "ANDROID_APKSIGNER_VERIFY.txt").write_bytes(evidence_body)
    release_candidate_manifest.build_manifest(
        tmp_path,
        kind="release",
        platform="android",
        version="1.6.0",
        commit="123release",
        signed=True,
    )


def test_verify_accepts_matching_dry_run_manifest(tmp_path):
    _build_fixture(tmp_path)

    manifest = verify_release_manifest.verify_manifest(
        tmp_path,
        platform="windows",
        version="1.6.0",
        commit="abc123",
        expect_dry_run=True,
    )

    assert manifest["kind"] == "release-candidate-dry-run"
    assert manifest["signed"] is False
    assert manifest["published"] is False
    assert [row["name"] for row in manifest["artifacts"]] == [
        "ClipVault-Desktop-v1.6.0-portable.exe",
        "ClipVault-Setup-v1.6.0.exe",
    ]


def test_verify_rejects_changed_artifact_hash(tmp_path):
    _build_fixture(tmp_path)
    (tmp_path / "ClipVault-Setup-v1.6.0.exe").write_bytes(b"tamper!!!")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_checksum_file_drift(tmp_path):
    _build_fixture(tmp_path)
    (tmp_path / "SHA256SUMS.txt").write_text("wrong\n", encoding="ascii")

    with pytest.raises(ValueError, match="SHA256SUMS"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_dry_run_signed_flag_drift(tmp_path):
    _build_fixture(tmp_path)
    path = tmp_path / "RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["signed"] = True
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="dry-run manifest must be unsigned"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_dry_run_signed_flag_even_without_dry_run_mode(tmp_path):
    _build_fixture(tmp_path)
    path = tmp_path / "RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["signed"] = True
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="dry-run manifest must be unsigned"):
        verify_release_manifest.verify_manifest(tmp_path)


def test_verify_rejects_dry_run_published_flag_even_without_dry_run_mode(tmp_path):
    _build_fixture(tmp_path)
    path = tmp_path / "RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["published"] = True
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="dry-run manifest must be unpublished"):
        verify_release_manifest.verify_manifest(tmp_path)


def test_verify_rejects_unknown_manifest_kind(tmp_path):
    _build_fixture(tmp_path)
    path = tmp_path / "RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["kind"] = "nightly"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest kind must be one of"):
        verify_release_manifest.verify_manifest(tmp_path)


def test_verify_rejects_manifest_artifact_path_traversal(tmp_path):
    _build_fixture(tmp_path)
    path = tmp_path / "RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["name"] = "../ClipVault-Desktop-v1.6.0-portable.exe"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="plain file name"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_manifest_hidden_artifact_names(tmp_path):
    _build_fixture(tmp_path)
    path = tmp_path / "RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["name"] = ".ClipVault-Desktop-v1.6.0-portable.exe"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="artifact name must not be hidden"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_actual_hidden_artifacts(tmp_path):
    _build_fixture(tmp_path)
    (tmp_path / ".ClipVault-extra.txt").write_text("hidden artifact\n", encoding="utf-8")

    with pytest.raises(ValueError, match="artifact name must not be hidden"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_symlink_artifacts(tmp_path):
    _build_fixture(tmp_path)
    artifact = tmp_path / "ClipVault-Desktop-v1.6.0-portable.exe"
    target = tmp_path / "target.exe"
    target.write_bytes(artifact.read_bytes())
    artifact.unlink()
    try:
        artifact.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable in this environment: {exc}")

    with pytest.raises(ValueError, match="must not be a symlink"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_rejects_nested_artifact_directories(tmp_path):
    _build_fixture(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "unexpected.txt").write_text("not covered by manifest\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unexpected subdirectory"):
        verify_release_manifest.verify_manifest(tmp_path, expect_dry_run=True)


def test_verify_signed_android_manifest_requires_apksigner_evidence(tmp_path):
    _build_signed_android_fixture(tmp_path)

    manifest = verify_release_manifest.verify_manifest(
        tmp_path,
        platform="android",
        version="1.6.0",
        commit="123release",
        require_signed=True,
    )

    assert manifest["signed"] is True
    assert [row["name"] for row in manifest["artifacts"]] == [
        "ANDROID_APKSIGNER_VERIFY.txt",
        "ClipVault-Android-v1.6.0-release-signed.apk",
    ]


def test_verify_signed_android_manifest_uses_manifest_platform_for_evidence(tmp_path):
    _build_signed_android_fixture(tmp_path, include_evidence=False)

    with pytest.raises(ValueError, match="ANDROID_APKSIGNER_VERIFY.txt"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            version="1.6.0",
            commit="123release",
            require_signed=True,
        )


def test_verify_rejects_signed_android_manifest_without_apksigner_evidence(tmp_path):
    _build_signed_android_fixture(tmp_path, include_evidence=False)

    with pytest.raises(ValueError, match="ANDROID_APKSIGNER_VERIFY.txt"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="123release",
            require_signed=True,
        )


def test_verify_rejects_empty_android_apksigner_evidence(tmp_path):
    _build_signed_android_fixture(tmp_path, evidence_body=b"")

    with pytest.raises(ValueError, match="must not be empty"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="123release",
            require_signed=True,
        )


def test_verify_rejects_non_apksigner_signed_evidence_text(tmp_path):
    _build_signed_android_fixture(tmp_path, evidence_body=b"not empty\n")

    with pytest.raises(ValueError, match="apksigner --print-certs"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="123release",
            require_signed=True,
        )


def test_verify_rejects_android_release_with_unexpected_signed_apk_name(tmp_path):
    (tmp_path / "renamed.apk").write_bytes(b"signed apk")
    (tmp_path / "ANDROID_APKSIGNER_VERIFY.txt").write_text(
        "Signer #1 certificate SHA-256 digest: abc123\n",
        encoding="utf-8",
    )
    release_candidate_manifest.build_manifest(
        tmp_path,
        kind="release",
        platform="android",
        version="1.6.0",
        commit="123release",
        signed=True,
    )

    with pytest.raises(ValueError, match="missing expected artifact"):
        verify_release_manifest.verify_manifest(
            tmp_path,
            platform="android",
            version="1.6.0",
            commit="123release",
            require_signed=True,
        )


def test_cli_returns_nonzero_for_mismatched_version(tmp_path, capsys):
    _build_fixture(tmp_path)

    rc = verify_release_manifest.main([
        "--artifact-dir",
        str(tmp_path),
        "--platform",
        "windows",
        "--version",
        "9.9.9",
        "--expect-dry-run",
    ])

    captured = capsys.readouterr()
    assert rc == 1
    assert "manifest version mismatch" in captured.err
