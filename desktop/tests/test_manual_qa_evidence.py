"""Unit tests for the Issue #36 manual QA evidence helper."""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "manual_qa_evidence.py"
_spec = importlib.util.spec_from_file_location("manual_qa_evidence", _SCRIPT)
manual_qa_evidence = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = manual_qa_evidence
_spec.loader.exec_module(manual_qa_evidence)


def _valid_report():
    data = manual_qa_evidence.build_template()
    data["target_commit"] = "a" * 40
    data["tester"] = "Owner"
    data["tested_at"] = "2026-07-04T12:00:00Z"
    data["android_runs"] = [
        {
            "run_id": "api26-cursorwindow",
            "source_commit": "a" * 40,
            "sdk_int": 26,
            "android_version": "8.0",
            "device_type": "emulator",
            "model": "API 26 AVD",
            "build_variant": "debug",
            "app_version": "1.6.0",
            "apk_name": "app-debug.apk",
            "apk_source": "Exact target-commit debug build",
            "apk_sha256": "1" * 64,
            "test_apk_name": "app-debug-androidTest.apk",
            "test_apk_sha256": "8" * 64,
            "artifact_evidence_ref": "CI run 123 / debug artifact",
            "tested_at": "2026-07-04T10:00:00Z",
        },
        {
            "run_id": "api27-cursorwindow",
            "source_commit": "a" * 40,
            "sdk_int": 27,
            "android_version": "8.1",
            "device_type": "emulator",
            "model": "API 27 AVD",
            "build_variant": "debug",
            "app_version": "1.6.0",
            "apk_name": "app-debug.apk",
            "apk_source": "Exact target-commit debug build",
            "apk_sha256": "2" * 64,
            "test_apk_name": "app-debug-androidTest.apk",
            "test_apk_sha256": "9" * 64,
            "artifact_evidence_ref": "CI run 123 / debug artifact",
            "tested_at": "2026-07-04T10:30:00+00:00",
        },
        {
            "run_id": "signed-release-physical",
            "source_commit": "a" * 40,
            "sdk_int": 35,
            "android_version": "15",
            "device_type": "physical",
            "model": "Pixel 8",
            "build_variant": "release",
            "app_version": "1.6.0",
            "apk_name": "ClipVault-Android-v1.6.0-release-signed.apk",
            "apk_source": "Release artifact build run 123",
            "apk_sha256": "3" * 64,
            "test_apk_name": "",
            "test_apk_sha256": "",
            "artifact_evidence_ref": "Validated release artifact report 123",
            "tested_at": "2026-07-04T11:00:00Z",
        },
    ]
    compatibility = data["android_compatibility_qa"]["cursorwindow_large_payload"]
    compatibility["status"] = "pass"
    compatibility["next_step"] = ""
    compatibility["results"] = [
        {
            "run_id": run_id,
            "executed": 1,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "payload_bytes": manual_qa_evidence.CURSORWINDOW_MIN_PAYLOAD_BYTES + 1,
            "wire_bytes": 5_000_000,
            "sdk_evidence_ref": f"SDK evidence for {run_id}",
            "sdk_evidence_sha256": sdk_digest * 64,
            "result_ref": f"JUnit result for {run_id}",
            "result_sha256": result_digest * 64,
        }
        for run_id, sdk_digest, result_digest in (
            ("api26-cursorwindow", "4", "6"),
            ("api27-cursorwindow", "5", "7"),
        )
    ]
    data["desktop_environment"] = {
        "os": "Windows 11",
        "app_version": "1.6.0",
        "build_source": "Release artifact build run 123",
        "source_commit": "a" * 40,
    }
    for section in manual_qa_evidence.REQUIRED_SECTIONS:
        for item in section.items:
            item_data = {
                "status": "pass",
                "evidence": f"Observed {item.key}",
                "next_step": "",
                "notes": item.label,
            }
            if section.key in {"android_device_qa", "ime_privacy_qa", "sync_qa"}:
                item_data["run_ids"] = ["signed-release-physical"]
            data["sections"][section.key]["items"][item.key] = item_data
    return data


