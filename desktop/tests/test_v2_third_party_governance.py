from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "validate_v2_third_party.py"
SPEC = importlib.util.spec_from_file_location("validate_v2_third_party", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)

FIXTURE_FILES = (
    "THIRD_PARTY_MANIFEST.yaml",
    "THIRD_PARTY_NOTICES.md",
    "android/app/build.gradle.kts",
    "android/app/PLAY_SERVICES_SMS_USER_CONSENT_LOCK.json",
    "android/ime-app/build.gradle.kts",
    "android/rime-engine-android/RIME_PRODUCTION_LOCK.json",
    "android/rime-engine-android/src/main/assets/third_party/NOTICE.txt",
    "shared-input/rime/RIME_ASSET_LOCK.json",
    "shared-input/rime/default.yaml",
    "shared-input/rime/clipvault_pinyin.schema.yaml",
    "shared-input/rime/clipvault_pinyin_private.schema.yaml",
    "shared-input/rime/clipvault_punctuation.yaml",
    "third_party/licenses/README-v2-rime.md",
    "third_party/licenses/yaml-cpp-MIT.txt",
    "third_party/licenses/leveldb-BSD-3-Clause.txt",
    "third_party/licenses/OpenCC-Apache-2.0.txt",
    "third_party/licenses/marisa-trie-COPYING.txt",
    "third_party/licenses/rime-pinyin-simp-Apache-2.0.txt",
    "windows/ime/rime/LICENSE-librime.txt",
    "windows/ime/rime/RIME_SDK_LOCK.json",
    "windows/ime/scripts/Build-ProductionIme.ps1",
    "windows/ime/scripts/Package-ClipVaultIme.ps1",
)


def _copy_fixture(tmp_path: Path) -> Path:
    for relative in FIXTURE_FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


def test_repository_v2_third_party_governance_is_internally_consistent():
    assert validator.validate(ROOT) == []


def test_internal_only_distribution_gate_cannot_be_silently_enabled(tmp_path):
    root = _copy_fixture(tmp_path)
    manifest_path = root / "THIRD_PARTY_MANIFEST.yaml"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["project_license"]["distribution_allowed"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = validator.validate(root)

    assert "internal-only project distribution_allowed must be false" in errors


def test_owner_approved_license_state_requires_file_and_matching_notice(tmp_path):
    root = _copy_fixture(tmp_path)
    manifest_path = root / "THIRD_PARTY_MANIFEST.yaml"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["project_license"] = {
        "status": "approved",
        "license_file": "LICENSE",
        "distribution_allowed": True,
        "note": "Owner-approved project license for this candidate line.",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    errors = validator.validate(root)
    assert "approved project license file is missing: LICENSE" in errors

    (root / "LICENSE").write_text("Approved test license\n", encoding="utf-8")
    notice_path = root / "THIRD_PARTY_NOTICES.md"
    notice = notice_path.read_text(encoding="utf-8")
    notice = notice.replace("distribution_allowed: false", "distribution_allowed: true")
    notice = notice.replace(
        "project_license.status: internal_only", "project_license.status: approved"
    )
    notice = notice.replace("license_file: null", "license_file: LICENSE")
    notice = notice.replace(
        "not an approval to distribute", "records Owner approval to distribute"
    )
    notice = notice.replace(
        "No license is granted to third parties",
        "The Owner-selected root LICENSE governs third-party use",
    )
    notice_path.write_text(notice, encoding="utf-8")

    assert validator.validate(root) == []


def test_dictionary_commit_drift_between_production_locks_is_blocked(tmp_path):
    root = _copy_fixture(tmp_path)
    lock_path = root / "android/rime-engine-android/RIME_PRODUCTION_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["sources"]["rime_pinyin_simp"]["commit"] = "a" * 40
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    errors = validator.validate(root)

    assert any(error.startswith("rime-pinyin-simp commit drift:") for error in errors)


def test_missing_repository_license_asset_is_blocked(tmp_path):
    root = _copy_fixture(tmp_path)
    missing = root / "third_party/licenses/OpenCC-Apache-2.0.txt"
    missing.unlink()

    errors = validator.validate(root)

    assert any("OpenCC: repository license asset missing" in error for error in errors)


def test_sms_user_consent_artifact_hash_drift_is_blocked(tmp_path):
    root = _copy_fixture(tmp_path)
    lock_path = root / "android/app/PLAY_SERVICES_SMS_USER_CONSENT_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["artifact"]["sha256"] = "a" * 64
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    errors = validator.validate(root)

    assert any(error.startswith("SMS User Consent AAR sha256 drift:") for error in errors)


def test_sms_user_consent_exact_build_dependency_and_hash_gate_are_required(tmp_path):
    root = _copy_fixture(tmp_path)
    build_path = root / "android/app/build.gradle.kts"
    build = build_path.read_text(encoding="utf-8")
    build = build.replace(
        'implementation("com.google.android.gms:play-services-auth-api-phone:18.2.0")',
        'implementation("com.google.android.gms:play-services-auth-api-phone:18.1.0")',
    )
    build = build.replace("verifySmsUserConsentDependency", "removedDependencyGate")
    build_path.write_text(build, encoding="utf-8")

    errors = validator.validate(root)

    assert any("exact locked SMS User Consent dependency" in error for error in errors)
    assert any("SMS User Consent dependency hash gate" in error for error in errors)


def test_windows_binary_composition_must_remain_unasserted(tmp_path):
    root = _copy_fixture(tmp_path)
    lock_path = root / "windows/ime/rime/RIME_SDK_LOCK.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["official_windows_asset"]["transitive_composition_status"] = "complete"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    errors = validator.validate(root)

    assert "Windows librime lock must not imply an enumerated transitive closure" in errors


def test_cli_pass_does_not_claim_distribution_permission(capsys):
    exit_code = validator.main(["--root", str(ROOT), "--json"])
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["status"] == "pass"
    assert report["distribution_allowed"] is False
    assert "project-license-state consistency" in report["scope_note"]
