from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_v2_candidate_version_lock_drives_android_and_windows_defaults():
    lock = json.loads(_text("contracts/v2_candidate_version.json"))
    assert lock == {
        "schema_version": 1,
        "candidate_line": "v2-daily",
        "version_name": "2.2.0-dev",
        "android_runtime_version_code": 14,
        "android_ime_version_code": 14,
    }
    assert lock["android_runtime_version_code"] > 13

    runtime = _text("android/app/build.gradle.kts")
    ime = _text("android/ime-app/build.gradle.kts")
    assert '../contracts/v2_candidate_version.json' in runtime
    assert '../contracts/v2_candidate_version.json' in ime
    assert 'v2CandidateVersion["version_name"] as String' in runtime
    assert 'v2CandidateVersion["version_name"] as String' in ime
    assert 'v2CandidateVersion["android_runtime_version_code"]' in runtime
    assert 'v2CandidateVersion["android_ime_version_code"]' in ime

    installer = _text("installer/clipvault-v2-daily.iss")
    match = re.search(r'#define AppVersion "([^"]+)"', installer)
    assert match
    assert match.group(1) == lock["version_name"]


def test_v2_android_modules_do_not_restore_independent_literal_versions():
    for relative in (
        "android/app/build.gradle.kts",
        "android/ime-app/build.gradle.kts",
    ):
        text = _text(relative)
        assert re.search(r'versionName\s*=\s*"', text) is None
        assert re.search(r'versionCode\s*=\s*\d+', text) is None