def test_template_contains_every_required_manual_qa_item():
    template = manual_qa_evidence.build_template()

    assert template["schema_version"] == 2
    assert template["version"] == "v1.6.0"
    assert template["scope_note"] == manual_qa_evidence.scope_note()
    assert [run["sdk_int"] for run in template["android_runs"][:2]] == [26, 27]
    assert template["android_runs"][0]["test_apk_name"] == "app-debug-androidTest.apk"
    assert template["android_runs"][2]["apk_name"] == "ClipVault-Android-v1.6.0-release-signed.apk"
    assert (
        template["android_compatibility_qa"]["cursorwindow_large_payload"]["test_name"]
        == manual_qa_evidence.CURSORWINDOW_TEST_NAME
    )
    for section in manual_qa_evidence.REQUIRED_SECTIONS:
        assert section.key in template["sections"]
        for item in section.items:
            item_data = template["sections"][section.key]["items"][item.key]
            assert item_data["status"] == "blocked"
            assert item_data["next_step"]


def test_helper_is_scoped_to_issue_36_v1_6_0():
    with pytest.raises(ValueError, match="only supports v1.6.0"):
        manual_qa_evidence.build_template("v1.7.0")

    result = manual_qa_evidence.validate_evidence(_valid_report(), expected_version="v1.7.0")
    assert result.release_ready is False
    assert "manual QA evidence helper only supports v1.6.0" in result.errors


def test_valid_all_pass_evidence_is_release_ready_and_renders_issue_comment():
    report = _valid_report()

    result = manual_qa_evidence.validate_evidence(report)
    markdown = manual_qa_evidence.render_markdown(report, result)

    assert result.ok is True
    assert result.release_ready is True
    assert result.item_counts == {"blocked": 0, "fail": 0, "pass": 18}
    assert "Manual QA evidence for Issue #36" in markdown
    assert "Status: **PASS (OWNER-ATTESTED)**" in markdown
    assert "Manual Android device QA" in markdown
    assert "Manual IME privacy QA" in markdown
    assert "Manual sync QA" in markdown
    assert "Manual Windows clipboard privacy QA" in markdown
    assert "API 26/27 CursorWindow compatibility evidence" in markdown
    assert "signed-release-physical" in markdown
    assert "does not replace signed artifact evidence" in markdown
    assert "Owner-attested inputs" in markdown
    assert result.as_dict()["evidence_assurance"] == "owner_attested_structural_validation"


def test_pass_rows_require_observed_evidence():
    report = _valid_report()
    report["sections"]["ime_privacy_qa"]["items"]["typed_text_not_persisted"]["evidence"] = ""

    result = manual_qa_evidence.validate_evidence(report)

    assert result.ok is False
    assert result.release_ready is False
    assert any("typed_text_not_persisted.evidence" in error for error in result.errors)


def test_blocked_rows_require_next_step_and_keep_release_incomplete():
    report = _valid_report()
    report["sections"]["windows_clipboard_privacy_qa"]["items"]["viewer_ignore"] = {
        "status": "blocked",
        "evidence": "",
        "next_step": "",
    }

    result = manual_qa_evidence.validate_evidence(report)

    assert result.ok is False
    assert result.release_ready is False
    assert any("viewer_ignore.next_step" in error for error in result.errors)


def test_fail_rows_require_next_step_and_keep_release_incomplete():
    report = _valid_report()
    report["sections"]["sync_qa"]["items"]["secret_private_isolation"] = {
        "status": "fail",
        "evidence": "Private item appeared in public sync.",
        "next_step": "",
    }

    result = manual_qa_evidence.validate_evidence(report)

    assert result.ok is False
    assert result.release_ready is False
    assert any("secret_private_isolation.next_step" in error for error in result.errors)


def test_missing_required_section_item_is_an_error():
    report = _valid_report()
    del report["sections"]["sync_qa"]["items"]["secret_private_isolation"]

    result = manual_qa_evidence.validate_evidence(report)

    assert result.ok is False
    assert any("secret_private_isolation is required" in error for error in result.errors)
    assert any("expected 18 QA items" in error for error in result.errors)


def test_metadata_must_pin_version_and_full_target_commit():
    report = _valid_report()
    report["version"] = "v1.5.10"
    report["target_commit"] = "abc123"

    result = manual_qa_evidence.validate_evidence(report)

    assert result.ok is False
    assert "version must be v1.6.0, got v1.5.10" in result.errors
    assert "target_commit must be a full 40-character hexadecimal commit SHA" in result.errors


