from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40
VERSION = "2.2.0-dev"


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


candidate = _load("v2_daily_candidate_tool", "tools/v2_daily_candidate.py")
manifest_writer = _load(
    "v2_daily_candidate_receipt_manifest_writer",
    "scripts/release_candidate_manifest.py",
)


def _stage_unsigned_files(directory: Path) -> None:
    names = {
        "CANDIDATE-NOT-A-RELEASE.txt",
        "ClipVault-Desktop-v2-unsigned.exe",
        "ClipVault-IME-v2-unsigned.apk",
        "ClipVault-Runtime-v2-unsigned.apk",
        f"ClipVault-v2-Daily-Setup-v{VERSION}.exe",
        "ClipVault-Windows-IME-v2-unsigned.zip",
    }
    for name in names:
        (directory / name).write_bytes(f"receipt-test:{name}".encode())


def _write_receipt(monkeypatch, directory: Path) -> None:
    monkeypatch.setattr(candidate, "_git_head", lambda _: COMMIT)
    monkeypatch.setattr(candidate, "_git_tracked_clean", lambda _: True)
    candidate.write_receipt(
        ROOT,
        directory,
        source_commit=COMMIT,
        source_ref="refs/heads/codex/v2-daily-integration",
        repository="owner/clipvault",
        run_id="12345",
        run_attempt=2,
    )


def test_receipt_and_manifest_verify_as_one_candidate(monkeypatch, tmp_path):
    _stage_unsigned_files(tmp_path)
    _write_receipt(monkeypatch, tmp_path)
    manifest_writer.build_manifest(
        tmp_path,
        platform="v2-daily",
        version=VERSION,
        commit=COMMIT,
    )

    verified = candidate.verify_bundle(
        ROOT,
        tmp_path,
        expected_commit=COMMIT,
        expected_run_id="12345",
    )

    assert verified["receipt"]["workflow"]["run_attempt"] == 2
    assert verified["receipt"]["restricted_artifacts_packaged"] is False
    assert (
        "android/app/PLAY_SERVICES_SMS_USER_CONSENT_LOCK.json"
        in verified["receipt"]["locked_inputs"]
    )
    assert verified["manifest"]["commit"] == COMMIT


def test_receipt_rejects_dirty_sources_and_lock_tampering(monkeypatch, tmp_path):
    _stage_unsigned_files(tmp_path)
    monkeypatch.setattr(candidate, "_git_head", lambda _: COMMIT)
    monkeypatch.setattr(candidate, "_git_tracked_clean", lambda _: False)
    with pytest.raises(ValueError, match="exactly match HEAD"):
        candidate.write_receipt(
            ROOT,
            tmp_path,
            source_commit=COMMIT,
            source_ref="refs/heads/test",
            repository="owner/clipvault",
            run_id="1",
            run_attempt=1,
        )

    _write_receipt(monkeypatch, tmp_path)
    manifest_writer.build_manifest(
        tmp_path,
        platform="v2-daily",
        version=VERSION,
        commit=COMMIT,
    )
    receipt_path = tmp_path / candidate.RECEIPT_NAME
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    first_lock = next(iter(receipt["locked_inputs"]))
    receipt["locked_inputs"][first_lock] = "0" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError, match="locked input digest mismatch"):
        candidate.verify_bundle(ROOT, tmp_path)
