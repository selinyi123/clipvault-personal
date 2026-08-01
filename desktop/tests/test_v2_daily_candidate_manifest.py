from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manifest_writer = _load(
    "v2_daily_release_candidate_manifest",
    "scripts/release_candidate_manifest.py",
)
manifest_verifier = _load(
    "v2_daily_verify_release_manifest",
    "scripts/verify_release_manifest.py",
)

VERSION = "2.2.0-dev"
COMMIT = "a" * 40
EXPECTED = {
    "BUILD_RECEIPT.json",
    "CANDIDATE-NOT-A-RELEASE.txt",
    "ClipVault-Desktop-v2-unsigned.exe",
    "ClipVault-IME-v2-unsigned.apk",
    "ClipVault-Runtime-v2-unsigned.apk",
    f"ClipVault-v2-Daily-Setup-v{VERSION}.exe",
    "ClipVault-Windows-IME-v2-unsigned.zip",
}


def _stage(directory: Path, names: set[str] = EXPECTED) -> None:
    for name in names:
        (directory / name).write_bytes(f"v2-candidate:{name}".encode())


def _build(directory: Path) -> None:
    manifest_writer.build_manifest(
        directory,
        platform="v2-daily",
        version=VERSION,
        commit=COMMIT,
    )


def test_v2_daily_manifest_binds_exact_flat_artifact_set(tmp_path):
    _stage(tmp_path)
    _build(tmp_path)

    manifest = manifest_verifier.verify_manifest(
        tmp_path,
        platform="v2-daily",
        version=VERSION,
        commit=COMMIT,
        expect_dry_run=True,
    )

    assert manifest["signed"] is False
    assert manifest["published"] is False
    assert {row["name"] for row in manifest["artifacts"]} == EXPECTED


def test_v2_daily_manifest_rejects_missing_or_restricted_artifacts(tmp_path):
    _stage(tmp_path, EXPECTED - {f"ClipVault-v2-Daily-Setup-v{VERSION}.exe"})
    _build(tmp_path)
    with pytest.raises(ValueError, match="missing expected artifact"):
        manifest_verifier.verify_manifest(tmp_path, platform="v2-daily")

    (tmp_path / f"ClipVault-v2-Daily-Setup-v{VERSION}.exe").write_bytes(b"installer")
    (tmp_path / "otpSmsRelay-restricted.apk").write_bytes(b"must-not-ship")
    _build(tmp_path)
    with pytest.raises(ValueError, match="unexpected release artifact"):
        manifest_verifier.verify_manifest(tmp_path, platform="v2-daily")


def test_v2_daily_manifest_cannot_be_marked_as_release(tmp_path):
    _stage(tmp_path)

    with pytest.raises(ValueError, match="unsigned internal candidates only"):
        manifest_writer.build_manifest(
            tmp_path,
            kind="release",
            platform="v2-daily",
            version=VERSION,
            commit=COMMIT,
        )