def test_schema_v1_is_blocked_instead_of_silently_accepted():
    report = _valid_report()
    report.pop("schema_version")

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("schema_version must be 2" in error for error in result.errors)


def test_timestamps_and_app_versions_are_strict():
    report = _valid_report()
    report["tested_at"] = "2026-07-04 12:00:00"
    report["android_runs"][0]["tested_at"] = "yesterday"
    report["android_runs"][1]["app_version"] = "1.7.0"
    report["desktop_environment"]["app_version"] = "v1.6.0"
    report["desktop_environment"]["source_commit"] = "b" * 40

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("tested_at must include an explicit timezone" in error for error in result.errors)
    assert any("android_runs[0].tested_at must be an ISO-8601" in error for error in result.errors)
    assert any("android_runs[1].app_version must be 1.6.0" in error for error in result.errors)
    assert any("desktop_environment.app_version must be 1.6.0" in error for error in result.errors)
    assert "desktop_environment.source_commit must match target_commit" in result.errors


def test_failed_cursorwindow_status_requires_failure_evidence():
    report = _valid_report()
    compatibility = report["android_compatibility_qa"]["cursorwindow_large_payload"]
    compatibility["status"] = "fail"
    compatibility["evidence"] = ""
    compatibility["next_step"] = "Investigate the API 26 failure."

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("evidence must describe the failed API 26/27 execution" in error for error in result.errors)


def test_android_run_timestamp_cannot_be_later_than_report_timestamp():
    report = _valid_report()
    report["android_runs"][0]["tested_at"] = "2026-07-05T12:00:00Z"

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert "android run api26-cursorwindow tested_at must not be later than report tested_at" in result.errors


@pytest.mark.parametrize(
    ("mutate", "error_fragment"),
    [
        (
            lambda report: report["android_compatibility_qa"]["cursorwindow_large_payload"]["results"].pop(),
            "exactly one non-skipped passing run for API 26 and API 27",
        ),
        (
            lambda report: report["android_compatibility_qa"]["cursorwindow_large_payload"]["results"][0].update({"skipped": 1}),
            "skipped must be 0",
        ),
        (
            lambda report: report["android_compatibility_qa"]["cursorwindow_large_payload"]["results"][0].update({"executed": 0}),
            "executed must be 1",
        ),
        (
            lambda report: report["android_compatibility_qa"]["cursorwindow_large_payload"]["results"][0].update({"errors": 1}),
            "errors must be 0",
        ),
        (
            lambda report: report["android_compatibility_qa"]["cursorwindow_large_payload"]["results"][0].update({"payload_bytes": 4 * 1024 * 1024}),
            "payload_bytes must be greater than",
        ),
        (
            lambda report: report["android_compatibility_qa"]["cursorwindow_large_payload"]["results"][0].update({"wire_bytes": manual_qa_evidence.MAX_SYNC_PUSH_REQUEST_BYTES + 1}),
            "wire_bytes must be between",
        ),
        (
            lambda report: report["android_compatibility_qa"]["cursorwindow_large_payload"]["results"][0].update({"result_sha256": "abc123"}),
            "64-character hexadecimal SHA-256",
        ),
    ],
)
def test_cursorwindow_evidence_fails_closed(mutate, error_fragment):
    report = _valid_report()
    mutate(report)

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any(error_fragment in error for error in result.errors)


def test_cursorwindow_results_cannot_reuse_api26_evidence_for_api27():
    report = _valid_report()
    results = report["android_compatibility_qa"]["cursorwindow_large_payload"]["results"]
    for field in (
        "result_ref",
        "result_sha256",
        "sdk_evidence_ref",
        "sdk_evidence_sha256",
    ):
        results[1][field] = results[0][field]

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    for field in ("result_ref", "result_sha256", "sdk_evidence_ref", "sdk_evidence_sha256"):
        assert any(f"{field} must be unique" in error for error in result.errors)


