"""Unit tests for the v1.7 field-test evidence helper."""

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
field_test_evidence = _load_script("tools/field_test_evidence.py")


def _build_candidate_fixtures(tmp_path, *, version="1.6.0", commit="a" * 40):
    windows_dir = tmp_path / "windows"
    android_dir = tmp_path / "android"
    windows_dir.mkdir()
    android_dir.mkdir()

    (windows_dir / f"ClipVault-Desktop-v{version}-portable.exe").write_bytes(b"portable")
    (windows_dir / f"ClipVault-Setup-v{version}.exe").write_bytes(b"installer")
    release_candidate_manifest.build_manifest(
        windows_dir,
        platform="windows",
        version=version,
        commit=commit,
    )

    (android_dir / f"ClipVault-Android-v{version}-debug.apk").write_bytes(b"debug")
    (android_dir / f"ClipVault-Android-v{version}-release-unsigned.apk").write_bytes(b"unsigned")
    release_candidate_manifest.build_manifest(
        android_dir,
        platform="android",
        version=version,
        commit=commit,
    )
    return windows_dir, android_dir


def _fill_all_rows_as_pass(data):
    for section in data["sections"].values():
        for item in section["items"].values():
            item["status"] = "pass"
            item["evidence"] = "Observed on test device."
            item["next_step"] = ""


def test_verify_artifacts_builds_partial_blocked_issue82_comment(tmp_path):
    commit = "b" * 40
    windows_dir, android_dir = _build_candidate_fixtures(tmp_path, commit=commit)

    data = field_test_evidence.build_artifact_verified_template(
        windows_dir=windows_dir,
        android_dir=android_dir,
        target_commit=commit,
        ci_run_url="https://github.com/selinyi123/clipvault-personal/actions/runs/111",
        candidate_run_url="https://github.com/selinyi123/clipvault-personal/actions/runs/222",
        tester="codex-agent",
        tested_at="2026-07-04T18:00:00+08:00",
    )
    result = field_test_evidence.validate_evidence(data)
    markdown = field_test_evidence.render_markdown(data, result)

    assert result.ok is True
    assert result.field_test_ready is False
    assert result.item_counts == {"blocked": 12, "fail": 0, "pass": 3}
    assert "Status: **BLOCKED**" in markdown
    assert "Issue #82" in markdown
    assert "candidate-only artifact verification" in markdown
    assert "ClipVault-Android-v1.6.0-release-unsigned.apk" in markdown
    assert "not signed/final release evidence" in markdown
    assert "- Windows environment: OS pending Owner Windows smoke" in markdown
    assert "- Android device: model pending Owner Android smoke" in markdown
    assert "Android: pending Owner Android smoke, Android pending Owner Android smoke" not in markdown


def test_cli_verify_artifacts_writes_partial_comment_with_no_fail(tmp_path):
    commit = "c" * 40
    windows_dir, android_dir = _build_candidate_fixtures(tmp_path, commit=commit)
    output = tmp_path / "issue82.md"

    rc = field_test_evidence.main([
        "--verify-artifacts",
        "--windows-dir",
        str(windows_dir),
        "--android-dir",
        str(android_dir),
        "--target-commit",
        commit,
        "--ci-run-url",
        "https://github.com/selinyi123/clipvault-personal/actions/runs/111",
        "--candidate-run-url",
        "https://github.com/selinyi123/clipvault-personal/actions/runs/222",
        "--tester",
        "codex-agent",
        "--tested-at",
        "2026-07-04T18:00:00+08:00",
        "--output",
        str(output),
        "--no-fail",
    ])

    assert rc == 0
    rendered = output.read_text(encoding="utf-8")
    assert "v1.7 field-test evidence" in rendered
    assert "12 blocked" in rendered


def test_cli_verify_artifacts_returns_incomplete_without_no_fail(tmp_path):
    commit = "d" * 40
    windows_dir, android_dir = _build_candidate_fixtures(tmp_path, commit=commit)

    rc = field_test_evidence.main([
        "--verify-artifacts",
        "--windows-dir",
        str(windows_dir),
        "--android-dir",
        str(android_dir),
        "--target-commit",
        commit,
        "--ci-run-url",
        "https://github.com/selinyi123/clipvault-personal/actions/runs/111",
        "--candidate-run-url",
        "https://github.com/selinyi123/clipvault-personal/actions/runs/222",
        "--tester",
        "codex-agent",
        "--tested-at",
        "2026-07-04T18:00:00+08:00",
    ])

    assert rc == 2


def test_validate_evidence_rejects_unsigned_release_apk_as_install_package():
    data = field_test_evidence.build_template()
    data.update({
        "target_commit": "e" * 40,
        "ci_run_url": "https://github.com/selinyi123/clipvault-personal/actions/runs/111",
        "candidate_run_url": "https://github.com/selinyi123/clipvault-personal/actions/runs/222",
        "tester": "owner",
        "tested_at": "2026-07-04T18:00:00+08:00",
    })
    data["windows_environment"]["os"] = "Windows 11"
    data["windows_environment"]["portable_or_installer"] = "ClipVault-Setup-v1.6.0.exe"
    data["android_device"]["model"] = "Pixel"
    data["android_device"]["android_version"] = "15"
    data["android_device"]["install_apk"] = "ClipVault-Android-v1.6.0-release-unsigned.apk"
    _fill_all_rows_as_pass(data)

    result = field_test_evidence.validate_evidence(data)

    assert result.ok is False
    assert any("unsigned release APK" in error for error in result.errors)


def test_verify_artifacts_rejects_wrong_commit(tmp_path):
    windows_dir, android_dir = _build_candidate_fixtures(tmp_path, commit="f" * 40)

    with pytest.raises(ValueError, match="manifest commit mismatch"):
        field_test_evidence.verify_candidate_artifacts(
            windows_dir=windows_dir,
            android_dir=android_dir,
            source_version="1.6.0",
            commit="1" * 40,
        )


def test_template_json_contains_issue82_scope_note(capsys):
    rc = field_test_evidence.main(["--template"])

    captured = capsys.readouterr()
    assert rc == 0
    data = json.loads(captured.out)
    assert data["field_test_label"] == "v1.7-field-test"
    assert "Issue #36" in data["scope_note"]
    assert "not signed/final release evidence" in data["scope_note"]
