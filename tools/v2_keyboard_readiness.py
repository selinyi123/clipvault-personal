#!/usr/bin/env python3
"""Read-only Android keyboard evidence for the v2 daily-use architecture.

The historical implementation of this helper audited two IME services in the
networked Runtime APK.  The accepted production architecture is now one
standalone, networkless ``com.clipvault.ime`` package plus a networked Runtime
which contains no legacy IME declaration, class or Rime runtime.  This helper checks only that
Android-local boundary.  The cross-platform candidate and its immutable Owner
evidence remain owned by ``tools/v2_daily_readiness.py``.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
ANDROID_NS = "http://schemas.android.com/apk/res/android"
RUNTIME_MANIFEST = "android/app/src/main/AndroidManifest.xml"
IME_MANIFEST = "android/ime-app/src/main/AndroidManifest.xml"
IME_GRADLE = "android/ime-app/build.gradle.kts"
IME_CONFIG = "android/ime-app/src/main/res/xml/ime_isolated_config.xml"
IME_SERVICE = ".ClipVaultIsolatedImeService"
IME_SOURCE = (
    "android/ime-app/src/main/kotlin/com/clipvault/imeapp/"
    "ClipVaultIsolatedImeService.kt"
)
RUNTIME_LEGACY_IME_SOURCE = "android/app/src/main/kotlin/com/clipvault/app/ime"
RUNTIME_LEGACY_IME_RESOURCES = (
    "android/app/src/main/res/xml/ime_panel_config.xml",
    "android/app/src/main/res/xml/ime_full_config.xml",
)
RUNTIME_GRADLE = "android/app/build.gradle.kts"

REQUIRED_IME_TEST_FILES = (
    "android/ime-app/src/test/kotlin/com/clipvault/imeapp/ManifestPermissionTest.kt",
    "android/ime-app/src/test/kotlin/com/clipvault/imeapp/LanguageToggleSourceTest.kt",
    "android/ime-app/src/test/kotlin/com/clipvault/imeapp/PhysicalKeyboardSourceTest.kt",
    "android/ime-app/src/test/kotlin/com/clipvault/imeapp/RuntimeCandidateCommitOrderSourceTest.kt",
    "android/ime-app/src/test/kotlin/com/clipvault/imeapp/RuntimeSnapshotClientSourceTest.kt",
    "android/ime-app/src/test/kotlin/com/clipvault/imeapp/SensitiveAppPolicyTest.kt",
    "android/ime-app/src/test/kotlin/com/clipvault/imeapp/BackspaceRepeatControllerTest.kt",
    "android/ime-app/src/test/kotlin/com/clipvault/imeapp/TouchBackspaceSourceTest.kt",
    "android/ime-app/src/test/kotlin/com/clipvault/imeapp/InputMethodSwitchSourceTest.kt",
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
    return Gate(name, "pass", detail, evidence=evidence, metadata=metadata)


def _blocked(
    name: str,
    detail: str,
    *,
    evidence: str = "",
    next_step: str = "",
    metadata: dict[str, object] | None = None,
) -> Gate:
    return Gate(
        name,
        "blocked",
        detail,
        evidence=evidence,
        next_step=next_step,
        metadata=metadata,
    )


def _read_text(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _parse_xml(root: Path, relative: str) -> ET.Element:
    return ET.parse(root / relative).getroot()


def _manifest_services(manifest: ET.Element) -> list[ET.Element]:
    application = manifest.find("application")
    return [] if application is None else application.findall("service")


def _is_input_method(service: ET.Element) -> bool:
    return (
        service.attrib.get(_android_attr("permission"))
        == "android.permission.BIND_INPUT_METHOD"
    )


def _is_enabled(service: ET.Element) -> bool:
    return service.attrib.get(_android_attr("enabled"), "true") != "false"


def _service_name(service: ET.Element) -> str:
    return service.attrib.get(_android_attr("name"), "<unnamed>")


def _intent_actions(service: ET.Element) -> list[str]:
    return [
        action.attrib.get(_android_attr("name"), "")
        for intent_filter in service.findall("intent-filter")
        for action in intent_filter.findall("action")
    ]


def check_package_ime_boundary(root: Path) -> Gate:
    name = "standalone IME package boundary"
    try:
        runtime_manifest = _parse_xml(root, RUNTIME_MANIFEST)
        ime_manifest = _parse_xml(root, IME_MANIFEST)
        ime_gradle = _read_text(root, IME_GRADLE)
    except (FileNotFoundError, UnicodeDecodeError, ET.ParseError) as exc:
        return _blocked(
            name,
            "The Runtime/IME package boundary could not be read as UTF-8 XML/source.",
            evidence=str(exc),
            next_step="Restore parseable Runtime and standalone IME package declarations.",
        )

    problems: list[str] = []
    runtime_ime_services = [
        service for service in _manifest_services(runtime_manifest) if _is_input_method(service)
    ]
    enabled_runtime_imes = [
        _service_name(service) for service in runtime_ime_services if _is_enabled(service)
    ]
    if runtime_ime_services:
        problems.append(
            "networked Runtime still declares IME services: "
            + ", ".join(_service_name(service) for service in runtime_ime_services)
        )

    legacy_source = root / RUNTIME_LEGACY_IME_SOURCE
    packaged_legacy_sources = (
        sorted(path.name for path in legacy_source.glob("*.kt"))
        if legacy_source.is_dir()
        else []
    )
    if packaged_legacy_sources:
        problems.append(
            "networked Runtime still compiles legacy IME sources: "
            + ", ".join(packaged_legacy_sources)
        )
    packaged_legacy_resources = [
        relative for relative in RUNTIME_LEGACY_IME_RESOURCES if (root / relative).exists()
    ]
    if packaged_legacy_resources:
        problems.append(
            "networked Runtime still packages legacy IME resources: "
            + ", ".join(packaged_legacy_resources)
        )
    try:
        runtime_gradle = _read_text(root, RUNTIME_GRADLE)
    except (FileNotFoundError, UnicodeDecodeError) as exc:
        runtime_gradle = ""
        problems.append(f"Runtime Gradle boundary cannot be read: {exc}")
    if 'implementation(project(\":rime-engine-android\"))' in runtime_gradle:
        problems.append("networked Runtime still packages the native Rime engine")

    if 'applicationId = "com.clipvault.ime"' not in ime_gradle:
        problems.append("standalone IME applicationId is not com.clipvault.ime")

    forbidden_permissions = {
        "android.permission.INTERNET",
        "android.permission.ACCESS_NETWORK_STATE",
        "android.permission.READ_SMS",
        "android.permission.RECEIVE_SMS",
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE",
    }
    ime_permissions = {
        item.attrib.get(_android_attr("name"), "")
        for item in ime_manifest.findall("uses-permission")
    }
    leaked_permissions = sorted(forbidden_permissions & ime_permissions)
    if leaked_permissions:
        problems.append("standalone IME has forbidden permissions: " + ", ".join(leaked_permissions))

    active_ime_services = [
        service
        for service in _manifest_services(ime_manifest)
        if _is_input_method(service) and _is_enabled(service)
    ]
    if len(active_ime_services) != 1:
        problems.append(
            f"standalone package has {len(active_ime_services)} active IME services; expected 1"
        )
        ime_checks: dict[str, bool] = {}
    else:
        service = active_ime_services[0]
        filters = service.findall("intent-filter")
        metadata = service.findall("meta-data")
        ime_checks = {
            "service_name": _service_name(service) == IME_SERVICE,
            "exported_for_system": service.attrib.get(_android_attr("exported")) == "true",
            "bind_permission": _is_input_method(service),
            "single_intent_filter": len(filters) == 1,
            "input_method_action": _intent_actions(service) == ["android.view.InputMethod"],
            "no_categories": all(not item.findall("category") for item in filters),
            "no_data": all(not item.findall("data") for item in filters),
            "single_input_metadata": (
                len(metadata) == 1
                and metadata[0].attrib.get(_android_attr("name")) == "android.view.im"
                and metadata[0].attrib.get(_android_attr("resource"))
                == "@xml/ime_isolated_config"
            ),
        }
        failed = [label for label, passed in ime_checks.items() if not passed]
        if failed:
            problems.append("standalone IME service failed checks: " + ", ".join(failed))

    details = {
        "runtime_ime_services": [_service_name(item) for item in runtime_ime_services],
        "enabled_runtime_ime_services": enabled_runtime_imes,
        "runtime_legacy_ime_sources": packaged_legacy_sources,
        "runtime_legacy_ime_resources": packaged_legacy_resources,
        "runtime_packages_rime": 'implementation(project(\":rime-engine-android\"))' in runtime_gradle,
        "standalone_active_ime_services": [_service_name(item) for item in active_ime_services],
        "standalone_permissions": sorted(ime_permissions),
        "standalone_service_checks": ime_checks,
        "problems": problems,
    }
    if problems:
        return _blocked(
            name,
            "The Android packages violate the one-entrypoint, no-network IME boundary.",
            evidence=f"{RUNTIME_MANIFEST}, {IME_MANIFEST}, {IME_GRADLE}",
            next_step="Remove Runtime IME declarations/classes/resources and restore one isolated com.clipvault.ime service.",
            metadata=details,
        )
    return _pass(
        name,
        "The networked Runtime contains no IME declaration/class/Rime payload; com.clipvault.ime exposes exactly one networkless system input method.",
        evidence=f"{RUNTIME_MANIFEST}, {IME_MANIFEST}, {IME_GRADLE}",
        metadata=details,
    )


def check_input_method_xml(root: Path) -> Gate:
    try:
        config = _parse_xml(root, IME_CONFIG)
    except (FileNotFoundError, ET.ParseError) as exc:
        return _blocked(
            "standalone input-method XML",
            "The standalone IME configuration is missing or invalid.",
            evidence=str(exc),
            next_step="Restore ime_isolated_config.xml before using the package as an IME.",
        )
    checks = {
        "input_method_root": config.tag == "input-method",
        "switch_back": config.attrib.get(
            _android_attr("supportsSwitchingToNextInputMethod")
        )
        == "true",
        "inline_autofill": config.attrib.get(_android_attr("supportsInlineSuggestions"))
        == "true",
    }
    failed = [label for label, passed in checks.items() if not passed]
    if failed:
        return _blocked(
            "standalone input-method XML",
            "The standalone IME XML is missing required switching/Inline Autofill capability.",
            evidence=IME_CONFIG,
            next_step="Restore the input-method root and both capability declarations.",
            metadata={"checks": checks, "problems": failed},
        )
    return _pass(
        "standalone input-method XML",
        "The single IME config supports switch-back and protected Inline Autofill surfaces.",
        evidence=IME_CONFIG,
        metadata={"checks": checks, "problems": []},
    )


def _missing_needles(source: str, required: dict[str, str]) -> list[str]:
    return [label for label, needle in required.items() if needle not in source]


def check_isolated_ime_source(root: Path) -> Gate:
    try:
        source = _read_text(root, IME_SOURCE)
    except (FileNotFoundError, UnicodeDecodeError) as exc:
        return _blocked(
            "standalone IME source controls",
            "The isolated IME source could not be read as UTF-8.",
            evidence=str(exc),
            next_step="Restore ClipVaultIsolatedImeService.kt.",
        )
    required = {
        "InputMethodService shell": "class ClipVaultIsolatedImeService : InputMethodService()",
        "Engine Protocol V2": "InputEngineAdapterV2",
        "native Rime path": "RimeEngineFactory.create(this, context)",
        "direct failure fallback": "DirectInputEngineAdapter()",
        "protected Inline Autofill host": "onInlineSuggestionsResponse",
        "signature snapshot client": "RuntimeSnapshotClient(this)",
        "sensitive application policy": "sensitiveAppPolicy.isSensitive",
        "QWERTY layout": 'addTextRow(host, "qwertyuiop")',
        "language toggle": "private fun toggleLanguage()",
        "physical keyboard path": "override fun onKeyDown",
        "backspace": "private fun backspace(): Boolean",
        "editor action": "private fun enter(): Boolean",
        "composition commit": "connection.commitText(transition.commitText, 1)",
    }
    missing = _missing_needles(source, required)
    if missing:
        return _blocked(
            "standalone IME source controls",
            "The production IME shell is missing required daily-use controls.",
            evidence=IME_SOURCE,
            next_step="Restore the native/fallback, privacy, keyboard and candidate paths.",
            metadata={"checked_controls": sorted(required), "missing_controls": missing},
        )
    return _pass(
        "standalone IME source controls",
        "The one production IME contains native Rime, safe fallback, keyboard, physical-key, Inline Autofill and snapshot paths.",
        evidence=IME_SOURCE,
        metadata={"checked_controls": sorted(required), "missing_controls": []},
    )


def check_ime_static_tests(root: Path) -> Gate:
    missing = [relative for relative in REQUIRED_IME_TEST_FILES if not (root / relative).is_file()]
    if missing:
        return _blocked(
            "standalone IME static test coverage",
            "Required standalone IME boundary tests are missing.",
            next_step="Restore the IME package, snapshot, privacy and physical-key tests.",
            metadata={"required_files": list(REQUIRED_IME_TEST_FILES), "missing_files": missing},
        )
    combined = "\n".join(_read_text(root, relative) for relative in REQUIRED_IME_TEST_FILES)
    required_markers = {
        "no network permission assertion": "android.permission.INTERNET",
        "no SMS permission assertion": "android.permission.RECEIVE_SMS",
        "snapshot identity assertion": "acceptsSnapshotIdentity",
        "candidate generation binding": "boundSnapshotGeneration",
        "physical key assertion": "KEYCODE_DEL -> backspace()",
        "press-hold-release delete assertion": "ACTION_CANCEL",
        "explicit system IME switch assertion": "switchToNextInputMethod(false)",
        "sensitive package assertion": "configured package is sensitive",
    }
    missing_markers = _missing_needles(combined, required_markers)
    if missing_markers:
        return _blocked(
            "standalone IME static test coverage",
            "Standalone IME tests exist but no longer guard every production boundary.",
            next_step="Restore permission, snapshot, privacy, candidate and physical-key assertions.",
            metadata={
                "required_files": list(REQUIRED_IME_TEST_FILES),
                "missing_markers": missing_markers,
            },
        )
    return _pass(
        "standalone IME static test coverage",
        "Host tests guard permissions, snapshots, stale candidates, sensitive apps, language and physical keys.",
        evidence=", ".join(REQUIRED_IME_TEST_FILES),
        metadata={"required_files": list(REQUIRED_IME_TEST_FILES), "missing_markers": []},
    )


def check_architecture_docs(root: Path) -> Gate:
    required_docs = {
        "docs/ADR/0010-keyboard-base-selection.md": (
            "A 路线已终裁",
            "独立无网络 IME APK",
            "ADR-0013 supersedes the former single-package IME boundary",
        ),
        "docs/ADR/0013-cross-platform-input-process-boundary.md": (
            "Android targets two packages",
            "signature-protected Binder contract",
        ),
        "docs/ADR/0014-engine-protocol-v2-and-candidate-surfaces.md": (
            "stable opaque ID",
            "separate surfaces",
        ),
        "docs/ADR/0016-otp-relay.md": (
            "ephemeral credential channel",
            "Companion Runtime, never the IME package",
        ),
        "docs/V2_DAILY_USE_ACCEPTANCE.md": (
            "V2D-A03",
            "V2D-A04",
            "V2D-A11",
            "签名、安装、升级、卸载和真实设备/应用矩阵仍是 Owner 门禁",
        ),
        "docs/V2_DAILY_USE_MANUAL_QA.md": (
            "系统只显示一个 ClipVault 主输入法入口",
            "tools/v2_daily_readiness.py",
        ),
    }
    problems: list[str] = []
    for relative, markers in required_docs.items():
        try:
            text = _read_text(root, relative)
        except (FileNotFoundError, UnicodeDecodeError) as exc:
            problems.append(f"{relative}: {exc}")
            continue
        if "\ufffd" in text:
            problems.append(f"{relative}: contains Unicode replacement characters")
        for marker in markers:
            if marker not in text:
                problems.append(f"{relative}: missing marker {marker}")
    if problems:
        return _blocked(
            "v2 daily-use Android architecture docs",
            "The accepted package/candidate/OTP decisions or Owner boundary have drifted.",
            next_step="Align the UTF-8 ADRs and daily-use acceptance docs before claiming readiness.",
            metadata={"checked_docs": sorted(required_docs), "problems": problems},
        )
    return _pass(
        "v2 daily-use Android architecture docs",
        "UTF-8 ADRs and acceptance docs describe one isolated IME, separate candidates and Runtime-owned OTP capture.",
        evidence=", ".join(sorted(required_docs)),
        metadata={"checked_docs": sorted(required_docs), "problems": []},
    )


def check_owner_release_gate() -> Gate:
    return _blocked(
        "Owner signed/manual release gate",
        "One immutable candidate still needs Owner-signed Runtime/IME artifacts and real-device daily-use evidence.",
        next_step=(
            "Use tools/v2_daily_readiness.py with the canonical Owner evidence file. "
            "This Android helper must not duplicate, synthesize or approve that evidence."
        ),
        metadata={
            "delegated_to": "tools/v2_daily_readiness.py",
            "requires_owner_signed_runtime_and_ime": True,
            "requires_real_device_manual_evidence": True,
            "synthetic_evidence_allowed": False,
        },
    )


def build_report(root: Path = ROOT) -> dict[str, object]:
    gates = [
        check_package_ime_boundary(root),
        check_input_method_xml(root),
        check_isolated_ime_source(root),
        check_ime_static_tests(root),
        check_architecture_docs(root),
        check_owner_release_gate(),
    ]
    blocked = sum(gate.status == "blocked" for gate in gates)
    return {
        "status": "ready" if blocked == 0 else "blocked",
        "blocked": blocked,
        "warnings": 0,
        "gates": [gate.as_dict() for gate in gates],
        "scope_note": (
            "Read-only Android-local report. It does not call GitHub, run device QA, "
            "sign or publish artifacts, or claim v2 daily-use release readiness. "
            "Cross-platform and Owner evidence belongs to tools/v2_daily_readiness.py."
        ),
    }


def _render_text(report: dict[str, object]) -> str:
    lines = [
        "ClipVault v2 daily-use Android keyboard readiness",
        f"status: {report['status']} (blocked={report['blocked']}, warnings={report['warnings']})",
        "",
        "Gates:",
    ]
    for gate in report["gates"]:
        assert isinstance(gate, dict)
        prefix = "[x]" if gate["status"] == "pass" else "[ ]"
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
                lines.extend(f"    - {item}" for item in problems)
            missing = metadata.get("missing_controls") or metadata.get("missing_files") or metadata.get("missing_markers")
            if isinstance(missing, list) and missing:
                lines.append("  missing:")
                lines.extend(f"    - {item}" for item in missing)
    lines.extend(["", str(report["scope_note"])])
    return "\n".join(lines) + "\n"


def _statuses(gates: Iterable[dict[str, object]]) -> set[str]:
    return {str(gate["status"]) for gate in gates}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only v2 daily-use Android keyboard readiness report."
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="return exit code 0 even while Owner/manual evidence is blocked",
    )
    args = parser.parse_args(argv)
    report = build_report(args.root)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(_render_text(report), end="")
    if args.no_fail:
        return 0
    return 0 if _statuses(report["gates"]) == {"pass"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