def test_cursorwindow_result_requires_exact_test_and_debug_run():
    report = _valid_report()
    compatibility = report["android_compatibility_qa"]["cursorwindow_large_payload"]
    compatibility["test_name"] = "another.Test#method"
    report["android_runs"][0]["build_variant"] = "release"
    report["android_runs"][1]["android_version"] = "8.1.0-vendor"

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("test_name must be com.clipvault" in error for error in result.errors)
    assert any("must reference a debug instrumentation run" in error for error in result.errors)


def test_compatibility_run_binds_both_app_and_instrumentation_apks():
    report = _valid_report()
    report["android_runs"][0]["apk_name"] = "old-debug.apk"
    report["android_runs"][0]["test_apk_name"] = "wrong-androidTest.apk"
    report["android_runs"][1]["test_apk_sha256"] = "abc123"

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("must reference app-debug.apk" in error for error in result.errors)
    assert any("test_apk_name must be app-debug-androidTest.apk" in error for error in result.errors)
    assert any("test_apk_sha256 must be a 64-character" in error for error in result.errors)


def test_independent_apk_and_device_evidence_digests_cannot_be_reused():
    report = _valid_report()
    report["android_runs"][0]["test_apk_sha256"] = report["android_runs"][0]["apk_sha256"]
    report["android_runs"][2]["apk_sha256"] = report["android_runs"][1]["test_apk_sha256"]
    result_row = report["android_compatibility_qa"]["cursorwindow_large_payload"]["results"][0]
    result_row["sdk_evidence_ref"] = result_row["result_ref"]
    result_row["sdk_evidence_sha256"] = result_row["result_sha256"]

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("apk_sha256 and test_apk_sha256 must identify different APKs" in error for error in result.errors)
    assert any("final signed APK SHA-256 must differ" in error for error in result.errors)
    assert any("result_ref and sdk_evidence_ref must identify different" in error for error in result.errors)
    assert any("result_sha256 and sdk_evidence_sha256 must identify different" in error for error in result.errors)


def test_result_sdk_and_apk_evidence_cannot_change_roles_across_api_runs():
    report = _valid_report()
    results = report["android_compatibility_qa"]["cursorwindow_large_payload"]["results"]
    shared_ref = results[0]["result_ref"]
    shared_digest = results[0]["result_sha256"]
    results[1]["sdk_evidence_ref"] = shared_ref
    results[1]["sdk_evidence_sha256"] = shared_digest
    report["android_runs"][0]["apk_sha256"] = shared_digest

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("unique across all API 26 and API 27 result and SDK references" in error for error in result.errors)
    assert any("unique across all API 26 and API 27 result and SDK SHA-256" in error for error in result.errors)
    assert any("must not reuse an app or instrumentation APK SHA-256" in error for error in result.errors)


def test_sdk_int_is_authoritative_over_vendor_android_version_string():
    report = _valid_report()
    report["android_runs"][0]["android_version"] = "8.0.0-vendor-build"
    report["android_runs"][1]["android_version"] = "8.1.0-vendor-build"

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is True


def test_cursorwindow_counters_reject_booleans_and_missing_references():
    report = _valid_report()
    row = report["android_compatibility_qa"]["cursorwindow_large_payload"]["results"][0]
    row["executed"] = True
    row["wire_bytes"] = 0
    row["result_ref"] = ""
    row["sdk_evidence_ref"] = ""

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("executed must be an integer" in error for error in result.errors)
    assert any("wire_bytes must be between" in error for error in result.errors)
    assert any("result_ref must be a non-empty string" in error for error in result.errors)
    assert any("sdk_evidence_ref must be a non-empty string" in error for error in result.errors)


def test_duplicate_runs_and_wrong_sdk_are_rejected():
    report = _valid_report()
    report["android_runs"][1]["run_id"] = "api26-cursorwindow"
    report["android_runs"][0]["sdk_int"] = 28

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("duplicates api26-cursorwindow" in error for error in result.errors)
    assert any("API 26 or API 27" in error for error in result.errors)


def test_android_rows_must_reference_physical_final_signed_apk_run():
    report = _valid_report()
    row = report["sections"]["ime_privacy_qa"]["items"]["typed_text_not_persisted"]
    row["run_ids"] = ["api26-cursorwindow"]
    report["android_runs"][2]["apk_name"] = "app-release-unsigned.apk"

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("final_signed_android_run_id must reference a physical release run" in error for error in result.errors)
    assert any("must include the physical final signed APK run" in error for error in result.errors)


