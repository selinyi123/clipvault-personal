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
from datetime import datetime
from pathlib import Path
from typing import Iterable

DEFAULT_VERSION = "v1.6.0"
DEFAULT_ISSUE = 36
SCHEMA_VERSION = 2
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
WINDOWS_PATH_IN_TEXT_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")
UNC_BACKSLASH_PATH_IN_TEXT_RE = re.compile(r"\\\\[^\\\s]")
FORWARD_UNC_PATH_IN_TEXT_RE = re.compile(r"(?:^|[\s=\[{(,;])//[^/\s]")
POSIX_PRIVATE_PATH_IN_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9])/(?:home|Users|tmp|var|mnt|private|opt)/",
    re.IGNORECASE,
)
VALID_STATUSES = {"pass", "fail", "blocked"}
VALID_DEVICE_TYPES = {"emulator", "physical"}
VALID_BUILD_VARIANTS = {"debug", "release"}
CURSORWINDOW_TEST_NAME = (
    "com.clipvault.app.capture.CaptureTransactionTest#"
    "maxControlCharacterCaptureCanBeReadThroughBoundedOutboxChunks"
)
CURSORWINDOW_MIN_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_SYNC_PUSH_REQUEST_BYTES = 6 * 1024 * 1024 + 64 * 1024


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
            "evidence_assurance": "owner_attested_structural_validation",
            "scope_note": scope_note(),
        }


def scope_note() -> str:
    return (
        "This manual QA report does not replace signed artifact evidence, final "
        "Windows artifact evidence, signed Android APK evidence, release "
        "environment/secrets evidence, or Owner-approved v1.6.0 GitHub Release "
        "publication. References, digests, counters, and observations are "
        "Owner-attested inputs: this structural validator does not fetch or "
        "independently parse the referenced device/JUnit evidence."
    )


def _app_version(version: str) -> str:
    return version.removeprefix("v")


def _signed_apk_name(version: str) -> str:
    return f"ClipVault-Android-{version}-release-signed.apk"


def _android_run_template(
    *,
    run_id: str,
    sdk_int: int | str,
    android_version: str,
    device_type: str,
    model: str,
    build_variant: str,
    version: str,
    apk_name: str,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "source_commit": "REPLACE_WITH_40_HEX_MAIN_COMMIT",
        "sdk_int": sdk_int,
        "android_version": android_version,
        "device_type": device_type,
        "model": model,
        "build_variant": build_variant,
        "app_version": _app_version(version),
        "apk_name": apk_name,
        "apk_source": "REPLACE_WITH_WORKFLOW_ARTIFACT_OR_RELEASE_URL",
        "apk_sha256": "REPLACE_WITH_64_HEX_APK_SHA256",
        "test_apk_name": "app-debug-androidTest.apk" if build_variant == "debug" else "",
        "test_apk_sha256": (
            "REPLACE_WITH_64_HEX_ANDROID_TEST_APK_SHA256" if build_variant == "debug" else ""
        ),
        "artifact_evidence_ref": (
            "REPLACE_WITH_VALIDATED_RELEASE_ARTIFACT_EVIDENCE_REF"
            if build_variant == "release"
            else ""
        ),
        "tested_at": "REPLACE_WITH_ISO_8601_TIMESTAMP",
    }


