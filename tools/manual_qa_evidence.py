#!/usr/bin/env python3
"""Validate and render Issue #36 manual QA evidence.

This helper is intentionally local-only. It reads a JSON evidence file, checks
that every required v1.6.0 manual QA item is represented, and renders a Markdown
comment draft that an Owner can paste into Issue #36 after the checks are truly
run. It does not call GitHub, run device QA, sign artifacts, publish releases,
or edit issue checklists.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_VERSION = "v1.6.0"
DEFAULT_ISSUE = 36
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
VALID_STATUSES = {"pass", "fail", "blocked"}


@dataclass(frozen=True)
class QaItem:
    key: str
    label: str


@dataclass(frozen=True)
class QaSection:
    key: str
    title: str
    items: tuple[QaItem, ...]


REQUIRED_SECTIONS: tuple[QaSection, ...] = (
    QaSection(
        key="android_device_qa",
        title="Manual Android device QA",
        items=(
            QaItem("pairing", "Pair Android with the desktop node using a one-time pairing code."),
            QaItem("share_capture", "Share text from another app into ClipVault and confirm it appears locally."),
            QaItem("qs_tile_capture", "Use the Quick Settings tile to explicitly save current clipboard content."),
            QaItem("panel_ime_paste", "Enable ClipVault Panel IME and confirm a candidate tap commits text."),
            QaItem("explicit_save_requires_tap", "Confirm Panel IME explicit save requires a user tap."),
        ),
    ),
    QaSection(
        key="ime_privacy_qa",
        title="Manual IME privacy QA",
        items=(
            QaItem("normal_field_candidates", "Open a normal text field and confirm candidates can appear."),
            QaItem("sensitive_field_entry", "Move to password/incognito/no-suggestions fields."),
            QaItem("sensitive_field_suppression", "Confirm candidates are hidden or replaced with the suppression message."),
            QaItem("inflight_candidates_cleared", "Confirm in-flight candidates are cleared on the transition into a sensitive field."),
            QaItem("typed_text_not_persisted", "Confirm typed text is not written to Room, outbox, logs, sync payloads, or desktop storage."),
        ),
    ),
    QaSection(
        key="sync_qa",
        title="Manual sync QA",
        items=(
            QaItem("public_clips_memory_sync", "Confirm public clips and memory sync desktop <-> Android."),
            QaItem("secret_private_isolation", "Confirm secret/private content remains isolated according to the current contracts."),
        ),
    ),
    QaSection(
        key="windows_clipboard_privacy_qa",
        title="Manual Windows clipboard privacy QA",
        items=(
            QaItem("exclude_monitor", "`ExcludeClipboardContentFromMonitorProcessing` prevents capture."),
            QaItem("viewer_ignore", "`Clipboard Viewer Ignore` prevents capture."),
            QaItem("history_off", "`CanIncludeInClipboardHistory=0` prevents capture."),
            QaItem("cloud_off", "`CanUploadToCloudClipboard=0` prevents capture."),
            QaItem("normal_control", "A normal text clipboard item without those formats is still captured."),
        ),
    ),
)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    release_ready: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    item_counts: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "release_ready": self.release_ready,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "item_counts": dict(self.item_counts),
            "scope_note": scope_note(),
        }


def scope_note() -> str:
    return (
        "This manual QA report does not replace signed artifact evidence, final "
        "Windows artifact evidence, signed Android APK evidence, release "
        "environment/secrets evidence, or Owner-approved v1.6.0 GitHub Release "
        "publication."
    )


def build_template(version: str = DEFAULT_VERSION) -> dict[str, object]:
    return {
        "version": version,
        "target_commit": "REPLACE_WITH_40_HEX_MAIN_COMMIT",
        "tester": "REPLACE_WITH_TESTER_NAME",
        "tested_at": "REPLACE_WITH_ISO_8601_TIMESTAMP",
        "android_device": {
            "model": "REPLACE_WITH_DEVICE_MODEL",
            "android_version": "REPLACE_WITH_ANDROID_VERSION",
            "app_version": "1.6.0",
            "apk_source": "REPLACE_WITH_APK_OR_WORKFLOW_ARTIFACT",
        },
        "desktop_environment": {
            "os": "REPLACE_WITH_WINDOWS_VERSION",
            "app_version": "1.6.0",
            "build_source": "REPLACE_WITH_EXE_OR_WORKFLOW_ARTIFACT",
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


def _validate_metadata(data: object, expected_version: str, errors: list[str]) -> None:
    if not _is_mapping(data):
        errors.append("root must be a JSON object")
        return

    version = _require_non_empty_string(data.get("version"), "version", errors)
    if version and version != expected_version:
        errors.append(f"version must be {expected_version}, got {version}")

    commit = _require_non_empty_string(data.get("target_commit"), "target_commit", errors)
    if commit and not COMMIT_RE.fullmatch(commit):
        errors.append("target_commit must be a full 40-character hexadecimal commit SHA")

    _require_non_empty_string(data.get("tester"), "tester", errors)
    _require_non_empty_string(data.get("tested_at"), "tested_at", errors)

    android_device = data.get("android_device")
    if not _is_mapping(android_device):
        errors.append("android_device must be an object")
    else:
        for field in ("model", "android_version", "app_version", "apk_source"):
            _require_non_empty_string(android_device.get(field), f"android_device.{field}", errors)

    desktop_environment = data.get("desktop_environment")
    if not _is_mapping(desktop_environment):
        errors.append("desktop_environment must be an object")
    else:
        for field in ("os", "app_version", "build_source"):
            _require_non_empty_string(
                desktop_environment.get(field),
                f"desktop_environment.{field}",
                errors,
            )


def _validate_item(
    item_data: object,
    *,
    section_key: str,
    item: QaItem,
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
        warnings.append(f"{path} is {status}; Issue #{DEFAULT_ISSUE} manual QA remains incomplete")
    return status


def validate_evidence(data: object, *, expected_version: str = DEFAULT_VERSION) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    counts = {status: 0 for status in sorted(VALID_STATUSES)}

    _validate_metadata(data, expected_version, errors)
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
                    errors=errors,
                    warnings=warnings,
                )
                if status is not None:
                    counts[status] += 1

    expected_items = sum(len(section.items) for section in REQUIRED_SECTIONS)
    observed_items = sum(counts.values())
    if observed_items != expected_items:
        errors.append(f"expected {expected_items} QA items, found {observed_items}")

    ok = not errors
    release_ready = ok and counts["pass"] == expected_items
    return ValidationResult(
        ok=ok,
        release_ready=release_ready,
        errors=tuple(errors),
        warnings=tuple(warnings),
        item_counts=counts,
    )


def _escape_table(value: object) -> str:
    text = str(value if value is not None else "").strip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = " / ".join(part.strip() for part in text.split("\n") if part.strip())
    return text.replace("|", "\\|") or "-"


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
    android = data.get("android_device") if isinstance(data.get("android_device"), dict) else {}
    desktop = data.get("desktop_environment") if isinstance(data.get("desktop_environment"), dict) else {}
    status_line = "PASS" if result.release_ready else "BLOCKED"

    lines = [
        f"## Manual QA evidence for Issue #{issue}",
        "",
        f"Status: **{status_line}**",
        "",
        f"- Version: `{_escape_table(data.get('version'))}`",
        f"- Target commit: `{_escape_table(data.get('target_commit'))}`",
        f"- Tester: {_escape_table(data.get('tester'))}",
        f"- Tested at: {_escape_table(data.get('tested_at'))}",
        f"- Android device: {_escape_table(android.get('model'))}, Android {_escape_table(android.get('android_version'))}, app {_escape_table(android.get('app_version'))}, APK source {_escape_table(android.get('apk_source'))}",
        f"- Desktop environment: {_escape_table(desktop.get('os'))}, app {_escape_table(desktop.get('app_version'))}, build source {_escape_table(desktop.get('build_source'))}",
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
    parser = argparse.ArgumentParser(description="Validate and render v1.6.0 manual QA evidence.")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--issue", type=int, default=DEFAULT_ISSUE)
    parser.add_argument("--template", action="store_true", help="write a JSON evidence template to stdout")
    parser.add_argument("--write-template", type=Path, help="write a JSON evidence template to this path")
    parser.add_argument("--input", type=Path, help="validate and render this JSON evidence file")
    parser.add_argument("--output", type=Path, help="write rendered Markdown to this path instead of stdout")
    parser.add_argument("--json", action="store_true", help="emit validation JSON instead of Markdown")
    parser.add_argument("--no-fail", action="store_true", help="return exit code 0 even when evidence is incomplete")
    args = parser.parse_args(list(argv) if argv is not None else None)

    selected_modes = sum(1 for enabled in (args.template, bool(args.write_template), bool(args.input)) if enabled)
    if selected_modes != 1:
        parser.error("choose exactly one of --template, --write-template, or --input")
    if args.output and not args.input:
        parser.error("--output requires --input")
    if args.output and args.json:
        parser.error("--output cannot be combined with --json")

    template = build_template(args.version)
    if args.template:
        _emit_json(template)
        return 0
    if args.write_template:
        write_template(args.write_template, template)
        return 0

    loaded = load_json(args.input)
    result = validate_evidence(loaded, expected_version=args.version)
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
    return 0 if result.release_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