def test_final_signed_run_is_bound_to_target_commit_and_artifact_evidence():
    report = _valid_report()
    report["android_runs"][2]["source_commit"] = "b" * 40
    report["android_runs"][2]["artifact_evidence_ref"] = ""

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("source_commit must match target_commit" in error for error in result.errors)
    assert any("artifact_evidence_ref must reference validated release artifact evidence" in error for error in result.errors)
    assert any("final_signed_android_run_id must reference a physical release run" in error for error in result.errors)


def test_missing_runs_and_unknown_final_run_fail_closed():
    report = _valid_report()
    report["android_runs"] = []
    report["final_signed_android_run_id"] = "missing"

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert "android_runs must contain API 26, API 27, and final signed release runs" in result.errors
    assert "final_signed_android_run_id must reference android_runs" in result.errors


def test_final_run_id_cannot_point_to_debug_run():
    report = _valid_report()
    report["final_signed_android_run_id"] = "api26-cursorwindow"

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("final_signed_android_run_id must reference a physical release run" in error for error in result.errors)


def test_rows_must_reference_declared_final_run_not_an_alternate_qualifying_run():
    report = _valid_report()
    alternate = dict(report["android_runs"][2])
    alternate["run_id"] = "alternate-signed-release"
    report["android_runs"].append(alternate)
    row = report["sections"]["android_device_qa"]["items"]["pairing"]
    row["run_ids"] = ["alternate-signed-release"]

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("run_ids must include the physical final signed APK run" in error for error in result.errors)


def test_android_row_unknown_run_reference_is_rejected():
    report = _valid_report()
    report["sections"]["sync_qa"]["items"]["public_clips_memory_sync"]["run_ids"] = ["missing"]

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("does not reference android_runs" in error for error in result.errors)


def test_renderer_ignores_unknown_device_serial_and_payload_fields():
    report = _valid_report()
    report["android_runs"][0]["serial"] = "PRIVATE-SERIAL"
    report["android_runs"][0]["payload"] = "PRIVATE-PAYLOAD"
    result = manual_qa_evidence.validate_evidence(report)

    markdown = manual_qa_evidence.render_markdown(report, result)

    assert "PRIVATE-SERIAL" not in markdown
    assert "PRIVATE-PAYLOAD" not in markdown


def test_cursorwindow_helper_contract_matches_instrumented_test_source():
    root = Path(__file__).resolve().parents[2]
    source = (
        root
        / "android"
        / "app"
        / "src"
        / "androidTest"
        / "kotlin"
        / "com"
        / "clipvault"
        / "app"
        / "capture"
        / "CaptureTransactionTest.kt"
    ).read_text(encoding="utf-8")
    sync_worker = (
        root
        / "android"
        / "app"
        / "src"
        / "main"
        / "kotlin"
        / "com"
        / "clipvault"
        / "app"
        / "sync"
        / "SyncWorker.kt"
    ).read_text(encoding="utf-8")

    assert manual_qa_evidence.CURSORWINDOW_TEST_NAME.split("#", 1)[1] in source
    assert "@SdkSuppress(minSdkVersion = 26, maxSdkVersion = 27)" in source
    assert "payloadBytes > 4L * 1024 * 1024" in source
    assert "CLIPVAULT_CURSORWINDOW_EVIDENCE" in source
    assert "MAX_JSON_ESCAPED_BYTES_PER_CLIP_BYTE = 6" in sync_worker
    assert "MAX_SYNC_EVENT_ENVELOPE_BYTES = 64 * 1024" in sync_worker
    assert manual_qa_evidence.MAX_SYNC_PUSH_REQUEST_BYTES == 6 * 1024 * 1024 + 64 * 1024


def test_template_placeholders_are_not_valid_evidence():
    report = _valid_report()
    report["tester"] = "REPLACE_WITH_TESTER_NAME"
    report["android_runs"][2]["model"] = "REPLACE_WITH_DEVICE_MODEL"
    report["sections"]["android_device_qa"]["items"]["pairing"]["evidence"] = "REPLACE_WITH_OBSERVATION"

    result = manual_qa_evidence.validate_evidence(report)

    assert result.ok is False
    assert "tester must replace the template placeholder" in result.errors
    assert "android_runs[2].model must replace the template placeholder" in result.errors
    assert any("pairing.evidence" in error for error in result.errors)


