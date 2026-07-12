#!/usr/bin/env python3
"""Local read-only readiness report for the v2.0 dual-IME stability lane.

This helper aggregates repository-local evidence for the v2.0 Keyboard Lab
mainline. It intentionally does not call GitHub, trigger workflows, download
artifacts, run device QA, sign or publish releases, close issues, change
version metadata, or claim v2.0 stable.
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
ANDROID_NS = "http://schemas.android.com/apk/res/android"

EXPECTED_IME_SERVICES = {
    ".ime.ClipVaultPanelImeService": {
        "label": "ClipVault 面板",
        "config": "@xml/ime_panel_config",
        "config_path": "android/app/src/main/res/xml/ime_panel_config.xml",
        "source_path": "android/app/src/main/kotlin/com/clipvault/app/ime/ClipVaultPanelImeService.kt",
    },
    ".ime.ClipVaultFullKeyboardService": {
        "label": "ClipVault 键盘(实验)",
        "config": "@xml/ime_full_config",
        "config_path": "android/app/src/main/res/xml/ime_full_config.xml",
        "source_path": "android/app/src/main/kotlin/com/clipvault/app/ime/ClipVaultFullKeyboardService.kt",
    },
}

REQUIRED_ANDROID_TEST_FILES = (
    "android/app/src/test/kotlin/com/clipvault/app/ime/ImeSourceBoundaryTest.kt",
    "android/app/src/test/kotlin/com/clipvault/app/ime/ImeCandidatePrivacySourceTest.kt",
    "android/app/src/test/kotlin/com/clipvault/app/ime/ImePrivacySessionTest.kt",
    "android/app/src/test/kotlin/com/clipvault/app/ime/PanelImePrivacySourceTest.kt",
    "android/app/src/test/kotlin/com/clipvault/app/ime/PrivacyAwareFilterTest.kt",
    "android/app/src/test/kotlin/com/clipvault/app/ime/PanelCandidateTabsTest.kt",
    "android/app/src/test/kotlin/com/clipvault/app/ime/ImeSwitchCompatSourceTest.kt",
)


@dataclass(frozen=True)
class Gate:
    name: str
    status: str
    detail: str
    evidence: str = ""
    next_step: str = ""
    metadata: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
            "next_step": self.next_step,
        }
        if self.metadata is not None:
            data["metadata"] = self.metadata
        return data


def _android_attr(name: str) -> str:
    return f"{{{ANDROID_NS}}}{name}"


def _pass(
    name: str,
    detail: str,
    *,
    evidence: str = "",
    metadata: dict[str, object] | None = None,
) -> Gate:
    return Gate(name=name, status="pass", detail=detail, evidence=evidence, metadata=metadata)


def _blocked(
    name: str,
    detail: str,
    *,
    evidence: str = "",
    next_step: str = "",
    metadata: dict[str, object] | None = None,
) -> Gate:
    return Gate(
        name=name,
        status="blocked",
        detail=detail,
        evidence=evidence,
        next_step=next_step,
        metadata=metadata,
    )


def _warn(
    name: str,
    detail: str,
    *,
    evidence: str = "",
    next_step: str = "",
    metadata: dict[str, object] | None = None,
) -> Gate:
    return Gate(
        name=name,
        status="warn",
        detail=detail,
        evidence=evidence,
        next_step=next_step,
        metadata=metadata,
    )


def _read_text(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _parse_xml(root: Path, rel: str) -> ET.Element:
    return ET.parse(root / rel).getroot()


def _manifest_services(manifest: ET.Element) -> dict[str, ET.Element]:
    app = manifest.find("application")
    if app is None:
        return {}
    services: dict[str, ET.Element] = {}
    for service in app.findall("service"):
        name = service.attrib.get(_android_attr("name"))
        if name:
            services[name] = service
    return services


def _intent_actions(service: ET.Element) -> list[str]:
    return [
        action.attrib.get(_android_attr("name"), "")
        for intent_filter in service.findall("intent-filter")
        for action in intent_filter.findall("action")
    ]


def _metadata_by_name(service: ET.Element) -> dict[str, ET.Element]:
    return {
        metadata.attrib.get(_android_attr("name"), ""): metadata
        for metadata in service.findall("meta-data")
    }


def check_dual_ime_manifest(root: Path) -> Gate:
    rel = "android/app/src/main/AndroidManifest.xml"
    try:
        manifest = _parse_xml(root, rel)
    except (FileNotFoundError, ET.ParseError) as exc:
        return _blocked(
            "dual IME manifest registration",
            "Android manifest could not be read or parsed.",
            evidence=str(exc),
            next_step="Restore a parseable AndroidManifest.xml before auditing v2.0 IME exposure.",
        )

    services = _manifest_services(manifest)
    bind_input_services = {
        name
        for name, service in services.items()
        if service.attrib.get(_android_attr("permission")) == "android.permission.BIND_INPUT_METHOD"
    }
    expected_names = set(EXPECTED_IME_SERVICES)
    problems: list[str] = []
    rows: list[dict[str, object]] = []

    if bind_input_services != expected_names:
        missing = sorted(expected_names - bind_input_services)
        extra = sorted(bind_input_services - expected_names)
        if missing:
            problems.append("missing BIND_INPUT_METHOD services: " + ", ".join(missing))
        if extra:
            problems.append("unexpected BIND_INPUT_METHOD services: " + ", ".join(extra))

    for name, expected in EXPECTED_IME_SERVICES.items():
        service = services.get(name)
        row: dict[str, object] = {"name": name, "expected": expected}
        if service is None:
            problems.append(f"{name} is missing from AndroidManifest.xml")
            row["present"] = False
            rows.append(row)
            continue
        row["present"] = True

        checks = {
            "exported": service.attrib.get(_android_attr("exported")) == "true",
            "permission": service.attrib.get(_android_attr("permission"))
            == "android.permission.BIND_INPUT_METHOD",
            "label": service.attrib.get(_android_attr("label")) == expected["label"],
            "single_intent_filter": len(service.findall("intent-filter")) == 1,
            "input_method_action": _intent_actions(service) == ["android.view.InputMethod"],
            "no_categories": all(
                intent_filter.findall("category") == []
                for intent_filter in service.findall("intent-filter")
            ),
            "no_data": all(
                intent_filter.findall("data") == []
                for intent_filter in service.findall("intent-filter")
            ),
        }
        metadata = _metadata_by_name(service)
        checks["metadata"] = set(metadata) == {"android.view.im"}
        checks["metadata_resource"] = (
            metadata.get("android.view.im") is not None
            and metadata["android.view.im"].attrib.get(_android_attr("resource")) == expected["config"]
        )
        row["checks"] = checks
        rows.append(row)

        failed = [key for key, ok in checks.items() if not ok]
        if failed:
            problems.append(f"{name} failed manifest checks: {', '.join(failed)}")

    metadata = {
        "expected_services": sorted(expected_names),
        "bind_input_services": sorted(bind_input_services),
        "rows": rows,
        "problems": problems,
    }
    if problems:
        return _blocked(
            "dual IME manifest registration",
            "The Android manifest does not expose exactly the two expected system IMEs.",
            evidence=rel,
            next_step="Fix the IME service declarations before using this APK as v2.0 dual-IME evidence.",
            metadata=metadata,
        )
    return _pass(
        "dual IME manifest registration",
        "AndroidManifest.xml exposes exactly ClipVault Panel and ClipVault Keyboard Lab as system IMEs.",
        evidence=rel,
        metadata=metadata,
    )


def check_input_method_xml(root: Path) -> Gate:
    problems: list[str] = []
    rows: list[dict[str, object]] = []
    for service, expected in EXPECTED_IME_SERVICES.items():
        rel = str(expected["config_path"])
        row: dict[str, object] = {"service": service, "path": rel}
        try:
            xml = _parse_xml(root, rel)
        except (FileNotFoundError, ET.ParseError) as exc:
            problems.append(f"{rel}: {exc}")
            row["present"] = False
            rows.append(row)
            continue
        row["present"] = True
        row["tag"] = xml.tag
        row["supports_switching_to_next_input_method"] = (
            xml.attrib.get(_android_attr("supportsSwitchingToNextInputMethod")) == "true"
        )
        row["subtype_count"] = len(xml.findall("subtype"))
        if xml.tag != "input-method":
            problems.append(f"{rel}: root tag must be input-method")
        if row["supports_switching_to_next_input_method"] is not True:
            problems.append(f"{rel}: supportsSwitchingToNextInputMethod must be true")
        rows.append(row)

    metadata = {"rows": rows, "problems": problems}
    if problems:
        return _blocked(
            "input-method XML switch-back support",
            "One or more IME XML configs cannot support user-visible switch-back behavior.",
            next_step="Fix the XML resource before treating source registration as v2.0 evidence.",
            metadata=metadata,
        )
    return _pass(
        "input-method XML switch-back support",
        "Both IME XML configs declare input-method roots and supportsSwitchingToNextInputMethod=true.",
        evidence=", ".join(str(item["config_path"]) for item in EXPECTED_IME_SERVICES.values()),
        metadata=metadata,
    )


def _missing_needles(source: str, needles: dict[str, str]) -> list[str]:
    return [
        label
        for label, needle in needles.items()
        if needle not in source
    ]


def check_keyboard_lab_source(root: Path) -> Gate:
    rel = str(EXPECTED_IME_SERVICES[".ime.ClipVaultFullKeyboardService"]["source_path"])
    try:
        source = _read_text(root, rel)
    except FileNotFoundError:
        return _blocked(
            "Keyboard Lab source controls",
            "ClipVaultFullKeyboardService.kt is missing.",
            evidence=rel,
            next_step="Restore the v2.0 Keyboard Lab service source before auditing controls.",
        )

    required = {
        "extends InputMethodService": "class ClipVaultFullKeyboardService : InputMethodService()",
        "basic QWERTY rows": 'private val letterRows = listOf("qwertyuiop", "asdfghjkl", "zxcvbnm")',
        "symbol rows": "private val symbolRows = listOf(",
        "one-shot shift state": "private var shifted = false",
        "symbol layer state": "private var symbols = false",
        "ClipVault toolbar button": 'key("📋 ClipVault"',
        "API-compatible switch-back button": "switchToPreviousInputMethodCompat()",
        "space key": 'key("空格"',
        "enter key handler": "private fun enter()",
        "backspace key handler": "private fun backspace()",
        "commitText path": "currentInputConnection?.commitText(s, 1)",
        "candidate runtime read": "runtime.listCandidates(limit = 20)",
        "candidate privacy session": "ImePrivacySession()",
    }
    missing = _missing_needles(source, required)
    metadata = {
        "source": rel,
        "checked_controls": sorted(required),
        "missing_controls": missing,
    }
    if missing:
        return _blocked(
            "Keyboard Lab source controls",
            "The Keyboard Lab source is missing required v2.0 baseline controls.",
            evidence=rel,
            next_step="Restore the basic keyboard, toolbar, candidate, and switch-back source paths.",
            metadata=metadata,
        )
    return _pass(
        "Keyboard Lab source controls",
        "Keyboard Lab source contains QWERTY, symbol, shift, space, enter, backspace, toolbar, candidate, and switch-back paths.",
        evidence=rel,
        metadata=metadata,
    )


def check_panel_ime_source(root: Path) -> Gate:
    rel = str(EXPECTED_IME_SERVICES[".ime.ClipVaultPanelImeService"]["source_path"])
    try:
        source = _read_text(root, rel)
    except FileNotFoundError:
        return _blocked(
            "Panel IME source controls",
            "ClipVaultPanelImeService.kt is missing.",
            evidence=rel,
            next_step="Restore the Panel IME service before auditing v2.0 panel evidence.",
        )

    required = {
        "extends InputMethodService": "class ClipVaultPanelImeService : InputMethodService()",
        "tab helper": "PanelCandidateTabs.filter(",
        "API-compatible switch-back button": "switchToPreviousInputMethodCompat()",
        "explicit save path": "runtime.saveExplicit(",
        "candidate commit path": "currentInputConnection?.commitText(c.text, 1)",
        "clipboard manager read path": "getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager",
        "candidate privacy session": "ImePrivacySession()",
    }
    missing = _missing_needles(source, required)
    metadata = {
        "source": rel,
        "checked_controls": sorted(required),
        "missing_controls": missing,
    }
    if missing:
        return _blocked(
            "Panel IME source controls",
            "The Panel IME source is missing required v2.0 baseline controls.",
            evidence=rel,
            next_step="Restore panel tabs, explicit save, candidate commit, clipboard-save, and switch-back paths.",
            metadata=metadata,
        )
    return _pass(
        "Panel IME source controls",
        "Panel IME source contains tab filtering, explicit save, candidate commit, clipboard-save, privacy, and switch-back paths.",
        evidence=rel,
        metadata=metadata,
    )


def check_ime_privacy_tests(root: Path) -> Gate:
    missing_files = [rel for rel in REQUIRED_ANDROID_TEST_FILES if not (root / rel).is_file()]
    details: dict[str, object] = {"required_files": list(REQUIRED_ANDROID_TEST_FILES)}
    if missing_files:
        details["missing_files"] = missing_files
        return _blocked(
            "IME privacy/static test coverage",
            "Required Android host-JVM IME privacy/source-shape tests are missing.",
            next_step="Restore the tests before treating local automation as v2.0 evidence.",
            metadata=details,
        )

    source_boundary = _read_text(root, REQUIRED_ANDROID_TEST_FILES[0])
    candidate_source = _read_text(root, REQUIRED_ANDROID_TEST_FILES[1])
    required_source_markers = {
        "network import block": "network imports belong outside IME services",
        "logging block": "typed-text-adjacent logging paths",
        "direct persistence block": "IME services must not add direct local persistence calls",
        "full keyboard worker guard": "fullKeyboardRechecksPrivacyBeforeRuntimeCandidateRead",
        "panel worker guard": "panelImeRechecksPrivacyBeforeRuntimeCandidateRead",
    }
    combined = source_boundary + "\n" + candidate_source
    missing_markers = _missing_needles(combined, required_source_markers)
    details["checked_markers"] = sorted(required_source_markers)
    details["missing_markers"] = missing_markers
    if missing_markers:
        return _blocked(
            "IME privacy/static test coverage",
            "IME privacy test files exist but no longer guard every required source boundary.",
            next_step="Restore network/logging/persistence and worker pre-read privacy guards.",
            metadata=details,
        )
    return _pass(
        "IME privacy/static test coverage",
        "Android host-JVM tests exist for IME source boundaries, candidate-read privacy, session invalidation, panel save privacy, filter behavior, and panel tabs.",
        evidence=", ".join(REQUIRED_ANDROID_TEST_FILES),
        metadata=details,
    )


def check_docs_release_boundaries(root: Path) -> Gate:
    required_docs = {
        "docs/STABILITY_PLAN_V2_0.md": (
            "v2.0 means the same APK exposes two IME entrypoints",
            "Issue #36 / v1.6.0 is closed",
            "dedicated v2.0 release-gate issue",
            "Do not wire librime/fcitx5 into the production IME.",
            "Do not add network work inside any IME service.",
        ),
        "docs/STABILITY_PLAN_V1_6_V1_7.md": (
            "Do not call v1.7 stable until",
            "Field-test package evidence",
        ),
        "AGENTS.md": (
            "Issue #36 is the current v1.6.0 release",
            "Do not claim v2.0 stable until docs/STABILITY_PLAN_V2_0.md",
        ),
        "docs/HANDOFF.md": (
            "v2.0 dual-IME stability planning",
            "v2.0 stays planning/stability-only",
        ),
    }
    problems: list[str] = []
    for rel, markers in required_docs.items():
        try:
            text = _read_text(root, rel)
        except FileNotFoundError:
            problems.append(f"{rel} is missing")
            continue
        for marker in markers:
            if marker not in text:
                problems.append(f"{rel} is missing marker: {marker}")

    metadata = {
        "checked_docs": sorted(required_docs),
        "problems": problems,
    }
    if problems:
        return _blocked(
            "v2.0 docs/release boundary",
            "Release-boundary docs do not fully preserve the v1.6/v1.7/v2.0 gate order.",
            next_step="Fix planning docs before continuing v2.0 implementation work.",
            metadata=metadata,
        )
    return _pass(
        "v2.0 docs/release boundary",
        "Docs keep v2.0 scoped to dual IME stability and preserve #36, v1.7, Owner/manual, and non-goal boundaries.",
        evidence=", ".join(sorted(required_docs)),
        metadata=metadata,
    )


def check_owner_release_gate() -> Gate:
    return _blocked(
        "Owner/manual release gate",
        "v2.0 stable still requires Issue #36 closure, v1.7 exit evidence or explicit deferral, a dedicated v2.0 release-gate issue, Owner/manual device evidence, and signed/final release approval.",
        next_step=(
            "Keep using this helper as local automation evidence only; do not claim v2.0 stable "
            "until the external Owner/release-gate rows are recorded."
        ),
        metadata={
            "requires_issue_36_closed": True,
            "requires_v1_7_exit_or_deferral": True,
            "requires_v2_0_release_gate_issue": True,
            "requires_owner_device_evidence": True,
            "requires_signed_final_artifacts": True,
        },
    )


def build_report(root: Path = ROOT) -> dict[str, object]:
    gates = [
        check_dual_ime_manifest(root),
        check_input_method_xml(root),
        check_keyboard_lab_source(root),
        check_panel_ime_source(root),
        check_ime_privacy_tests(root),
        check_docs_release_boundaries(root),
        check_owner_release_gate(),
    ]
    blocked = sum(1 for gate in gates if gate.status == "blocked")
    warnings = sum(1 for gate in gates if gate.status == "warn")
    status = "ready" if blocked == 0 and warnings == 0 else "blocked"
    return {
        "status": status,
        "blocked": blocked,
        "warnings": warnings,
        "gates": [gate.as_dict() for gate in gates],
        "scope_note": (
            "Read-only local report. It does not call GitHub, trigger workflows, "
            "download artifacts, run device QA, sign or publish releases, close issues, "
            "change version metadata, or claim v2.0 stable."
        ),
    }


def _render_text(report: dict[str, object]) -> str:
    lines = [
        "ClipVault v2.0 keyboard readiness",
        f"status: {report['status']} (blocked={report['blocked']}, warnings={report['warnings']})",
        "",
        "Gates:",
    ]
    for gate in report["gates"]:
        assert isinstance(gate, dict)
        prefix = {"pass": "[x]", "blocked": "[ ]", "warn": "[!]"}[str(gate["status"])]
        lines.append(f"- {prefix} {gate['name']}: {gate['detail']}")
        if gate.get("evidence"):
            lines.append(f"  evidence: {gate['evidence']}")
        if gate.get("next_step"):
            lines.append(f"  next: {gate['next_step']}")
        metadata = gate.get("metadata")
        if isinstance(metadata, dict):
            problems = metadata.get("problems")
            if isinstance(problems, list) and problems:
                lines.append("  problems:")
                for problem in problems:
                    lines.append(f"    - {problem}")
            missing = metadata.get("missing_controls") or metadata.get("missing_files") or metadata.get("missing_markers")
            if isinstance(missing, list) and missing:
                lines.append("  missing:")
                for item in missing:
                    lines.append(f"    - {item}")
    lines.extend(["", str(report["scope_note"])])
    return "\n".join(lines) + "\n"


def _statuses(gates: Iterable[dict[str, object]]) -> set[str]:
    return {str(gate["status"]) for gate in gates}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only local v2.0 dual-IME readiness report.")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="return exit code 0 even when v2.0 gates are blocked",
    )
    args = parser.parse_args(argv)

    report = build_report(root=args.root)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(_render_text(report), end="")

    statuses = _statuses(report["gates"])
    if args.no_fail:
        return 0
    return 0 if statuses == {"pass"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
