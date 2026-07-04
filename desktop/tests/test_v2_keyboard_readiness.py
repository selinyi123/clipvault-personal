"""Unit tests for the local v2.0 dual-IME readiness checker."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "tools" / "v2_keyboard_readiness.py"
_spec = importlib.util.spec_from_file_location("v2_keyboard_readiness", _SCRIPT)
v2_keyboard_readiness = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = v2_keyboard_readiness
_spec.loader.exec_module(v2_keyboard_readiness)


def _gates(report):
    return {gate["name"]: gate for gate in report["gates"]}


def test_current_repo_reports_static_v2_keyboard_evidence_but_keeps_owner_gate_blocked():
    report = v2_keyboard_readiness.build_report(root=_ROOT)
    gates = _gates(report)

    assert report["status"] == "blocked"
    assert report["blocked"] == 1
    assert gates["dual IME manifest registration"]["status"] == "pass"
    assert gates["input-method XML switch-back support"]["status"] == "pass"
    assert gates["Keyboard Lab source controls"]["status"] == "pass"
    assert gates["Panel IME source controls"]["status"] == "pass"
    assert gates["IME privacy/static test coverage"]["status"] == "pass"
    assert gates["v2.0 docs/release boundary"]["status"] == "pass"
    assert gates["Owner/manual release gate"]["status"] == "blocked"
    assert "does not call GitHub" in report["scope_note"]
    assert "claim v2.0 stable" in report["scope_note"]


def test_manifest_gate_locks_exact_two_system_ime_services():
    gate = v2_keyboard_readiness.check_dual_ime_manifest(_ROOT)

    assert gate.status == "pass"
    rows = {row["name"]: row for row in gate.metadata["rows"]}
    assert set(rows) == {
        ".ime.ClipVaultPanelImeService",
        ".ime.ClipVaultFullKeyboardService",
    }
    assert rows[".ime.ClipVaultPanelImeService"]["expected"]["label"] == "ClipVault 面板"
    assert rows[".ime.ClipVaultFullKeyboardService"]["expected"]["label"] == "ClipVault 键盘(实验)"
    for row in rows.values():
        assert all(row["checks"].values())


def test_input_method_xml_gate_requires_switching_support():
    gate = v2_keyboard_readiness.check_input_method_xml(_ROOT)

    assert gate.status == "pass"
    for row in gate.metadata["rows"]:
        assert row["tag"] == "input-method"
        assert row["supports_switching_to_next_input_method"] is True


def test_cli_json_no_fail_emits_machine_readable_blocked_report(capsys):
    exit_code = v2_keyboard_readiness.main(["--root", str(_ROOT), "--json", "--no-fail"])

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert exit_code == 0
    assert report["status"] == "blocked"
    assert report["blocked"] == 1
    assert any(gate["name"] == "Owner/manual release gate" for gate in report["gates"])


def test_cli_returns_nonzero_when_owner_gate_is_still_blocked(capsys):
    exit_code = v2_keyboard_readiness.main(["--root", str(_ROOT)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Owner/manual release gate" in captured.out


def test_manifest_gate_blocks_when_an_ime_label_drifts(tmp_path):
    source_manifest = _ROOT / "android/app/src/main/AndroidManifest.xml"
    target_manifest = tmp_path / "android/app/src/main/AndroidManifest.xml"
    target_manifest.parent.mkdir(parents=True)
    text = source_manifest.read_text(encoding="utf-8").replace("ClipVault 面板", "ClipVault")
    target_manifest.write_text(text, encoding="utf-8")

    gate = v2_keyboard_readiness.check_dual_ime_manifest(tmp_path)

    assert gate.status == "blocked"
    assert any("failed manifest checks: label" in problem for problem in gate.metadata["problems"])


@pytest.mark.parametrize("checker_name", [
    "check_keyboard_lab_source",
    "check_panel_ime_source",
    "check_docs_release_boundaries",
])
def test_local_file_checkers_fail_closed_when_repo_root_is_wrong(tmp_path, checker_name):
    checker = getattr(v2_keyboard_readiness, checker_name)

    gate = checker(tmp_path)

    assert gate.status == "blocked"