def test_lowercase_template_placeholders_are_not_valid_evidence():
    report = _valid_report()
    report["tester"] = "replace_with_tester_name"
    report["sections"]["android_device_qa"]["items"]["pairing"]["evidence"] = (
        "replace_with_observation"
    )

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert "tester must replace the template placeholder" in result.errors
    assert any("pairing.evidence" in error for error in result.errors)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report["android_runs"][0].update({"apk_source": r"C:\\Users\\Owner\\apk"}),
        lambda report: report["android_runs"][2].update(
            {"artifact_evidence_ref": r"\\\\server\\private\\report.json"}
        ),
        lambda report: report["android_compatibility_qa"]["cursorwindow_large_payload"][
            "results"
        ][0].update({"result_ref": "file:///private/result.xml"}),
        lambda report: report["android_compatibility_qa"]["cursorwindow_large_payload"][
            "results"
        ][1].update({"sdk_evidence_ref": "/home/owner/sdk.txt"}),
        lambda report: report["desktop_environment"].update(
            {"build_source": r"C:\\Private\\ClipVault.exe"}
        ),
        lambda report: report["android_runs"][0].update(
            {"apk_source": r"built from C:\\Users\\Owner\\private\\app-debug.apk"}
        ),
    ],
)
def test_public_report_references_reject_absolute_local_paths(mutate):
    report = _valid_report()
    mutate(report)

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("absolute local paths and file URIs are not allowed" in error for error in result.errors)


def test_free_form_evidence_rejects_private_paths_and_escapes_html():
    report = _valid_report()
    pairing = report["sections"]["android_device_qa"]["items"]["pairing"]
    pairing["evidence"] = r"Observed path=C:\Users\Owner\PrivateVault\pairing.txt"
    report["sections"]["ime_privacy_qa"]["items"]["sensitive_field_entry"]["next_step"] = (
        r"Review path=\\server\private\notes.txt"
    )
    report["android_compatibility_qa"]["cursorwindow_large_payload"]["evidence"] = (
        "See path=/home/owner/private/result.txt"
    )
    report["sections"]["sync_qa"]["items"]["public_clips_memory_sync"]["evidence"] = (
        "<script>alert('x')</script> observed"
    )

    result = manual_qa_evidence.validate_evidence(report)
    markdown = manual_qa_evidence.render_markdown(report, result)

    assert result.release_ready is False
    assert sum("must not contain an absolute local/UNC path" in error for error in result.errors) >= 3
    assert "<script>" not in markdown
    assert "&lt;script&gt;" in markdown