def build_template(version: str = DEFAULT_VERSION) -> dict[str, object]:
    if version != DEFAULT_VERSION:
        raise ValueError(f"manual QA evidence helper only supports {DEFAULT_VERSION}")
    return {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "target_commit": "REPLACE_WITH_40_HEX_MAIN_COMMIT",
        "final_signed_android_run_id": "signed-release-physical",
        "tester": "REPLACE_WITH_TESTER_NAME",
        "tested_at": "REPLACE_WITH_ISO_8601_TIMESTAMP",
        "android_runs": [
            _android_run_template(
                run_id="api26-cursorwindow",
                sdk_int=26,
                android_version="8.0",
                device_type="REPLACE_WITH_physical_OR_emulator",
                model="REPLACE_WITH_API_26_DEVICE_OR_EMULATOR_MODEL",
                build_variant="debug",
                version=version,
                apk_name="app-debug.apk",
            ),
            _android_run_template(
                run_id="api27-cursorwindow",
                sdk_int=27,
                android_version="8.1",
                device_type="REPLACE_WITH_physical_OR_emulator",
                model="REPLACE_WITH_API_27_DEVICE_OR_EMULATOR_MODEL",
                build_variant="debug",
                version=version,
                apk_name="app-debug.apk",
            ),
            _android_run_template(
                run_id="signed-release-physical",
                sdk_int="REPLACE_WITH_DEVICE_SDK_INT",
                android_version="REPLACE_WITH_ANDROID_VERSION",
                device_type="physical",
                model="REPLACE_WITH_PHYSICAL_DEVICE_MODEL",
                build_variant="release",
                version=version,
                apk_name=_signed_apk_name(version),
            ),
        ],
        "android_compatibility_qa": {
            "cursorwindow_large_payload": {
                "status": "blocked",
                "test_name": CURSORWINDOW_TEST_NAME,
                "results": [
                    {
                        "run_id": run_id,
                        "executed": 0,
                        "failures": 0,
                        "errors": 0,
                        "skipped": 0,
                        "payload_bytes": 0,
                        "wire_bytes": 0,
                        "sdk_evidence_ref": "",
                        "sdk_evidence_sha256": "",
                        "result_ref": "",
                        "result_sha256": "",
                    }
                    for run_id in ("api26-cursorwindow", "api27-cursorwindow")
                ],
                "next_step": (
                    "Run the named instrumented regression once on API 26 and once on API 27; "
                    "record non-skipped JUnit evidence and its SHA-256."
                ),
                "evidence": "",
            }
        },
        "desktop_environment": {
            "os": "REPLACE_WITH_WINDOWS_VERSION",
            "app_version": _app_version(version),
            "build_source": "REPLACE_WITH_EXE_OR_WORKFLOW_ARTIFACT",
            "source_commit": "REPLACE_WITH_40_HEX_MAIN_COMMIT",
        },
        "sections": {
            section.key: {
                "title": section.title,
                "items": {
                    item.key: {
                        "status": "blocked",
                        "evidence": "",
                        **(
                            {"run_ids": ["signed-release-physical"]}
                            if section.key in {"android_device_qa", "ime_privacy_qa", "sync_qa"}
                            else {}
                        ),
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
    return value.upper().startswith("REPLACE_WITH_")


def _contains_private_local_path(value: str) -> bool:
    return (
        "file://" in value.lower()
        or value.startswith(("\\\\", "//", "/", "~/", "~\\"))
        or bool(WINDOWS_PATH_IN_TEXT_RE.search(value))
        or bool(UNC_BACKSLASH_PATH_IN_TEXT_RE.search(value))
        or bool(FORWARD_UNC_PATH_IN_TEXT_RE.search(value))
        or bool(POSIX_PRIVATE_PATH_IN_TEXT_RE.search(value))
    )


def _reject_private_path_in_free_text(value: str, path: str, errors: list[str]) -> None:
    if value and _contains_private_local_path(value):
        errors.append(
            f"{path} must not contain an absolute local/UNC path or file URI; "
            "use a redacted relative label"
        )


def _require_non_empty_string(data: object, path: str, errors: list[str]) -> str:
    value = _string(data)
    if not value:
        errors.append(f"{path} must be a non-empty string")
    elif _is_placeholder(value):
        errors.append(f"{path} must replace the template placeholder")
    return value


def _require_public_reference(
    data: object,
    path: str,
    errors: list[str],
    *,
    required: bool = True,
) -> str:
    value = _string(data)
    if required:
        value = _require_non_empty_string(data, path, errors)
    elif value and _is_placeholder(value):
        errors.append(f"{path} must replace the template placeholder")
    if value and not _is_placeholder(value) and _contains_private_local_path(value):
        errors.append(
            f"{path} must use a public URL, workflow/run reference, or short relative evidence label; "
            "absolute local paths and file URIs are not allowed"
        )
    return value


def _parse_iso8601(value: str) -> datetime | None:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _require_iso8601(data: object, path: str, errors: list[str]) -> str:
    value = _require_non_empty_string(data, path, errors)
    if not value or _is_placeholder(value):
        return value
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        errors.append(f"{path} must be an ISO-8601 timestamp with a timezone")
        return value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(f"{path} must include an explicit timezone")
    return value


def _require_sha256(data: object, path: str, errors: list[str]) -> str:
    value = _require_non_empty_string(data, path, errors)
    if value and not _is_placeholder(value) and not SHA256_RE.fullmatch(value):
        errors.append(f"{path} must be a 64-character hexadecimal SHA-256 digest")
    return value


def _require_int(data: object, path: str, errors: list[str], *, minimum: int = 0) -> int | None:
    if isinstance(data, bool) or not isinstance(data, int):
        errors.append(f"{path} must be an integer")
        return None
    if data < minimum:
        errors.append(f"{path} must be at least {minimum}")
        return None
    return data


def _is_signed_release_run(
    run: dict[str, object],
    expected_version: str,
    target_commit: str,
) -> bool:
    return (
        _string(run.get("device_type")).lower() == "physical"
        and _string(run.get("build_variant")).lower() == "release"
        and _string(run.get("apk_name")) == _signed_apk_name(expected_version)
        and bool(SHA256_RE.fullmatch(_string(run.get("apk_sha256"))))
        and _string(run.get("source_commit")).lower() == target_commit.lower()
        and bool(_string(run.get("artifact_evidence_ref")))
        and not _is_placeholder(_string(run.get("artifact_evidence_ref")))
    )


def _validate_android_runs(
    data: object,
    expected_version: str,
    target_commit: str,
    errors: list[str],
) -> dict[str, dict[str, object]]:
    if not isinstance(data, list):
        errors.append("android_runs must be an array")
        return {}

    expected_app_version = _app_version(expected_version)
    runs: dict[str, dict[str, object]] = {}
    for index, raw_run in enumerate(data):
        path = f"android_runs[{index}]"
        if not _is_mapping(raw_run):
            errors.append(f"{path} must be an object")
            continue

        run_id = _require_non_empty_string(raw_run.get("run_id"), f"{path}.run_id", errors)
        if run_id and not _is_placeholder(run_id) and not RUN_ID_RE.fullmatch(run_id):
            errors.append(f"{path}.run_id must use only letters, digits, dot, underscore, or hyphen")
        if run_id in runs:
            errors.append(f"{path}.run_id duplicates {run_id}")
        elif run_id and not _is_placeholder(run_id):
            runs[run_id] = raw_run

        sdk_int = _require_int(raw_run.get("sdk_int"), f"{path}.sdk_int", errors, minimum=1)
        if sdk_int is not None and sdk_int > 100:
            errors.append(f"{path}.sdk_int must be at most 100")

        source_commit = _require_non_empty_string(
            raw_run.get("source_commit"), f"{path}.source_commit", errors
        )
        if source_commit and not _is_placeholder(source_commit):
            if not COMMIT_RE.fullmatch(source_commit):
                errors.append(f"{path}.source_commit must be a full 40-character hexadecimal commit SHA")
            elif target_commit and COMMIT_RE.fullmatch(target_commit) and source_commit.lower() != target_commit.lower():
                errors.append(f"{path}.source_commit must match target_commit")

        for field in (
            "android_version",
            "model",
            "apk_name",
        ):
            _require_non_empty_string(raw_run.get(field), f"{path}.{field}", errors)
        _require_public_reference(raw_run.get("apk_source"), f"{path}.apk_source", errors)
        _require_public_reference(
            raw_run.get("artifact_evidence_ref"),
            f"{path}.artifact_evidence_ref",
            errors,
            required=False,
        )

        device_type = _require_non_empty_string(
            raw_run.get("device_type"), f"{path}.device_type", errors
        ).lower()
        if device_type and not _is_placeholder(device_type) and device_type not in VALID_DEVICE_TYPES:
            errors.append(f"{path}.device_type must be one of: emulator, physical")

        build_variant = _require_non_empty_string(
            raw_run.get("build_variant"), f"{path}.build_variant", errors
        ).lower()
        if build_variant and build_variant not in VALID_BUILD_VARIANTS:
            errors.append(f"{path}.build_variant must be one of: debug, release")
        if build_variant == "debug":
            test_apk_name = _require_non_empty_string(
                raw_run.get("test_apk_name"), f"{path}.test_apk_name", errors
            )
            if test_apk_name and test_apk_name != "app-debug-androidTest.apk":
                errors.append(f"{path}.test_apk_name must be app-debug-androidTest.apk")
            test_apk_sha256 = _require_sha256(
                raw_run.get("test_apk_sha256"), f"{path}.test_apk_sha256", errors
            ).lower()
        else:
            test_apk_sha256 = ""

        app_version = _require_non_empty_string(
            raw_run.get("app_version"), f"{path}.app_version", errors
        )
        if app_version and app_version != expected_app_version:
            errors.append(
                f"{path}.app_version must be {expected_app_version}, got {app_version}"
            )
        apk_sha256 = _require_sha256(
            raw_run.get("apk_sha256"), f"{path}.apk_sha256", errors
        ).lower()
        if (
            build_variant == "debug"
            and SHA256_RE.fullmatch(apk_sha256)
            and SHA256_RE.fullmatch(test_apk_sha256)
            and apk_sha256 == test_apk_sha256
        ):
            errors.append(f"{path}.apk_sha256 and test_apk_sha256 must identify different APKs")
        _require_iso8601(raw_run.get("tested_at"), f"{path}.tested_at", errors)

    if not runs:
        errors.append("android_runs must contain API 26, API 27, and final signed release runs")
    return runs


def _validate_metadata(
    data: object,
    expected_version: str,
    errors: list[str],
) -> dict[str, dict[str, object]]:
    if not _is_mapping(data):
        errors.append("root must be a JSON object")
        return {}

    schema_version = data.get("schema_version")
    if schema_version != SCHEMA_VERSION or isinstance(schema_version, bool):
        errors.append(
            f"schema_version must be {SCHEMA_VERSION}; regenerate the template instead of reusing v1 evidence"
        )

    version = _require_non_empty_string(data.get("version"), "version", errors)
    if version and version != expected_version:
        errors.append(f"version must be {expected_version}, got {version}")

    commit = _require_non_empty_string(data.get("target_commit"), "target_commit", errors)
    if commit and not COMMIT_RE.fullmatch(commit):
        errors.append("target_commit must be a full 40-character hexadecimal commit SHA")

    _require_non_empty_string(data.get("tester"), "tester", errors)
    tested_at = _require_iso8601(data.get("tested_at"), "tested_at", errors)

    runs = _validate_android_runs(data.get("android_runs"), expected_version, commit, errors)
    report_time = _parse_iso8601(tested_at)
    if report_time is not None:
        for run_id, run in runs.items():
            run_time = _parse_iso8601(_string(run.get("tested_at")))
            if run_time is not None and run_time > report_time:
                errors.append(f"android run {run_id} tested_at must not be later than report tested_at")
    final_run_id = _require_non_empty_string(
        data.get("final_signed_android_run_id"),
        "final_signed_android_run_id",
        errors,
    )
    final_run = runs.get(final_run_id)
    if final_run_id and not _is_placeholder(final_run_id) and final_run is None:
        errors.append("final_signed_android_run_id must reference android_runs")
    elif final_run is not None:
        artifact_ref = _string(final_run.get("artifact_evidence_ref"))
        if not artifact_ref or _is_placeholder(artifact_ref):
            errors.append(
                "final signed android run artifact_evidence_ref must reference validated release artifact evidence"
            )
        if not _is_signed_release_run(final_run, expected_version, commit):
            errors.append(
                "final_signed_android_run_id must reference a physical release run bound to target_commit, "
                "the exact signed APK name/SHA-256, and artifact evidence"
            )
        final_digest = _string(final_run.get("apk_sha256")).lower()
        debug_digests = {
            _string(run.get(field)).lower()
            for run in runs.values()
            if _string(run.get("build_variant")).lower() == "debug"
            for field in ("apk_sha256", "test_apk_sha256")
            if SHA256_RE.fullmatch(_string(run.get(field)))
        }
        if SHA256_RE.fullmatch(final_digest) and final_digest in debug_digests:
            errors.append(
                "final signed APK SHA-256 must differ from every debug app and instrumentation APK"
            )

    desktop_environment = data.get("desktop_environment")
    if not _is_mapping(desktop_environment):
        errors.append("desktop_environment must be an object")
    else:
        for field in ("os", "app_version"):
            _require_non_empty_string(
                desktop_environment.get(field),
                f"desktop_environment.{field}",
                errors,
            )
        _require_public_reference(
            desktop_environment.get("build_source"),
            "desktop_environment.build_source",
            errors,
        )
        app_version = _string(desktop_environment.get("app_version"))
        expected_app_version = _app_version(expected_version)
        if app_version and not _is_placeholder(app_version) and app_version != expected_app_version:
            errors.append(
                "desktop_environment.app_version must be "
                f"{expected_app_version}, got {app_version}"
            )
        desktop_commit = _require_non_empty_string(
            desktop_environment.get("source_commit"),
            "desktop_environment.source_commit",
            errors,
        )
        if desktop_commit and not _is_placeholder(desktop_commit):
            if not COMMIT_RE.fullmatch(desktop_commit):
                errors.append(
                    "desktop_environment.source_commit must be a full 40-character hexadecimal commit SHA"
                )
            elif commit and COMMIT_RE.fullmatch(commit) and desktop_commit.lower() != commit.lower():
                errors.append("desktop_environment.source_commit must match target_commit")
    return runs


def _validate_cursorwindow_evidence(
    data: object,
    runs: dict[str, dict[str, object]],
    *,
    errors: list[str],
    warnings: list[str],
) -> str | None:
    path = "android_compatibility_qa.cursorwindow_large_payload"
    if not _is_mapping(data):
        errors.append(f"{path} must be an object")
        return None

    status = _string(data.get("status")).lower()
    if status not in VALID_STATUSES:
        errors.append(f"{path}.status must be one of: blocked, fail, pass")
        return None

    test_name = _require_non_empty_string(data.get("test_name"), f"{path}.test_name", errors)
    if test_name and test_name != CURSORWINDOW_TEST_NAME:
        errors.append(f"{path}.test_name must be {CURSORWINDOW_TEST_NAME}")

    results = data.get("results")
    if not isinstance(results, list):
        errors.append(f"{path}.results must be an array")
        results = []

    covered_sdks: list[int] = []
    observed_run_ids: set[str] = set()
    # JUnit and SDK-version outputs are independent evidence, regardless of
    # which API row names them. Keep one namespace for references and one for
    # digests so a file cannot change roles between API 26 and API 27.
    observed_evidence_refs: set[str] = set()
    observed_evidence_digests: set[str] = set()
    apk_digests = {
        _string(run.get(field)).lower()
        for run in runs.values()
        for field in ("apk_sha256", "test_apk_sha256")
        if SHA256_RE.fullmatch(_string(run.get(field)))
    }
    for index, raw_result in enumerate(results):
        result_path = f"{path}.results[{index}]"
        if not _is_mapping(raw_result):
            errors.append(f"{result_path} must be an object")
            continue

        run_id = _require_non_empty_string(raw_result.get("run_id"), f"{result_path}.run_id", errors)
        if run_id in observed_run_ids:
            errors.append(f"{result_path}.run_id duplicates {run_id}")
        observed_run_ids.add(run_id)
        run = runs.get(run_id)
        if run is None and run_id and not _is_placeholder(run_id):
            errors.append(f"{result_path}.run_id does not reference android_runs")

        executed = _require_int(raw_result.get("executed"), f"{result_path}.executed", errors)
        failures = _require_int(raw_result.get("failures"), f"{result_path}.failures", errors)
        error_count = _require_int(raw_result.get("errors"), f"{result_path}.errors", errors)
        skipped = _require_int(raw_result.get("skipped"), f"{result_path}.skipped", errors)
        payload_bytes = _require_int(
            raw_result.get("payload_bytes"), f"{result_path}.payload_bytes", errors
        )
        wire_bytes = _require_int(raw_result.get("wire_bytes"), f"{result_path}.wire_bytes", errors)

        if status == "pass":
            result_ref = _require_public_reference(
                raw_result.get("result_ref"), f"{result_path}.result_ref", errors
            )
            result_digest = _require_sha256(
                raw_result.get("result_sha256"), f"{result_path}.result_sha256", errors
            ).lower()
            sdk_ref = _require_public_reference(
                raw_result.get("sdk_evidence_ref"), f"{result_path}.sdk_evidence_ref", errors
            )
            sdk_digest = _require_sha256(
                raw_result.get("sdk_evidence_sha256"),
                f"{result_path}.sdk_evidence_sha256",
                errors,
            ).lower()
            if result_ref and sdk_ref and result_ref == sdk_ref:
                errors.append(
                    f"{result_path}.result_ref and sdk_evidence_ref must identify different evidence"
                )
            if (
                SHA256_RE.fullmatch(result_digest)
                and SHA256_RE.fullmatch(sdk_digest)
                and result_digest == sdk_digest
            ):
                errors.append(
                    f"{result_path}.result_sha256 and sdk_evidence_sha256 must identify different evidence"
                )
            for value, field in (
                (result_ref, "result_ref"),
                (sdk_ref, "sdk_evidence_ref"),
            ):
                if not value or _is_placeholder(value):
                    continue
                if value in observed_evidence_refs:
                    errors.append(
                        f"{result_path}.{field} must be unique across "
                        "all API 26 and API 27 result and SDK references"
                    )
                observed_evidence_refs.add(value)
            for value, field in (
                (result_digest, "result_sha256"),
                (sdk_digest, "sdk_evidence_sha256"),
            ):
                if not SHA256_RE.fullmatch(value):
                    continue
                if value in observed_evidence_digests:
                    errors.append(
                        f"{result_path}.{field} must be unique across "
                        "all API 26 and API 27 result and SDK SHA-256 values"
                    )
                if value in apk_digests:
                    errors.append(
                        f"{result_path}.{field} must not reuse an app or instrumentation APK SHA-256"
                    )
                observed_evidence_digests.add(value)
            if executed != 1:
                errors.append(f"{result_path}.executed must be 1 when status is pass")
            if failures != 0:
                errors.append(f"{result_path}.failures must be 0 when status is pass")
            if error_count != 0:
                errors.append(f"{result_path}.errors must be 0 when status is pass")
            if skipped != 0:
                errors.append(f"{result_path}.skipped must be 0 when status is pass")
            if payload_bytes is not None and payload_bytes <= CURSORWINDOW_MIN_PAYLOAD_BYTES:
                errors.append(
                    f"{result_path}.payload_bytes must be greater than {CURSORWINDOW_MIN_PAYLOAD_BYTES}"
                )
            if wire_bytes is not None and not (0 < wire_bytes <= MAX_SYNC_PUSH_REQUEST_BYTES):
                errors.append(
                    f"{result_path}.wire_bytes must be between 1 and {MAX_SYNC_PUSH_REQUEST_BYTES}"
                )
            if run is not None:
                sdk_int = run.get("sdk_int")
                build_variant = _string(run.get("build_variant")).lower()
                if sdk_int not in {26, 27}:
                    errors.append(f"{result_path}.run_id must reference an API 26 or API 27 run")
                elif isinstance(sdk_int, int):
                    covered_sdks.append(sdk_int)
                if build_variant != "debug":
                    errors.append(f"{result_path}.run_id must reference a debug instrumentation run")
                if _string(run.get("apk_name")) != "app-debug.apk":
                    errors.append(f"{result_path}.run_id must reference app-debug.apk")

    next_step = _string(data.get("next_step"))
    evidence = _string(data.get("evidence"))
    _reject_private_path_in_free_text(evidence, f"{path}.evidence", errors)
    _reject_private_path_in_free_text(next_step, f"{path}.next_step", errors)
    if status == "fail" and (not evidence or _is_placeholder(evidence)):
        errors.append(f"{path}.evidence must describe the failed API 26/27 execution")
    if status in {"blocked", "fail"} and (not next_step or _is_placeholder(next_step)):
        errors.append(f"{path}.next_step must describe the remediation when status is {status}")
    if status != "pass":
        warnings.append(f"{path} is {status}; Issue #{DEFAULT_ISSUE} manual QA remains incomplete")
    elif sorted(covered_sdks) != [26, 27]:
        errors.append(
            f"{path}.results must include exactly one non-skipped passing run for API 26 and API 27"
        )
    return status


def _validate_item(
    item_data: object,
    *,
    section_key: str,
    item: QaItem,
    runs: dict[str, dict[str, object]],
    final_run_id: str,
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
    _reject_private_path_in_free_text(evidence, f"{path}.evidence", errors)
    _reject_private_path_in_free_text(next_step, f"{path}.next_step", errors)
    if status == "pass" and (not evidence or _is_placeholder(evidence)):
        errors.append(f"{path}.evidence must be non-empty when status is pass")
    if status == "fail" and (not evidence or _is_placeholder(evidence)):
        errors.append(f"{path}.evidence must describe the observed failure when status is fail")
    if status in {"blocked", "fail"} and (not next_step or _is_placeholder(next_step)):
        errors.append(f"{path}.next_step must describe the remediation or unblock step when status is {status}")

    if section_key in {"android_device_qa", "ime_privacy_qa", "sync_qa"}:
        raw_run_ids = item_data.get("run_ids")
        if raw_run_ids is None and status != "pass":
            raw_run_ids = []
        if not isinstance(raw_run_ids, list):
            errors.append(f"{path}.run_ids must be an array")
            raw_run_ids = []
        referenced_runs: list[dict[str, object]] = []
        seen_run_ids: set[str] = set()
        for index, raw_run_id in enumerate(raw_run_ids):
            run_id = _string(raw_run_id)
            if not run_id:
                errors.append(f"{path}.run_ids[{index}] must be a non-empty string")
                continue
            if run_id in seen_run_ids:
                errors.append(f"{path}.run_ids[{index}] duplicates {run_id}")
                continue
            seen_run_ids.add(run_id)
            run = runs.get(run_id)
            if run is None:
                errors.append(f"{path}.run_ids[{index}] does not reference android_runs")
            else:
                referenced_runs.append(run)
        if status == "pass" and not referenced_runs:
            errors.append(f"{path}.run_ids must reference the physical final signed APK run")
        elif status == "pass" and final_run_id not in seen_run_ids:
            errors.append(f"{path}.run_ids must include the physical final signed APK run")

    if status != "pass":
        warnings.append(f"{path} is {status}; Issue #{DEFAULT_ISSUE} manual QA remains incomplete")
    return status


def validate_evidence(data: object, *, expected_version: str = DEFAULT_VERSION) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    counts = {status: 0 for status in sorted(VALID_STATUSES)}

    if expected_version != DEFAULT_VERSION:
        errors.append(f"manual QA evidence helper only supports {DEFAULT_VERSION}")

    runs = _validate_metadata(data, expected_version, errors)
    final_run_id = _string(data.get("final_signed_android_run_id")) if _is_mapping(data) else ""
    compatibility = data.get("android_compatibility_qa") if _is_mapping(data) else None
    if not _is_mapping(compatibility):
        errors.append("android_compatibility_qa must be an object")
        cursorwindow = None
    else:
        cursorwindow = compatibility.get("cursorwindow_large_payload")
    compatibility_status = _validate_cursorwindow_evidence(
        cursorwindow,
        runs,
        errors=errors,
        warnings=warnings,
    )
    if compatibility_status is not None:
        counts[compatibility_status] += 1

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
                    runs=runs,
                    final_run_id=final_run_id,
                    errors=errors,
                    warnings=warnings,
                )
                if status is not None:
                    counts[status] += 1

    expected_items = 1 + sum(len(section.items) for section in REQUIRED_SECTIONS)
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
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
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
    android_runs = data.get("android_runs") if isinstance(data.get("android_runs"), list) else []
    compatibility = (
        data.get("android_compatibility_qa")
        if isinstance(data.get("android_compatibility_qa"), dict)
        else {}
    )
    cursorwindow = (
        compatibility.get("cursorwindow_large_payload")
        if isinstance(compatibility.get("cursorwindow_large_payload"), dict)
        else {}
    )
    desktop = data.get("desktop_environment") if isinstance(data.get("desktop_environment"), dict) else {}
    status_line = "PASS (OWNER-ATTESTED)" if result.release_ready else "BLOCKED"

    lines = [
        f"## Manual QA evidence for Issue #{issue}",
        "",
        f"Status: **{status_line}**",
        "",
        f"- Evidence schema: `{_escape_table(data.get('schema_version'))}`",
        f"- Version: `{_escape_table(data.get('version'))}`",
        f"- Target commit: `{_escape_table(data.get('target_commit'))}`",
        f"- Final signed Android run: `{_escape_table(data.get('final_signed_android_run_id'))}`",
        f"- Tester: {_escape_table(data.get('tester'))}",
        f"- Tested at: {_escape_table(data.get('tested_at'))}",
        f"- Desktop environment: {_escape_table(desktop.get('os'))}, app {_escape_table(desktop.get('app_version'))}, source commit `{_escape_table(desktop.get('source_commit'))}`, build source {_escape_table(desktop.get('build_source'))}",
        "",
        "Item counts: "
        f"{result.item_counts.get('pass', 0)} pass, "
        f"{result.item_counts.get('fail', 0)} fail, "
        f"{result.item_counts.get('blocked', 0)} blocked.",
        "",
        "### Android execution matrix",
        "",
        "| Run ID | Source commit | SDK | Device | Build | App | App APK | App APK SHA-256 | Test APK | Test APK SHA-256 | Artifact evidence | Tested at | Source |",
        "|---|---|---:|---|---|---|---|---|---|---|---|---|---|",
    ]

    for raw_run in android_runs:
        run = raw_run if isinstance(raw_run, dict) else {}
        device = (
            f"{_escape_table(run.get('device_type'))}: {_escape_table(run.get('model'))}, "
            f"Android {_escape_table(run.get('android_version'))}"
        )
        lines.append(
            f"| {_escape_table(run.get('run_id'))} | `{_escape_table(run.get('source_commit'))}` | "
            f"{_escape_table(run.get('sdk_int'))} | "
            f"{device} | {_escape_table(run.get('build_variant'))} | "
            f"{_escape_table(run.get('app_version'))} | {_escape_table(run.get('apk_name'))} | "
            f"`{_escape_table(run.get('apk_sha256'))}` | {_escape_table(run.get('test_apk_name'))} | "
            f"`{_escape_table(run.get('test_apk_sha256'))}` | "
            f"{_escape_table(run.get('artifact_evidence_ref'))} | "
            f"{_escape_table(run.get('tested_at'))} | "
            f"{_escape_table(run.get('apk_source'))} |"
        )

    lines.extend([
        "",
        "### API 26/27 CursorWindow compatibility evidence",
        "",
        f"- Status: `{_escape_table(cursorwindow.get('status'))}`",
        f"- Test: `{_escape_table(cursorwindow.get('test_name'))}`",
        f"- Failure evidence: {_escape_table(cursorwindow.get('evidence'))}",
        f"- Next step: {_escape_table(cursorwindow.get('next_step'))}",
        "",
        "| Run ID | Executed | Failures | Errors | Skipped | Payload bytes | Wire bytes | SDK evidence | SDK SHA-256 | Result evidence | Result SHA-256 |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|---|---|",
    ])
    cursor_results = cursorwindow.get("results") if isinstance(cursorwindow.get("results"), list) else []
    for raw_result in cursor_results:
        row = raw_result if isinstance(raw_result, dict) else {}
        lines.append(
            f"| {_escape_table(row.get('run_id'))} | {_escape_table(row.get('executed'))} | "
            f"{_escape_table(row.get('failures'))} | {_escape_table(row.get('errors'))} | "
            f"{_escape_table(row.get('skipped'))} | "
            f"{_escape_table(row.get('payload_bytes'))} | {_escape_table(row.get('wire_bytes'))} | "
            f"{_escape_table(row.get('sdk_evidence_ref'))} | "
            f"`{_escape_table(row.get('sdk_evidence_sha256'))}` | "
            f"{_escape_table(row.get('result_ref'))} | "
            f"`{_escape_table(row.get('result_sha256'))}` |"
        )
    lines.append("")

    for section in REQUIRED_SECTIONS:
        lines.extend([
            f"### {section.title}",
            "",
            "| Item | Status | Run IDs | Evidence | Next step |",
            "|---|---:|---|---|---|",
        ])
        for expected in section.items:
            item_data = _item(data, section.key, expected.key)
            status = _escape_table(str(item_data.get("status", "")).lower())
            run_ids = item_data.get("run_ids")
            run_ids_text = ", ".join(str(value) for value in run_ids) if isinstance(run_ids, list) else ""
            evidence = _escape_table(item_data.get("evidence"))
            next_step = _escape_table(item_data.get("next_step"))
            lines.append(
                f"| {expected.label} | {status} | {_escape_table(run_ids_text)} | "
                f"{evidence} | {next_step} |"
            )
        lines.append("")

    if result.errors:
        lines.extend(["### Validation errors", ""])
        lines.extend(f"- {_escape_table(error)}" for error in result.errors)
        lines.append("")
    if result.warnings:
        lines.extend(["### Incomplete rows", ""])
        lines.extend(f"- {_escape_table(warning)}" for warning in result.warnings)
        lines.append("")

    lines.extend([
        "### Scope note",
        "",
        scope_note(),
    ])
    return "\n".join(lines).rstrip() + "\n"


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is not allowed: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=_reject_duplicate_json_keys)


def _paths_identify_same_file(left: Path, right: Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    if not left.exists() or not right.exists():
        return False
    try:
        return left.samefile(right)
    except OSError:
        return False


def _write_text_file(path: Path, text: str, *, force: bool = False) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to write symlink output: {path}")
    if path.exists():
        if not path.is_file():
            raise ValueError(f"output must be a regular file: {path}")
        if not force:
            raise FileExistsError(f"output already exists; use --force to replace it: {path}")
    mode = "w" if force else "x"
    with path.open(mode, encoding="utf-8", newline="") as handle:
        handle.write(text)


def write_template(path: Path, template: dict[str, object], *, force: bool = False) -> None:
    _write_text_file(
        path,
        json.dumps(template, indent=2, sort_keys=True) + "\n",
        force=force,
    )


def _emit_json(data: object) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and render v1.6.0 manual QA evidence.")
    parser.add_argument("--version", default=DEFAULT_VERSION, choices=(DEFAULT_VERSION,))
    parser.add_argument("--issue", type=int, default=DEFAULT_ISSUE, choices=(DEFAULT_ISSUE,))
    parser.add_argument("--template", action="store_true", help="write a JSON evidence template to stdout")
    parser.add_argument("--write-template", type=Path, help="write a JSON evidence template to this path")
    parser.add_argument("--input", type=Path, help="validate and render this JSON evidence file")
    parser.add_argument("--output", type=Path, help="write rendered Markdown to this path instead of stdout")
    parser.add_argument("--json", action="store_true", help="emit validation JSON instead of Markdown")
    parser.add_argument("--no-fail", action="store_true", help="return exit code 0 even when evidence is incomplete")
    parser.add_argument("--force", action="store_true", help="replace an existing regular output file")
    args = parser.parse_args(list(argv) if argv is not None else None)

    selected_modes = sum(1 for enabled in (args.template, bool(args.write_template), bool(args.input)) if enabled)
    if selected_modes != 1:
        parser.error("choose exactly one of --template, --write-template, or --input")
    if args.output and not args.input:
        parser.error("--output requires --input")
    if args.output and args.json:
        parser.error("--output cannot be combined with --json")
    if args.force and not (args.write_template or args.output):
        parser.error("--force requires --write-template or --output")
    if args.input and args.output and _paths_identify_same_file(args.input, args.output):
        parser.error("--input and --output must not identify the same file")

    template = build_template(args.version)
    if args.template:
        _emit_json(template)
        return 0
    if args.write_template:
        try:
            write_template(args.write_template, template, force=args.force)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        return 0

    try:
        loaded = load_json(args.input)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    result = validate_evidence(loaded, expected_version=args.version)
    if args.json:
        _emit_json(result.as_dict())
    else:
        if not isinstance(loaded, dict):
            loaded = {}
        markdown = render_markdown(loaded, result, issue=args.issue)
        if args.output:
            try:
                _write_text_file(args.output, markdown, force=args.force)
            except (OSError, ValueError) as exc:
                parser.error(str(exc))
        else:
            print(markdown, end="")

    if args.no_fail:
        return 0
    return 0 if result.release_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
