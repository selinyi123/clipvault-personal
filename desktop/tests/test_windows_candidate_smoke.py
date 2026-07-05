"""Unit tests for the Windows candidate smoke helper."""

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load_script(rel):
    script = _ROOT / rel
    spec = importlib.util.spec_from_file_location(script.stem, script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


windows_candidate_smoke = _load_script("tools/windows_candidate_smoke.py")


def test_smoke_config_uses_isolated_paths_and_delays_watcher(tmp_path):
    config = windows_candidate_smoke.write_smoke_config(tmp_path, port=54321)
    text = config.read_text(encoding="utf-8")

    assert 'host = "127.0.0.1"' in text
    assert "port = 54321" in text
    assert "enabled = false" in text
    assert "poll_fallback_ms = 600000" in text
    assert (tmp_path / "ClipVaultSmoke" / "vault").is_dir()
    assert str(tmp_path).replace("\\", "/") in text


def test_missing_portable_reports_fail_without_running_processes(tmp_path):
    report = windows_candidate_smoke.build_report(
        windows_dir=tmp_path,
        version="1.6.0",
        work_dir=tmp_path / "work",
        timeout_s=3,
    )

    assert report["ok"] is False
    assert report["status"] == "fail"
    assert "missing Windows portable candidate" in report["evidence"]
    assert report["checks"]["help"]["status"] == "blocked"


def test_already_running_service_exit_is_blocked_not_candidate_failure():
    check = windows_candidate_smoke._service_early_exit_check(
        "ClipVault-Desktop-v1.6.0-portable.exe",
        "ClipVault is already running -- opened the panel.",
    )

    assert check.status == "blocked"
    assert "single-instance lock" in check.evidence
    assert "Close the already-running ClipVault" in check.next_step


def test_cli_writes_json_report_with_no_fail(tmp_path):
    output = tmp_path / "smoke.json"

    rc = windows_candidate_smoke.main([
        "--windows-dir",
        str(tmp_path),
        "--output",
        str(output),
        "--no-fail",
    ])

    assert rc == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "fail"
    assert "scope_note" in report