def test_cli_writes_template_and_json_summary(tmp_path, capsys):
    template_path = tmp_path / "manual-qa.json"

    assert manual_qa_evidence.main(["--write-template", str(template_path)]) == 0
    loaded = json.loads(template_path.read_text(encoding="utf-8"))
    assert loaded["sections"]["android_device_qa"]["items"]["pairing"]["status"] == "blocked"

    valid_path = tmp_path / "valid.json"
    valid_path.write_text(json.dumps(_valid_report()), encoding="utf-8")
    assert manual_qa_evidence.main(["--input", str(valid_path), "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["release_ready"] is True
    assert output["item_counts"]["pass"] == 18

    markdown_path = tmp_path / "manual-qa-comment.md"
    assert manual_qa_evidence.main(["--input", str(valid_path), "--output", str(markdown_path)]) == 0
    assert "Manual QA evidence for Issue #36" in markdown_path.read_text(encoding="utf-8")


def test_cli_refuses_to_overwrite_template_without_force(tmp_path):
    template_path = tmp_path / "manual-qa.json"
    template_path.write_bytes(b"OWNER EVIDENCE")

    with pytest.raises(SystemExit):
        manual_qa_evidence.main(["--write-template", str(template_path)])
    assert template_path.read_bytes() == b"OWNER EVIDENCE"

    assert manual_qa_evidence.main(
        ["--write-template", str(template_path), "--force"]
    ) == 0
    assert json.loads(template_path.read_text(encoding="utf-8"))["schema_version"] == 2


def test_cli_refuses_to_overwrite_rendered_output_without_force(tmp_path):
    input_path = tmp_path / "manual-qa.json"
    input_path.write_text(json.dumps(_valid_report()), encoding="utf-8")
    output_path = tmp_path / "comment.md"
    output_path.write_bytes(b"OWNER COMMENT")

    with pytest.raises(SystemExit):
        manual_qa_evidence.main(["--input", str(input_path), "--output", str(output_path)])
    assert output_path.read_bytes() == b"OWNER COMMENT"

    assert manual_qa_evidence.main(
        ["--input", str(input_path), "--output", str(output_path), "--force"]
    ) == 0
    assert "Status: **PASS (OWNER-ATTESTED)**" in output_path.read_text(encoding="utf-8")


def test_cli_never_allows_input_to_be_overwritten_by_output(tmp_path):
    evidence_path = tmp_path / "manual-qa.json"
    original = json.dumps(_valid_report()).encode()
    evidence_path.write_bytes(original)

    with pytest.raises(SystemExit):
        manual_qa_evidence.main(
            [
                "--input",
                str(evidence_path),
                "--output",
                str(evidence_path),
                "--force",
            ]
        )
    assert evidence_path.read_bytes() == original


def test_cli_never_allows_hardlink_alias_to_overwrite_input(tmp_path):
    evidence_path = tmp_path / "manual-qa.json"
    original = json.dumps(_valid_report()).encode()
    evidence_path.write_bytes(original)
    alias_path = tmp_path / "comment.md"
    os.link(evidence_path, alias_path)

    with pytest.raises(SystemExit):
        manual_qa_evidence.main(
            [
                "--input",
                str(evidence_path),
                "--output",
                str(alias_path),
                "--force",
            ]
        )
    assert evidence_path.read_bytes() == original
    assert alias_path.read_bytes() == original


def test_cli_rejects_duplicate_json_keys_at_any_depth(tmp_path):
    evidence_path = tmp_path / "duplicate.json"
    raw = json.dumps(_valid_report())
    raw = raw.replace('"status": "pass"', '"status": "fail", "status": "pass"', 1)
    evidence_path.write_text(raw, encoding="utf-8")

    with pytest.raises(SystemExit):
        manual_qa_evidence.main(["--input", str(evidence_path), "--json"])


def test_renderer_escapes_validation_error_newlines():
    report = _valid_report()
    report["android_runs"][0]["run_id"] = "bad\n@owner"

    result = manual_qa_evidence.validate_evidence(report)
    markdown = manual_qa_evidence.render_markdown(report, result)

    assert "\n@owner" not in markdown
    assert "bad / @owner" in markdown


def test_cli_rejects_directory_and_symlink_outputs_even_with_force(tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(SystemExit):
        manual_qa_evidence.main(["--write-template", str(directory), "--force"])

    target = tmp_path / "target.json"
    target.write_text("target", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    with pytest.raises(SystemExit):
        manual_qa_evidence.main(["--write-template", str(link), "--force"])
    assert target.read_text(encoding="utf-8") == "target"


def test_cli_returns_nonzero_for_incomplete_evidence_unless_no_fail(tmp_path):
    report = _valid_report()
    report["sections"]["android_device_qa"]["items"]["pairing"] = {
        "status": "blocked",
        "evidence": "",
        "next_step": "Run on a real Android device.",
    }
    path = tmp_path / "blocked.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    assert manual_qa_evidence.main(["--input", str(path), "--json"]) == 2
    assert manual_qa_evidence.main(["--input", str(path), "--json", "--no-fail"]) == 0


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--template", "--input", "x.json"],
        ["--template", "--write-template", "x.json"],
        ["--template", "--output", "x.md"],
        ["--input", "x.json", "--json", "--output", "x.md"],
        ["--template", "--force"],
        ["--input", "x.json", "--force"],
        ["--template", "--version", "v1.7.0"],
        ["--template", "--issue", "82"],
    ],
)
def test_cli_requires_exactly_one_mode(argv):
    with pytest.raises(SystemExit):
        manual_qa_evidence.main(argv)
