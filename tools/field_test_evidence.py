#!/usr/bin/env python3
"""Validate and render v1.7 field-test evidence for Issue #82.

This helper is intentionally local-only. It reads an Owner-filled JSON evidence
file, checks that every required v1.7 field-test row has a status and evidence,
and renders a Markdown comment draft. It does not download artifacts, install
apps, run device QA, post to GitHub, sign or publish releases, close Issue #82,
close Issue #36, or claim v1.7 stable.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "selinyi123/clipvault-personal"
DEFAULT_ISSUE = 82
DEFAULT_FIELD_TEST_LABEL = "v1.7-field-test"
DEFAULT_SOURCE_VERSION = "1.6.0"
EXPECTED_WINDOWS_ARTIFACT_NAME = "clipvault-windows-release-candidate"
EXPECTED_ANDROID_ARTIFACT_NAME = "clipvault-android-release-candidate"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_URL_RE = re.compile(
    r"^https://github\.com/(?P<repo>[^/\s]+/[^/\s]+)/actions/runs/(?P<run_id>[0-9]+)$"
)
VALID_STATUSES = {"pass", "fail", "blocked"}


def _load_verify_release_manifest():
    script = ROOT / "scripts" / "verify_release_manifest.py"
    spec = importlib.util.spec_from_file_location("verify_release_manifest", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify_release_manifest = _load_verify_release_manifest()


@dataclass(frozen=True)
class EvidenceItem:
    key: str
    label: str


@dataclass(frozen=True)
class EvidenceSection:
    key: str
    title: str
    items: tuple[EvidenceItem, ...]


REQUIRED_SECTIONS: tuple[EvidenceSection, ...] = (
    EvidenceSection(
        key="artifact_verification",
        title="Downloaded candidate artifact verification",
        items=(
            EvidenceItem(
                "windows_manifest_verified",
                "Windows candidate directory passed verify_release_manifest.py --expect-dry-run.",
            ),
            EvidenceItem(
                "android_manifest_verified",
                "Android candidate directory passed verify_release_manifest.py --expect-dry-run.",
            ),
            EvidenceItem(
                "candidate_boundary_acknowledged",
                "Tester acknowledged release-candidate artifacts are not signed/final release evidence.",
            ),
        ),
    ),
    EvidenceSection(
        key="windows_smoke",
        title="Windows field-test smoke",
        items=(
            EvidenceItem("portable_launch", "Launch the candidate portable executable."),
            EvidenceItem("installer_install", "Install with the candidate Windows installer."),
            EvidenceItem("clipboard_capture", "Capture a normal clipboard text item."),
            EvidenceItem("sync_smoke", "Confirm LAN/Tailscale sync smoke with Android or another node."),
            EvidenceItem("uninstall_cleanup", "Uninstall or clean up the candidate install path."),
        ),
    ),
    EvidenceSection(
        key="android_smoke",
        title="Android field-test smoke",
        items=(
            EvidenceItem("debug_apk_install", "Install the candidate debug APK on the test device."),
            EvidenceItem("ime_enable", "Enable the ClipVault IME entrypoint from Android settings."),
            EvidenceItem("pairing", "Pair Android with the desktop node using a one-time pairing code."),
            EvidenceItem("share_capture", "Share text from another app into ClipVault."),
            EvidenceItem("qs_tile_capture", "Use the Quick Settings tile to explicitly save clipboard content."),
            EvidenceItem("panel_ime_paste", "Paste from the ClipVault Panel IME."),
            EvidenceItem("sensitive_field_suppression", "Confirm password/incognito fields suppress candidates and typed text is not logged."),
        ),
    ),
)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    field_test_ready: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    item_counts: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "field_test_ready": self.field_test_ready,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "item_counts": dict(self.item_counts),
            "scope_note": scope_note(),
        }


def scope_note() -> str:
    return (
        "This v1.7 field-test report validates Owner-recorded candidate testing "
        "evidence only. It does not replace Issue #36 release evidence, signed "
        "artifact evidence, manual QA evidence, release environment/secrets "
        "evidence, Owner approval, or final GitHub Release publication. "
        "Release-candidate artifacts are not signed/final release evidence, and "
        "the Android unsigned release APK is not a signed install package."
    )


def build_template(
    *,
    field_test_label: str = DEFAULT_FIELD_TEST_LABEL,
    source_version: str = DEFAULT_SOURCE_VERSION,
    repo: str = DEFAULT_REPO,
) -> dict[str, object]:
    return {
        "field_test_label": field_test_label,
        "source_version": source_version,
        "repo": repo,
        "target_commit": "REPLACE_WITH_40_HEX_MAIN_COMMIT",
        "ci_run_url": "REPLACE_WITH_CI_RUN_URL",
        "candidate_run_url": "REPLACE_WITH_RELEASE_CANDIDATE_RUN_URL",
        "tester": "REPLACE_WITH_TESTER_NAME",
        "tested_at": "REPLACE_WITH_ISO_8601_TIMESTAMP",
        "windows_environment": {
            "os": "REPLACE_WITH_WINDOWS_VERSION",
            "artifact_name": "clipvault-windows-release-candidate",
            "portable_or_installer": "REPLACE_WITH_EXE_OR_INSTALLER_NAME",
        },
        "android_device": {
            "model": "REPLACE_WITH_DEVICE_MODEL",
            "android_version": "REPLACE_WITH_ANDROID_VERSION",
            "artifact_name": "clipvault-android-release-candidate",
            "install_apk": "ClipVault-Android-v1.6.0-debug.apk",
        },
        "sections": {
            section.key: {
                "title": section.title,
                "items": {
                    item.key: {
                        "status": "blocked",
                        "evidence": "",
                        "next_step": "REPLACE_WITH_OWNER_ACTION_OR_SET_STATUS_TO_PASS_AFTER_RUNNING",
                        "notes": item.label,
                    }
                    for item in section.items
                },
            }
            for section in REQUIRED_SECTIONS
        },
        "scope_note": scope_note(),
    }


def _is_mapping(value: object) -> bool:
    return isinstance(value, dict)


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_placeholder(value: str) -> bool:
    return value.startswith("REPLACE_WITH_")


def _require_non_empty_string(data: object, path: str, errors: list[str]) -> str:
    value = _string(data)
    if not value:
        errors.append(f"{path} must be a non-empty string")
    elif _is_placeholder(value):
        errors.append(f"{path} must replace the template placeholder")
    return value


def _validate_run_url(value: str, path: str, repo: str, errors: list[str]) -> None:
    if not value or _is_placeholder(value):
        return
    match = RUN_URL_RE.fullmatch(value)
    if not match:
        errors.append(f"{path} must be a GitHub Actions run URL")
    elif match.group("repo") != repo:
        errors.append(f"{path} repo mismatch: expected {repo!r}")


def _artifact_names(manifest: dict[str, Any]) -> list[str]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return []
    names: list[str] = []
    for row in artifacts:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            names.append(row["name"])
    return sorted(names)


def verify_candidate_artifacts(
    *,
    windows_dir: Path,
    android_dir: Path,
    source_version: str,
    commit: str,
) -> dict[str, list[str]]:
    commit = commit.strip()
    if not COMMIT_RE.fullmatch(commit):
        raise ValueError("target commit must be a full 40-character lowercase hexadecimal commit SHA")

    windows_manifest = verify_release_manifest.verify_manifest(
        windows_dir,
        platform="windows",
        version=source_version,
        commit=commit,
        expect_dry_run=True,
    )
    android_manifest = verify_release_manifest.verify_manifest(
        android_dir,
        platform="android",
        version=source_version,
        commit=commit,
        expect_dry_run=True,
    )
    return {
        "windows_artifacts": _artifact_names(windows_manifest),
        "android_artifacts": _artifact_names(android_manifest),
    }


def _set_pass(items: dict[str, object], key: str, evidence: str) -> None:
    expected = {
        item.key: item
        for section in REQUIRED_SECTIONS
        for item in section.items
    }[key]
    items[key] = {
        "status": "pass",
        "evidence": evidence,
        "next_step": "",
        "notes": expected.label,
    }


def apply_windows_smoke_report(data: dict[str, object], report: object) -> None:
    """Fold a windows_candidate_smoke.py JSON report into portable_launch only."""
    if not isinstance(report, dict):
        raise ValueError("windows smoke report must be a JSON object")
    status = _string(report.get("status")).lower()
    if status not in VALID_STATUSES:
        if report.get("ok") is True:
            status = "pass"
        else:
            raise ValueError("windows smoke report status must be one of: blocked, fail, pass")
    evidence = _string(report.get("evidence"))
    next_step = _string(report.get("next_step"))
    if status == "pass" and not evidence:
        raise ValueError("windows smoke report evidence is required when status is pass")
    if status in {"blocked", "fail"} and not next_step:
        raise ValueError(
            f"windows smoke report next_step is required when status is {status}"
        )

    windows = data.get("windows_environment")
    if isinstance(windows, dict):
        environment = _string(report.get("windows_environment"))
        if environment and "pending Owner Windows smoke" in _string(windows.get("os")):
            windows["os"] = environment
        portable_name = f"ClipVault-Desktop-v{_string(data.get('source_version'))}-portable.exe"
        package = _string(windows.get("portable_or_installer"))
        if package and "install smoke pending" in package:
            windows["portable_or_installer"] = (
                f"{portable_name} portable smoke report applied; installer smoke pending"
            )

    sections = data.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("field-test data sections must be an object")
    windows_section = sections.get("windows_smoke")
    if not isinstance(windows_section, dict):
        raise ValueError("field-test data sections.windows_smoke must be an object")
    items = windows_section.get("items")
    if not isinstance(items, dict):
        raise ValueError("field-test data sections.windows_smoke.items must be an object")
    expected = {
        item.key: item
        for section in REQUIRED_SECTIONS
        for item in section.items
    }["portable_launch"]
    items["portable_launch"] = {
        "status": status,
        "evidence": evidence or "-",
        "next_step": next_step,
        "notes": expected.label
        + " This automated smoke does not cover installer, clipboard, sync, or Android rows.",
    }


def build_artifact_verified_template(
    *,
    windows_dir: Path,
    android_dir: Path,
    target_commit: str,
    ci_run_url: str,
    candidate_run_url: str,
    tester: str,
    tested_at: str,
    field_test_label: str = DEFAULT_FIELD_TEST_LABEL,
    source_version: str = DEFAULT_SOURCE_VERSION,
    repo: str = DEFAULT_REPO,
) -> dict[str, object]:
    artifacts = verify_candidate_artifacts(
        windows_dir=windows_dir,
        android_dir=android_dir,
        source_version=source_version,
        commit=target_commit,
    )
    data = build_template(
        field_test_label=field_test_label,
        source_version=source_version,
        repo=repo,
    )
    data.update({
        "target_commit": target_commit,
        "ci_run_url": ci_run_url,
        "candidate_run_url": candidate_run_url,
        "tester": tester,
        "tested_at": tested_at,
        "windows_environment": {
            "os": "pending Owner Windows smoke",
            "artifact_name": EXPECTED_WINDOWS_ARTIFACT_NAME,
            "portable_or_installer": "candidate portable/installer verified; install smoke pending",
        },
        "android_device": {
            "model": "pending Owner Android smoke",
            "android_version": "pending Owner Android smoke",
            "artifact_name": EXPECTED_ANDROID_ARTIFACT_NAME,
            "install_apk": f"ClipVault-Android-v{source_version}-debug.apk",
        },
    })
    sections = data["sections"]
    assert isinstance(sections, dict)
    artifact_section = sections["artifact_verification"]
    assert isinstance(artifact_section, dict)
    items = artifact_section["items"]
    assert isinstance(items, dict)
    _set_pass(
        items,
        "windows_manifest_verified",
        "`verify_release_manifest.py --expect-dry-run` passed for "
        f"`{windows_dir}`; artifacts: "
        + ", ".join(f"`{name}`" for name in artifacts["windows_artifacts"])
        + ".",
    )
    _set_pass(
        items,
        "android_manifest_verified",
        "`verify_release_manifest.py --expect-dry-run` passed for "
        f"`{android_dir}`; artifacts: "
        + ", ".join(f"`{name}`" for name in artifacts["android_artifacts"])
        + ".",
    )
    _set_pass(
        items,
        "candidate_boundary_acknowledged",
        "This is candidate-only artifact verification; it does not prove signed/final "
        "release status, real-device smoke, or v1.7 stable readiness.",
    )
    for section in REQUIRED_SECTIONS:
        if section.key == "artifact_verification":
            continue
        section_data = sections[section.key]
        assert isinstance(section_data, dict)
        section_items = section_data["items"]
        assert isinstance(section_items, dict)
        for item in section.items:
            item_data = section_items[item.key]
            assert isinstance(item_data, dict)
            item_data["next_step"] = (
                "Owner must run and record this smoke check on the target device/environment."
            )
    return data


def _validate_metadata(
    data: object,
    *,
    expected_field_test_label: str,
    expected_source_version: str,
    expected_repo: str,
    errors: list[str],
) -> None:
    if not _is_mapping(data):
        errors.append("root must be a JSON object")
        return

    label = _require_non_empty_string(data.get("field_test_label"), "field_test_label", errors)
    if label and label != expected_field_test_label:
        errors.append(f"field_test_label must be {expected_field_test_label}, got {label}")

    source_version = _require_non_empty_string(data.get("source_version"), "source_version", errors)
    if source_version and source_version != expected_source_version:
        errors.append(f"source_version must be {expected_source_version}, got {source_version}")

    repo = _require_non_empty_string(data.get("repo"), "repo", errors)
    if repo and repo != expected_repo:
        errors.append(f"repo must be {expected_repo}, got {repo}")

    commit = _require_non_empty_string(data.get("target_commit"), "target_commit", errors)
    if commit and not COMMIT_RE.fullmatch(commit):
        errors.append("target_commit must be a full 40-character lowercase hexadecimal commit SHA")

    ci_run_url = _require_non_empty_string(data.get("ci_run_url"), "ci_run_url", errors)
    candidate_run_url = _require_non_empty_string(data.get("candidate_run_url"), "candidate_run_url", errors)
    _validate_run_url(ci_run_url, "ci_run_url", expected_repo, errors)
    _validate_run_url(candidate_run_url, "candidate_run_url", expected_repo, errors)
    _require_non_empty_string(data.get("tester"), "tester", errors)
    _require_non_empty_string(data.get("tested_at"), "tested_at", errors)

    windows = data.get("windows_environment")
    if not _is_mapping(windows):
        errors.append("windows_environment must be an object")
    else:
        for field in ("os", "artifact_name", "portable_or_installer"):
            _require_non_empty_string(windows.get(field), f"windows_environment.{field}", errors)
        artifact_name = _string(windows.get("artifact_name"))
        if artifact_name and artifact_name != EXPECTED_WINDOWS_ARTIFACT_NAME:
            errors.append(
                "windows_environment.artifact_name must be "
                f"{EXPECTED_WINDOWS_ARTIFACT_NAME}"
            )

    android = data.get("android_device")
    if not _is_mapping(android):
        errors.append("android_device must be an object")
    else:
        for field in ("model", "android_version", "artifact_name", "install_apk"):
            _require_non_empty_string(android.get(field), f"android_device.{field}", errors)
        artifact_name = _string(android.get("artifact_name"))
        if artifact_name and artifact_name != EXPECTED_ANDROID_ARTIFACT_NAME:
            errors.append(
                "android_device.artifact_name must be "
                f"{EXPECTED_ANDROID_ARTIFACT_NAME}"
            )
        install_apk = _string(android.get("install_apk"))
        expected_debug_apk = f"ClipVault-Android-v{expected_source_version}-debug.apk"
        if install_apk and install_apk != expected_debug_apk:
            errors.append(f"android_device.install_apk must be {expected_debug_apk}")
        if install_apk and "unsigned" in install_apk.lower():
            errors.append("android_device.install_apk must not cite the unsigned release APK as the install package")


def _validate_item(
    item_data: object,
    *,
    section_key: str,
    item: EvidenceItem,
    issue: int,
    errors: list[str],
    warnings: list[str],
) -> str | None:
    path = f"sections.{section_key}.items.{item.key}"
    if not _is_mapping(item_data):
        errors.append(f"{path} must be an object")
        return None

    status = _string(item_data.get("status")).lower()
    if status not in VALID_STATUSES:
        errors.append(f"{path}.status must be one of: blocked, fail, pass")
        return None

    evidence = _string(item_data.get("evidence"))
    next_step = _string(item_data.get("next_step"))
    if status == "pass" and (not evidence or _is_placeholder(evidence)):
        errors.append(f"{path}.evidence must be non-empty when status is pass")
    if status == "fail" and (not evidence or _is_placeholder(evidence)):
        errors.append(f"{path}.evidence must describe the observed failure when status is fail")
    if status in {"blocked", "fail"} and (not next_step or _is_placeholder(next_step)):
        errors.append(f"{path}.next_step must describe the remediation or unblock step when status is {status}")
    if status != "pass":
        warnings.append(f"{path} is {status}; Issue #{issue} field-test evidence remains incomplete")
    return status


def validate_evidence(
    data: object,
    *,
    expected_field_test_label: str = DEFAULT_FIELD_TEST_LABEL,
    expected_source_version: str = DEFAULT_SOURCE_VERSION,
    expected_repo: str = DEFAULT_REPO,
    issue: int = DEFAULT_ISSUE,
) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    counts = {status: 0 for status in sorted(VALID_STATUSES)}

    _validate_metadata(
        data,
        expected_field_test_label=expected_field_test_label,
        expected_source_version=expected_source_version,
        expected_repo=expected_repo,
        errors=errors,
    )
    sections = data.get("sections") if _is_mapping(data) else None
    if not _is_mapping(sections):
        errors.append("sections must be an object")
    else:
        for section in REQUIRED_SECTIONS:
            section_data = sections.get(section.key)
            if not _is_mapping(section_data):
                errors.append(f"sections.{section.key} must be an object")
                continue
            items_data = section_data.get("items")
            if not _is_mapping(items_data):
                errors.append(f"sections.{section.key}.items must be an object")
                continue
            for item in section.items:
                if item.key not in items_data:
                    errors.append(f"sections.{section.key}.items.{item.key} is required")
                    continue
                status = _validate_item(
                    items_data[item.key],
                    section_key=section.key,
                    item=item,
                    issue=issue,
                    errors=errors,
                    warnings=warnings,
                )
                if status is not None:
                    counts[status] += 1

    expected_items = sum(len(section.items) for section in REQUIRED_SECTIONS)
    observed_items = sum(counts.values())
    if observed_items != expected_items:
        errors.append(f"expected {expected_items} field-test items, found {observed_items}")

    ok = not errors
    field_test_ready = ok and counts["pass"] == expected_items
    return ValidationResult(
        ok=ok,
        field_test_ready=field_test_ready,
        errors=tuple(errors),
        warnings=tuple(warnings),
        item_counts=counts,
    )


def _escape_table(value: object) -> str:
    text = str(value if value is not None else "").strip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = " / ".join(part.strip() for part in text.split("\n") if part.strip())
    return text.replace("|", "\\|") or "-"


def _nested_mapping(data: dict[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def _item(data: dict[str, object], section_key: str, item_key: str) -> dict[str, object]:
    sections = data.get("sections")
    if not isinstance(sections, dict):
        return {}
    section = sections.get(section_key)
    if not isinstance(section, dict):
        return {}
    items = section.get("items")
    if not isinstance(items, dict):
        return {}
    item = items.get(item_key)
    return item if isinstance(item, dict) else {}


def render_markdown(
    data: dict[str, object],
    result: ValidationResult,
    *,
    issue: int = DEFAULT_ISSUE,
) -> str:
    windows = _nested_mapping(data, "windows_environment")
    android = _nested_mapping(data, "android_device")
    status_line = "PASS" if result.field_test_ready else "BLOCKED"

    lines = [
        f"## v1.7 field-test evidence for Issue #{issue}",
        "",
        f"Status: **{status_line}**",
        "",
        f"- Field-test label: `{_escape_table(data.get('field_test_label'))}`",
        f"- Source version under test: `{_escape_table(data.get('source_version'))}`",
        f"- Target commit: `{_escape_table(data.get('target_commit'))}`",
        f"- CI run: {_escape_table(data.get('ci_run_url'))}",
        f"- Release candidate run: {_escape_table(data.get('candidate_run_url'))}",
        f"- Tester: {_escape_table(data.get('tester'))}",
        f"- Tested at: {_escape_table(data.get('tested_at'))}",
        f"- Windows environment: OS {_escape_table(windows.get('os'))}, artifact {_escape_table(windows.get('artifact_name'))}, package {_escape_table(windows.get('portable_or_installer'))}",
        f"- Android device: model {_escape_table(android.get('model'))}, Android version {_escape_table(android.get('android_version'))}, artifact {_escape_table(android.get('artifact_name'))}, install APK {_escape_table(android.get('install_apk'))}",
        "",
        "Item counts: "
        f"{result.item_counts.get('pass', 0)} pass, "
        f"{result.item_counts.get('fail', 0)} fail, "
        f"{result.item_counts.get('blocked', 0)} blocked.",
        "",
    ]

    for section in REQUIRED_SECTIONS:
        lines.extend([
            f"### {section.title}",
            "",
            "| Item | Status | Evidence | Next step |",
            "|---|---:|---|---|",
        ])
        for expected in section.items:
            item_data = _item(data, section.key, expected.key)
            status = _escape_table(str(item_data.get("status", "")).lower())
            evidence = _escape_table(item_data.get("evidence"))
            next_step = _escape_table(item_data.get("next_step"))
            lines.append(f"| {expected.label} | {status} | {evidence} | {next_step} |")
        lines.append("")

    if result.errors:
        lines.extend(["### Validation errors", ""])
        lines.extend(f"- {error}" for error in result.errors)
        lines.append("")
    if result.warnings:
        lines.extend(["### Incomplete rows", ""])
        lines.extend(f"- {warning}" for warning in result.warnings)
        lines.append("")

    lines.extend([
        "### Scope note",
        "",
        scope_note(),
    ])
    return "\n".join(lines).rstrip() + "\n"


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_template(path: Path, template: dict[str, object]) -> None:
    path.write_text(json.dumps(template, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _emit_json(data: object) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and render v1.7 field-test evidence.")
    parser.add_argument("--field-test-label", default=DEFAULT_FIELD_TEST_LABEL)
    parser.add_argument("--source-version", default=DEFAULT_SOURCE_VERSION)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--issue", type=int, default=DEFAULT_ISSUE)
    parser.add_argument("--template", action="store_true", help="write a JSON evidence template to stdout")
    parser.add_argument("--write-template", type=Path, help="write a JSON evidence template to this path")
    parser.add_argument("--input", type=Path, help="validate and render this JSON evidence file")
    parser.add_argument(
        "--verify-artifacts",
        action="store_true",
        help="verify downloaded release-candidate artifact directories and render a partial Issue #82 comment",
    )
    parser.add_argument("--windows-dir", type=Path, help="downloaded Windows release-candidate artifact directory")
    parser.add_argument("--android-dir", type=Path, help="downloaded Android release-candidate artifact directory")
    parser.add_argument("--target-commit", help="full 40-character target commit SHA for artifact verification")
    parser.add_argument("--ci-run-url", help="matching current-main CI run URL")
    parser.add_argument("--candidate-run-url", help="matching Release candidate dry run URL")
    parser.add_argument("--tester", help="person or agent preparing this evidence draft")
    parser.add_argument("--tested-at", help="ISO-8601 timestamp or dated evidence label")
    parser.add_argument(
        "--windows-smoke-report",
        type=Path,
        help="JSON report from tools/windows_candidate_smoke.py to apply to the portable_launch row",
    )
    parser.add_argument("--output", type=Path, help="write rendered Markdown to this path instead of stdout")
    parser.add_argument("--json", action="store_true", help="emit validation JSON instead of Markdown")
    parser.add_argument("--no-fail", action="store_true", help="return exit code 0 even when evidence is incomplete")
    args = parser.parse_args(list(argv) if argv is not None else None)

    selected_modes = sum(
        1
        for enabled in (
            args.template,
            bool(args.write_template),
            bool(args.input),
            args.verify_artifacts,
        )
        if enabled
    )
    if selected_modes != 1:
        parser.error("choose exactly one of --template, --write-template, --input, or --verify-artifacts")
    if args.output and not (args.input or args.verify_artifacts):
        parser.error("--output requires --input or --verify-artifacts")
    if args.output and args.json:
        parser.error("--output cannot be combined with --json")

    template = build_template(
        field_test_label=args.field_test_label,
        source_version=args.source_version,
        repo=args.repo,
    )
    if args.template:
        _emit_json(template)
        return 0
    if args.write_template:
        write_template(args.write_template, template)
        return 0

    if args.verify_artifacts:
        missing = [
            name
            for name, value in (
                ("--windows-dir", args.windows_dir),
                ("--android-dir", args.android_dir),
                ("--target-commit", args.target_commit),
                ("--ci-run-url", args.ci_run_url),
                ("--candidate-run-url", args.candidate_run_url),
                ("--tester", args.tester),
                ("--tested-at", args.tested_at),
            )
            if not value
        ]
        if missing:
            parser.error("--verify-artifacts requires " + ", ".join(missing))
        try:
            loaded = build_artifact_verified_template(
                windows_dir=args.windows_dir,
                android_dir=args.android_dir,
                target_commit=args.target_commit,
                ci_run_url=args.ci_run_url,
                candidate_run_url=args.candidate_run_url,
                tester=args.tester,
                tested_at=args.tested_at,
                field_test_label=args.field_test_label,
                source_version=args.source_version,
                repo=args.repo,
            )
        except ValueError as exc:
            print(f"field-test artifact verification failed: {exc}")
            return 0 if args.no_fail else 1
    else:
        loaded = load_json(args.input)

    if args.windows_smoke_report:
        if not isinstance(loaded, dict):
            print("field-test evidence must be a JSON object before applying Windows smoke")
            return 0 if args.no_fail else 1
        try:
            apply_windows_smoke_report(loaded, load_json(args.windows_smoke_report))
        except ValueError as exc:
            print(f"field-test Windows smoke merge failed: {exc}")
            return 0 if args.no_fail else 1

    result = validate_evidence(
        loaded,
        expected_field_test_label=args.field_test_label,
        expected_source_version=args.source_version,
        expected_repo=args.repo,
        issue=args.issue,
    )
    if args.json:
        _emit_json(result.as_dict())
    else:
        if not isinstance(loaded, dict):
            loaded = {}
        markdown = render_markdown(loaded, result, issue=args.issue)
        if args.output:
            args.output.write_text(markdown, encoding="utf-8")
        else:
            print(markdown, end="")

    if args.no_fail:
        return 0
    return 0 if result.field_test_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
