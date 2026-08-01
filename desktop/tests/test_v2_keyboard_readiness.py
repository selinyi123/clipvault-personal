"""Unit tests for the v2 daily-use Android keyboard readiness checker."""

import importlib.util
import json
import shutil
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


def _copy_package_boundary(tmp_path: Path) -> None:
    for relative in (
        "android/app/src/main/AndroidManifest.xml",
        "android/app/build.gradle.kts",
        "android/ime-app/src/main/AndroidManifest.xml",
        "android/ime-app/build.gradle.kts",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_ROOT / relative, target)


def test_current_repo_passes_android_static_gates_but_keeps_owner_gate_blocked():
    report = v2_keyboard_readiness.build_report(root=_ROOT)
    gates = _gates(report)

    assert report["status"] == "blocked"
    assert report["blocked"] == 1
    assert gates["standalone IME package boundary"]["status"] == "pass"
    assert gates["standalone input-method XML"]["status"] == "pass"
    assert gates["standalone IME source controls"]["status"] == "pass"
    assert gates["standalone IME static test coverage"]["status"] == "pass"
    assert gates["v2 daily-use Android architecture docs"]["status"] == "pass"
    owner = gates["Owner signed/manual release gate"]
    assert owner["status"] == "blocked"
    assert owner["metadata"]["delegated_to"] == "tools/v2_daily_readiness.py"
    assert owner["metadata"]["synthetic_evidence_allowed"] is False
    assert "Android-local report" in report["scope_note"]
    assert "claim v2 daily-use release readiness" in report["scope_note"]


def test_package_gate_locks_one_standalone_ime_and_no_enabled_runtime_ime():
    gate = v2_keyboard_readiness.check_package_ime_boundary(_ROOT)

    assert gate.status == "pass"
    assert gate.metadata["enabled_runtime_ime_services"] == []
    assert gate.metadata["runtime_ime_services"] == []
    assert gate.metadata["runtime_legacy_ime_sources"] == []
    assert gate.metadata["runtime_legacy_ime_resources"] == []
    assert gate.metadata["runtime_packages_rime"] is False
    assert gate.metadata["standalone_active_ime_services"] == [
        ".ClipVaultIsolatedImeService"
    ]
    assert all(gate.metadata["standalone_service_checks"].values())
    assert "android.permission.INTERNET" not in gate.metadata["standalone_permissions"]
    assert "android.permission.RECEIVE_SMS" not in gate.metadata["standalone_permissions"]


def test_input_method_xml_gate_requires_switching_and_inline_autofill():
    gate = v2_keyboard_readiness.check_input_method_xml(_ROOT)

    assert gate.status == "pass"
    assert gate.metadata["checks"] == {
        "input_method_root": True,
        "switch_back": True,
        "inline_autofill": True,
    }


def test_cli_json_no_fail_emits_machine_readable_owner_block(capsys):
    exit_code = v2_keyboard_readiness.main(
        ["--root", str(_ROOT), "--json", "--no-fail"]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["status"] == "blocked"
    assert report["blocked"] == 1
    assert any(
        gate["name"] == "Owner signed/manual release gate"
        for gate in report["gates"]
    )


def test_cli_returns_nonzero_while_owner_gate_is_blocked(capsys):
    exit_code = v2_keyboard_readiness.main(["--root", str(_ROOT)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Owner signed/manual release gate" in captured.out


def test_package_gate_blocks_when_standalone_service_identity_drifts(tmp_path):
    _copy_package_boundary(tmp_path)
    manifest = tmp_path / "android/ime-app/src/main/AndroidManifest.xml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            ".ClipVaultIsolatedImeService", ".UnexpectedImeService"
        ),
        encoding="utf-8",
    )

    gate = v2_keyboard_readiness.check_package_ime_boundary(tmp_path)

    assert gate.status == "blocked"
    assert "service_name" in gate.metadata["problems"][0]


def test_package_gate_blocks_if_networked_runtime_declares_even_a_disabled_legacy_ime(tmp_path):
    _copy_package_boundary(tmp_path)
    manifest = tmp_path / "android/app/src/main/AndroidManifest.xml"
    text = manifest.read_text(encoding="utf-8")
    injected = """
        <service
            android:name=".ime.ClipVaultPanelImeService"
            android:enabled="false"
            android:exported="true"
            android:permission="android.permission.BIND_INPUT_METHOD" />
"""
    manifest.write_text(text.replace("</application>", injected + "</application>"), encoding="utf-8")

    gate = v2_keyboard_readiness.check_package_ime_boundary(tmp_path)

    assert gate.status == "blocked"
    assert gate.metadata["enabled_runtime_ime_services"] == []
    assert gate.metadata["runtime_ime_services"] == [
        ".ime.ClipVaultPanelImeService"
    ]


@pytest.mark.parametrize(
    "checker_name",
    [
        "check_package_ime_boundary",
        "check_isolated_ime_source",
        "check_architecture_docs",
    ],
)
def test_local_file_checkers_fail_closed_when_repo_root_is_wrong(tmp_path, checker_name):
    checker = getattr(v2_keyboard_readiness, checker_name)

    gate = checker(tmp_path)

    assert gate.status == "blocked"
