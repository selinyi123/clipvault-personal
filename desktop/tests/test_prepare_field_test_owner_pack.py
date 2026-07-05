"""Unit tests for the v1.7 Owner field-test action-pack helper."""

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "tools" / "prepare_field_test_owner_pack.py"
_spec = importlib.util.spec_from_file_location("prepare_field_test_owner_pack", _SCRIPT)
prepare_field_test_owner_pack = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = prepare_field_test_owner_pack
_spec.loader.exec_module(prepare_field_test_owner_pack)


def _fake_report():
    return {
        "repo": "selinyi123/clipvault-personal",
        "branch": "main",
        "source_version": "1.6.0",
        "main_sha": "a" * 40,
        "status": "blocked",
        "blocked": 1,
        "warnings": 0,
        "ci_run_url": "https://github.com/selinyi123/clipvault-personal/actions/runs/111",
        "candidate_run_url": "https://github.com/selinyi123/clipvault-personal/actions/runs/222",
        "gates": [
            {"name": "current main commit", "status": "pass"},
            {"name": "CI", "status": "pass"},
            {"name": "Release candidate dry run", "status": "pass"},
            {
                "name": "candidate artifact inventory",
                "status": "pass",
                "metadata": {
                    "artifacts": [
                        {
                            "name": "clipvault-windows-release-candidate",
                            "id": 10,
                            "size_in_bytes": 123,
                            "expires_at": "2026-10-03T00:00:00Z",
                            "digest": "sha256:" + "1" * 64,
                        },
                        {
                            "name": "clipvault-android-release-candidate",
                            "id": 11,
                            "size_in_bytes": 456,
                            "expires_at": "2026-10-03T00:00:00Z",
                            "digest": "sha256:" + "2" * 64,
                        },
                    ],
                },
            },
            {
                "name": "Issue #82",
                "status": "blocked",
                "metadata": {"unchecked_items": ["Owner runs Android smoke."]},
            },
            {"name": "Issue #82 current-run evidence markers", "status": "warn"},
        ],
    }


def test_owner_pack_writes_prefilled_files_without_calling_github(tmp_path, monkeypatch):
    monkeypatch.setattr(
        prepare_field_test_owner_pack.field_test_readiness,
        "build_report",
        lambda **kwargs: _fake_report(),
    )

    summary = prepare_field_test_owner_pack.build_pack(
        output_dir=tmp_path / "pack",
        tester="Owner",
        tested_at="2026-07-05T10:00:00+08:00",
    )

    paths = summary["paths"]
    guide = Path(paths["guide"]).read_text(encoding="utf-8")
    evidence = json.loads(Path(paths["evidence_json"]).read_text(encoding="utf-8"))
    comment = Path(paths["issue_comment"]).read_text(encoding="utf-8")

    assert summary["field_test_ready"] is False
    assert summary["item_counts"] == {"blocked": 15, "fail": 0, "pass": 0}
    assert evidence["target_commit"] == "a" * 40
    assert evidence["ci_run_url"].endswith("/111")
    assert evidence["candidate_run_url"].endswith("/222")
    assert "Status: **BLOCKED**" in comment
    assert "not signed/final release evidence" in comment
    assert "clipvault-windows-release-candidate" in guide
    assert "Owner action pack only" in guide


def test_owner_pack_guide_keeps_smoke_commands_isolated_and_reversible(tmp_path, monkeypatch):
    monkeypatch.setattr(
        prepare_field_test_owner_pack.field_test_readiness,
        "build_report",
        lambda **kwargs: _fake_report(),
    )

    summary = prepare_field_test_owner_pack.build_pack(
        output_dir=tmp_path / "pack",
        tester="Owner",
        tested_at="2026-07-05T10:00:00+08:00",
    )
    guide = Path(summary["paths"]["guide"]).read_text(encoding="utf-8")

    assert 'vault_path = "$vaultPathToml"' in guide
    assert 'db_path = "$dbPathToml"' in guide
    assert 'port = $port' in guide
    assert "Set-Content -Encoding UTF8 -LiteralPath $configPath" in guide
    assert "$p = $null" in guide
    assert "Start-Sleep -Seconds 5" in guide
    assert "finally {" in guide
    assert "Stop-Process -Id $p.Id -Force" in guide
    assert "$previousIme = (adb shell settings get secure default_input_method).Trim()" in guide
    assert "adb shell ime set $previousIme" in guide
    assert "typed-text canary appeared in logcat" in guide
