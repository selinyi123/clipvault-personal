"""Unit tests for the Issue #36 manual QA evidence helper."""

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "manual_qa_evidence.py"
_spec = importlib.util.spec_from_file_location("manual_qa_evidence", _SCRIPT)
manual_qa_evidence = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = manual_qa_evidence
_spec.loader.exec_module(manual_qa_evidence)

_ARTIFACT_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "release_artifact_evidence.py"
_artifact_spec = importlib.util.spec_from_file_location(
    "release_artifact_evidence_for_manual_qa_tests", _ARTIFACT_SCRIPT
)
release_artifact_evidence = importlib.util.module_from_spec(_artifact_spec)
sys.modules[_artifact_spec.name] = release_artifact_evidence
_artifact_spec.loader.exec_module(release_artifact_evidence)


def _final_draft_artifact_report():
    target_commit = "a" * 40
    run_id = 123
    run_url = (
        "https://github.com/selinyi123/clipvault-personal/actions/runs/123"
    )
    artifacts = []
    for index, spec in enumerate(
        sorted(
            release_artifact_evidence._asset_specs("v1.6.0"),
            key=lambda value: value.release_name,
        ),
        start=1,
    ):
        digest = "3" * 64 if spec.role == "android_signed_apk" else f"{index + 10:064x}"
        artifacts.append({
            "role": spec.role,
            "workflow_bundle": spec.workflow_bundle,
            "workflow_name": spec.workflow_name,
            "release_name": spec.release_name,
            "size_bytes": 1_000 + index,
            "sha256": digest,
            "attestation_verified": True,
            "matching_invocation_count": 1,
            "release_asset_id": 200 + index,
        })
    report = {
        "schema_version": 1,
        "evidence_type": "clipvault.issue36.final_draft_artifacts",
        "artifact_gate_status": "snapshot_verified_live_revalidation_required",
        "repo": "selinyi123/clipvault-personal",
        "issue": 36,
        "version": "v1.6.0",
        "branch": "main",
        "target_commit": target_commit,
        "workflow_run": {
            "id": run_id,
            "url": run_url,
            "attempt": 1,
            "workflow": "Release artifact build",
            "path": ".github/workflows/release.yml",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": target_commit,
            "display_title": "Release artifacts v1.6.0 from main draft=true",
        },
        "workflow_artifacts": [
            {
                "id": 101,
                "name": "clipvault-android-signed-release-artifacts",
                "size_bytes": 2_000,
                "api_archive_sha256": "4" * 64,
            },
            {
                "id": 102,
                "name": "clipvault-windows-release-artifacts",
                "size_bytes": 3_000,
                "api_archive_sha256": "5" * 64,
            },
        ],
        "draft_release": {
            "id": 77,
            "url": (
                "https://github.com/selinyi123/clipvault-personal/"
                "releases/tag/untagged-77"
            ),
            "tag_name": "v1.6.0",
            "name": "ClipVault Personal v1.6.0",
            "is_draft": True,
            "is_prerelease": False,
            "target_commitish": target_commit,
        },
        "release_tag": {
            "ref": "refs/tags/v1.6.0",
            "state": "absent",
            "commit_sha": None,
        },
        "android_signer": {
            "expected_cert_sha256": "ab" * 32,
            "observed_cert_sha256": "ab" * 32,
            "signer_count": 1,
            "apksigner_verified": True,
            "trust_anchor_source": (
                "github_release_environment_variable_and_owner_input_match"
            ),
            "release_environment": "release",
            "release_environment_variable": "ANDROID_RELEASE_CERT_SHA256",
        },
        "artifacts": artifacts,
    }
    report["artifact_binding_sha256"] = (
        release_artifact_evidence._compute_binding_sha256(report)
    )
    return report


def _bind_report_to_final_draft(report, artifact_report, *, evidence_ref="Final draft report 123"):
    projection = (
        release_artifact_evidence.build_final_draft_manual_qa_binding_projection(
            artifact_report
        )
    )
    report["release_artifact_binding"] = json.loads(json.dumps(projection))
    report["release_artifact_binding"]["evidence_ref"] = evidence_ref
    final_run = report["android_runs"][2]
    final_run["artifact_evidence_ref"] = evidence_ref
    final_run["apk_name"] = projection["android_signed_apk"]["name"]
    final_run["apk_sha256"] = projection["android_signed_apk"]["sha256"]
    return report


def test_final_draft_fixture_has_fixed_inventory_binding_and_projection():
    artifact_report = _final_draft_artifact_report()
    inventory = {
        (row["role"], row["workflow_bundle"], row["release_name"])
        for row in artifact_report["artifacts"]
    }
    assert inventory == {
        (
            "windows_portable",
            "clipvault-windows-release-artifacts",
            "ClipVault-Desktop-v1.6.0-portable.exe",
        ),
        (
            "windows_installer",
            "clipvault-windows-release-artifacts",
            "ClipVault-Setup-v1.6.0.exe",
        ),
        (
            "windows_lgpl_relink_kit",
            "clipvault-windows-release-artifacts",
            "ClipVault-v1.6.0-LGPL-relink-kit.zip",
        ),
        (
            "windows_checksums",
            "clipvault-windows-release-artifacts",
            "windows-SHA256SUMS.txt",
        ),
        (
            "windows_manifest",
            "clipvault-windows-release-artifacts",
            "windows-RELEASE_MANIFEST.json",
        ),
        (
            "android_signed_apk",
            "clipvault-android-signed-release-artifacts",
            "ClipVault-Android-v1.6.0-release-signed.apk",
        ),
        (
            "android_apksigner_evidence",
            "clipvault-android-signed-release-artifacts",
            "ANDROID_APKSIGNER_VERIFY.txt",
        ),
        (
            "android_checksums",
            "clipvault-android-signed-release-artifacts",
            "android-SHA256SUMS.txt",
        ),
        (
            "android_manifest",
            "clipvault-android-signed-release-artifacts",
            "android-RELEASE_MANIFEST.json",
        ),
    }
    assert artifact_report["artifact_binding_sha256"] == (
        "1055fa600a019e18d3888d72c01e83df8037a9ca197af09f1849f1260258cbd1"
    )
    assert release_artifact_evidence.build_final_draft_manual_qa_binding_projection(
        artifact_report
    ) == {
        "artifact_evidence_type": "clipvault.issue36.final_draft_artifacts",
        "artifact_binding_sha256": (
            "1055fa600a019e18d3888d72c01e83df8037a9ca197af09f1849f1260258cbd1"
        ),
        "target_commit": "a" * 40,
        "workflow_run": {
            "id": 123,
            "attempt": 1,
            "url": (
                "https://github.com/selinyi123/clipvault-personal/actions/runs/123"
            ),
        },
        "draft_release": {
            "id": 77,
            "url": (
                "https://github.com/selinyi123/clipvault-personal/"
                "releases/tag/untagged-77"
            ),
            "tag_name": "v1.6.0",
        },
        "android_signed_apk": {
            "name": "ClipVault-Android-v1.6.0-release-signed.apk",
            "sha256": "3" * 64,
        },
    }


def _valid_report():
    data = manual_qa_evidence.build_template()
    data.pop("release_artifact_binding")
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
            if section.key in {
                "android_device_qa",
                "android_signing_reset_qa",
                "ime_privacy_qa",
                "sync_qa",
            }:
                item_data["run_ids"] = ["signed-release-physical"]
            if item.key == "re_pair_outbox_high_water":
                item_data["instrumented_test_class"] = (
                    manual_qa_evidence.OUTBOX_BASE_TEST_CLASS
                )
                item_data["instrumented_results"] = [
                    {
                        "run_id": run_id,
                        "executed": 4,
                        "failures": 0,
                        "errors": 0,
                        "skipped": 0,
                        "result_ref": f"Outbox baseline JUnit for {run_id}",
                        "result_sha256": digest * 64,
                    }
                    for run_id, digest in (
                        ("api26-cursorwindow", "a"),
                        ("api27-cursorwindow", "b"),
                    )
                ]
            data["sections"][section.key]["items"][item.key] = item_data
    return data


def _valid_schema_v3_report():
    data = _valid_report()
    data["schema_version"] = manual_qa_evidence.LEGACY_V3_SCHEMA_VERSION
    del data["sections"]["android_signing_reset_qa"]
    return data


def test_template_contains_every_required_manual_qa_item():
    template = manual_qa_evidence.build_template()

    assert template["schema_version"] == 4
    assert template["version"] == "v1.6.0"
    assert template["scope_note"] == manual_qa_evidence.scope_note()
    assert template["release_artifact_binding"]["artifact_evidence_type"] == (
        "clipvault.issue36.final_draft_artifacts"
    )
    assert [run["sdk_int"] for run in template["android_runs"][:2]] == [26, 27]
    assert template["android_runs"][0]["test_apk_name"] == "app-debug-androidTest.apk"
    assert template["android_runs"][2]["apk_name"] == "ClipVault-Android-v1.6.0-release-signed.apk"
    assert "one-time pairing code" in template["sections"]["android_device_qa"]["items"]["pairing"]["notes"]
    assert "Desktop -> Android" in template["sections"]["sync_qa"]["items"]["public_clips_memory_sync"]["notes"]
    signing_reset_items = template["sections"]["android_signing_reset_qa"]["items"]
    assert tuple(signing_reset_items) == (
        "dual_backup_verified",
        "old_outbox_barrier_drained",
        "quarantine_decision",
        "zero_peer_reseed",
        "update_incompatible",
        "fresh_install",
        "reseed_delivery_verified",
    )
    assert all(
        row["run_ids"] == ["signed-release-physical"]
        for row in signing_reset_items.values()
    )
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
    high_water = template["sections"]["sync_qa"]["items"][
        "re_pair_outbox_high_water"
    ]
    assert high_water["instrumented_test_class"] == manual_qa_evidence.OUTBOX_BASE_TEST_CLASS
    assert [row["run_id"] for row in high_water["instrumented_results"]] == [
        "api26-cursorwindow",
        "api27-cursorwindow",
    ]


def test_helper_is_scoped_to_issue_36_v1_6_0():
    with pytest.raises(ValueError, match="only supports v1.6.0"):
        manual_qa_evidence.build_template("v1.7.0")

    result = manual_qa_evidence.validate_evidence(_valid_report(), expected_version="v1.7.0")
    assert result.release_ready is False
    assert "manual QA evidence helper only supports v1.6.0" in result.errors


def test_legacy_all_pass_evidence_is_compatible_but_remains_release_blocked():
    report = _valid_schema_v3_report()

    result = manual_qa_evidence.validate_evidence(report)
    markdown = manual_qa_evidence.render_markdown(report, result)

    assert result.ok is True
    assert result.structurally_complete is True
    assert result.release_ready is False
    assert result.final_draft_binding_assurance == "not_present_legacy_compatibility"
    assert result.item_counts == {"blocked": 0, "fail": 0, "pass": 19}
    assert any("schema-v3 evidence is accepted only" in warning for warning in result.warnings)
    assert "Manual QA evidence for Issue #36" in markdown
    assert "Status: **BLOCKED**" in markdown
    assert "final_draft_binding_assurance=not_present_legacy_compatibility" in markdown
    assert "does not qualify as final Issue #36 release-gate evidence" in markdown
    assert "Manual Android device QA" in markdown
    assert "Manual IME privacy QA" in markdown
    assert "Manual sync QA" in markdown
    assert "Manual Windows clipboard privacy QA" in markdown
    assert "API 26/27 CursorWindow compatibility evidence" in markdown
    assert "API 26/27 Android outbox baseline evidence" in markdown
    assert manual_qa_evidence.OUTBOX_BASE_TEST_CLASS in markdown
    assert "Outbox baseline JUnit for api26-cursorwindow" in markdown
    assert "signed-release-physical" in markdown
    assert "does not replace signed artifact evidence" in markdown
    assert "Owner-attested inputs" in markdown


def test_schema_v3_all_pass_with_verified_binding_still_cannot_be_release_ready():
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_schema_v3_report(), artifact_report)

    result = manual_qa_evidence.validate_evidence(
        report,
        final_draft_artifact_evidence=artifact_report,
        require_final_draft_binding=True,
    )

    assert result.ok is True
    assert result.structurally_complete is True
    assert result.release_ready is False
    assert result.final_draft_binding_assurance == "verified_external_snapshot"
    assert result.item_counts == {"blocked": 0, "fail": 0, "pass": 19}
    assert any("schema-v3 evidence is accepted only" in warning for warning in result.warnings)


def test_schema_v2_is_readable_but_cannot_satisfy_current_release_gate():
    report = _valid_report()
    report["schema_version"] = 2
    del report["sections"]["sync_qa"]["items"]["re_pair_outbox_high_water"]
    artifact_report = _final_draft_artifact_report()
    _bind_report_to_final_draft(report, artifact_report)

    result = manual_qa_evidence.validate_evidence(
        report,
        final_draft_artifact_evidence=artifact_report,
        require_final_draft_binding=True,
    )
    markdown = manual_qa_evidence.render_markdown(report, result)

    assert result.ok is True
    assert result.structurally_complete is True
    assert result.release_ready is False
    assert result.final_draft_binding_assurance == "verified_external_snapshot"
    assert result.item_counts == {"blocked": 0, "fail": 0, "pass": 18}
    assert any("schema-v2 evidence is accepted only" in warning for warning in result.warnings)
    assert "re-pairing preserves" not in markdown


@pytest.mark.parametrize("invalid_schema", [4.0, 3.0, 2.0, True])
def test_non_integer_schema_version_cannot_be_release_ready(invalid_schema):
    report = _valid_report()
    report["schema_version"] = invalid_schema
    artifact_report = _final_draft_artifact_report()
    _bind_report_to_final_draft(report, artifact_report)

    result = manual_qa_evidence.validate_evidence(
        report,
        final_draft_artifact_evidence=artifact_report,
        require_final_draft_binding=True,
    )

    assert result.ok is False
    assert result.structurally_complete is False
    assert result.release_ready is False
    assert any("schema_version must be 4, 3, or 2" in error for error in result.errors)
    assert result.as_dict()["evidence_assurance"] == "owner_attested_structural_validation"


def test_strict_binding_accepts_exact_final_draft_snapshot_and_renders_identity():
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)

    result = manual_qa_evidence.validate_evidence(
        report,
        final_draft_artifact_evidence=artifact_report,
        require_final_draft_binding=True,
    )
    markdown = manual_qa_evidence.render_markdown(report, result)

    assert result.release_ready is True
    assert result.structurally_complete is True
    assert result.final_draft_binding_assurance == "verified_external_snapshot"
    assert "final_draft_binding_assurance=verified_external_snapshot" in markdown
    assert "Final draft artifact binding" in markdown
    assert artifact_report["artifact_binding_sha256"] in markdown
    assert artifact_report["workflow_run"]["url"] in markdown
    assert artifact_report["draft_release"]["url"] in markdown
    assert "ClipVault-Android-v1.6.0-release-signed.apk" in markdown
    assert "Final draft report 123" in markdown


def test_verified_binding_assurance_survives_an_unfinished_manual_qa_row():
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    report["sections"]["android_device_qa"]["items"]["pairing"] = {
        "status": "blocked",
        "evidence": "",
        "run_ids": ["signed-release-physical"],
        "next_step": "Complete physical pairing QA.",
    }

    result = manual_qa_evidence.validate_evidence(
        report,
        final_draft_artifact_evidence=artifact_report,
        require_final_draft_binding=True,
    )
    markdown = manual_qa_evidence.render_markdown(report, result)

    assert result.release_ready is False
    assert result.final_draft_binding_assurance == "verified_external_snapshot"
    assert "verified against the supplied final-draft snapshot (offline)" in markdown


def test_strict_binding_is_required_but_schema_v2_without_it_remains_compatible():
    artifact_report = _final_draft_artifact_report()
    report = _valid_report()
    unverified_bound_report = _bind_report_to_final_draft(
        _valid_report(), artifact_report
    )

    legacy = manual_qa_evidence.validate_evidence(report)
    unverified = manual_qa_evidence.validate_evidence(unverified_bound_report)
    strict = manual_qa_evidence.validate_evidence(
        report,
        final_draft_artifact_evidence=artifact_report,
        require_final_draft_binding=True,
    )

    assert legacy.ok is True
    assert legacy.release_ready is False
    assert legacy.final_draft_binding_assurance == "not_present_legacy_compatibility"
    assert unverified.release_ready is False
    assert unverified.final_draft_binding_assurance == "unverified_or_invalid"
    assert any(
        "not accepted without an externally supplied" in error
        for error in unverified.errors
    )
    assert "not verified; report remains blocked" in manual_qa_evidence.render_markdown(
        unverified_bound_report, unverified
    )
    assert strict.release_ready is False
    assert any("release_artifact_binding is required" in error for error in strict.errors)


@pytest.mark.parametrize(
    ("mutate", "error_fragment"),
    [
        (
            lambda binding: binding.__setitem__("artifact_binding_sha256", "f" * 64),
            "artifact_binding_sha256 must match",
        ),
        (
            lambda binding: binding.__setitem__("target_commit", "b" * 40),
            "target_commit must match",
        ),
        (
            lambda binding: binding["workflow_run"].__setitem__("id", 124),
            "workflow_run.id must match",
        ),
        (
            lambda binding: binding["workflow_run"].__setitem__("attempt", 2),
            "workflow_run.attempt must match",
        ),
        (
            lambda binding: binding["workflow_run"].__setitem__(
                "url",
                "https://github.com/selinyi123/clipvault-personal/actions/runs/124",
            ),
            "workflow_run.url must match",
        ),
        (
            lambda binding: binding["draft_release"].__setitem__("id", 78),
            "draft_release.id must match",
        ),
        (
            lambda binding: binding["draft_release"].__setitem__(
                "url",
                "https://github.com/selinyi123/clipvault-personal/releases/tag/untagged-78",
            ),
            "draft_release.url must match",
        ),
        (
            lambda binding: binding["draft_release"].__setitem__("tag_name", "v1.5.10"),
            "draft_release.tag_name",
        ),
        (
            lambda binding: binding["android_signed_apk"].__setitem__(
                "name", "renamed.apk"
            ),
            "android_signed_apk.name",
        ),
        (
            lambda binding: binding["android_signed_apk"].__setitem__(
                "sha256", "f" * 64
            ),
            "android_signed_apk.sha256",
        ),
    ],
)
def test_strict_manual_binding_rejects_every_identity_mismatch(mutate, error_fragment):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    mutate(report["release_artifact_binding"])

    result = manual_qa_evidence.validate_evidence(
        report,
        final_draft_artifact_evidence=artifact_report,
        require_final_draft_binding=True,
    )

    assert result.release_ready is False
    assert any(error_fragment in error for error in result.errors)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evidence: evidence["workflow_run"].__setitem__("conclusion", "failure"),
        lambda evidence: evidence["workflow_run"].__setitem__("head_sha", "b" * 40),
        lambda evidence: evidence["release_tag"].__setitem__("state", "unknown"),
        lambda evidence: evidence["android_signer"].__setitem__(
            "observed_cert_sha256", "cd" * 32
        ),
        lambda evidence: evidence["artifacts"][0].__setitem__(
            "attestation_verified", False
        ),
        lambda evidence: evidence["artifacts"][0].__setitem__(
            "matching_invocation_count", 0
        ),
        lambda evidence: evidence["workflow_artifacts"][1].__setitem__("id", 101),
        lambda evidence: evidence["artifacts"][1].__setitem__(
            "release_asset_id", evidence["artifacts"][0]["release_asset_id"]
        ),
        lambda evidence: evidence["workflow_artifacts"].reverse(),
        lambda evidence: evidence["artifacts"].reverse(),
        lambda evidence: evidence["workflow_run"].__setitem__("id", True),
        lambda evidence: evidence["draft_release"].__setitem__("id", True),
        lambda evidence: next(
            row
            for row in evidence["artifacts"]
            if row["role"] == "android_signed_apk"
        ).__setitem__("role", "windows_portable"),
        lambda evidence: evidence.__setitem__(
            "target_commit", f" {evidence['target_commit']}"
        ),
    ],
)
def test_strict_binding_rejects_noncanonical_or_unverified_artifact_snapshot(mutate):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    mutate(artifact_report)
    artifact_report["artifact_binding_sha256"] = (
        release_artifact_evidence._compute_binding_sha256(artifact_report)
    )

    result = manual_qa_evidence.validate_evidence(
        report,
        final_draft_artifact_evidence=artifact_report,
        require_final_draft_binding=True,
    )

    assert result.release_ready is False
    assert any("final_draft_artifact_evidence is invalid" in error for error in result.errors)


def test_strict_binding_rejects_tampered_projection_with_stale_claimed_digest():
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    artifact_report["artifacts"][0]["sha256"] = "f" * 64

    result = manual_qa_evidence.validate_evidence(
        report,
        final_draft_artifact_evidence=artifact_report,
        require_final_draft_binding=True,
    )

    assert result.release_ready is False
    assert any(
        "final-draft artifact binding does not match report contents" in error
        for error in result.errors
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("apk_name", "renamed.apk"),
        ("apk_sha256", "f" * 64),
        ("artifact_evidence_ref", "another final draft report"),
    ],
)
def test_strict_binding_rejects_final_android_run_diverging_from_binding(field, value):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    report["android_runs"][2][field] = value

    result = manual_qa_evidence.validate_evidence(
        report,
        final_draft_artifact_evidence=artifact_report,
        require_final_draft_binding=True,
    )

    assert result.release_ready is False
    assert any(f"final signed android run {field}" in error for error in result.errors)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda binding: binding.__setitem__("artifact_evidence_type", "wrong"),
        lambda binding: binding["workflow_run"].__setitem__("id", True),
        lambda binding: binding["draft_release"].__setitem__("id", True),
        lambda binding: binding.__setitem__(
            "artifact_binding_sha256", f" {binding['artifact_binding_sha256']}"
        ),
        lambda binding: binding.__setitem__(
            "target_commit", binding["target_commit"].upper()
        ),
        lambda binding: binding["workflow_run"].__setitem__(
            "url", f" {binding['workflow_run']['url']}"
        ),
        lambda binding: binding.pop("android_signed_apk"),
    ],
)
def test_strict_manual_binding_requires_canonical_shape_and_values(mutate):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    mutate(report["release_artifact_binding"])

    result = manual_qa_evidence.validate_evidence(
        report,
        final_draft_artifact_evidence=artifact_report,
        require_final_draft_binding=True,
    )

    assert result.release_ready is False


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
    assert any("expected 26 QA items" in error for error in result.errors)


@pytest.mark.parametrize(
    "item_key",
    [
        "dual_backup_verified",
        "old_outbox_barrier_drained",
        "quarantine_decision",
        "zero_peer_reseed",
        "update_incompatible",
        "fresh_install",
        "reseed_delivery_verified",
    ],
)
def test_schema_v4_requires_every_android_signing_reset_row(item_key):
    report = _valid_report()
    del report["sections"]["android_signing_reset_qa"]["items"][item_key]

    result = manual_qa_evidence.validate_evidence(report)

    assert result.ok is False
    assert result.structurally_complete is False
    assert result.release_ready is False
    assert any(
        f"sections.android_signing_reset_qa.items.{item_key} is required" in error
        for error in result.errors
    )
    assert any("expected 26 QA items" in error for error in result.errors)


def test_android_signing_reset_rows_require_evidence_and_final_physical_run():
    report = _valid_report()
    row = report["sections"]["android_signing_reset_qa"]["items"][
        "dual_backup_verified"
    ]
    row["evidence"] = ""
    row["run_ids"] = ["api26-cursorwindow"]

    result = manual_qa_evidence.validate_evidence(report)

    assert result.release_ready is False
    assert any("dual_backup_verified.evidence" in error for error in result.errors)
    assert any(
        "dual_backup_verified.run_ids must include the physical final signed APK run"
        in error
        for error in result.errors
    )


def test_re_pair_outbox_high_water_is_required_release_evidence():
    report = _valid_report()
    del report["sections"]["sync_qa"]["items"]["re_pair_outbox_high_water"]

    result = manual_qa_evidence.validate_evidence(report)

    assert result.ok is False
    assert result.release_ready is False
    assert any("re_pair_outbox_high_water is required" in error for error in result.errors)
    assert any("expected 26 QA items" in error for error in result.errors)


def test_re_pair_outbox_high_water_requires_executed_api26_and_api27_results():
    report = _valid_report()
    item = report["sections"]["sync_qa"]["items"]["re_pair_outbox_high_water"]
    item["instrumented_results"][0]["executed"] = 0
    item["instrumented_results"][1]["result_ref"] = ""

    result = manual_qa_evidence.validate_evidence(report)

    assert result.ok is False
    assert result.release_ready is False
    assert any("instrumented_results[0].executed must be 4" in error for error in result.errors)
    assert any("instrumented_results[1].result_ref" in error for error in result.errors)


@pytest.mark.parametrize(
    "reuse_kind",
    [
        "cursorwindow",
        "test_apk",
        "run_apk_name",
        "run_test_apk_name",
        "run_apk_source",
        "run_artifact_ref",
    ],
)
def test_re_pair_outbox_evidence_cannot_reuse_other_evidence(reuse_kind):
    report = _valid_report()
    artifact_report = _final_draft_artifact_report()
    _bind_report_to_final_draft(report, artifact_report)
    high_water_result = report["sections"]["sync_qa"]["items"][
        "re_pair_outbox_high_water"
    ]["instrumented_results"][0]
    if reuse_kind == "cursorwindow":
        cursor_result = report["android_compatibility_qa"][
            "cursorwindow_large_payload"
        ]["results"][0]
        high_water_result["result_ref"] = cursor_result["result_ref"]
        high_water_result["result_sha256"] = cursor_result["result_sha256"]
    elif reuse_kind == "test_apk":
        high_water_result["result_sha256"] = report["android_runs"][0][
            "test_apk_sha256"
        ]
    elif reuse_kind == "run_apk_name":
        high_water_result["result_ref"] = report["android_runs"][0]["apk_name"]
    elif reuse_kind == "run_test_apk_name":
        high_water_result["result_ref"] = report["android_runs"][0][
            "test_apk_name"
        ]
    elif reuse_kind == "run_apk_source":
        high_water_result["result_ref"] = report["android_runs"][0]["apk_source"]
    else:
        high_water_result["result_ref"] = report["android_runs"][0][
            "artifact_evidence_ref"
        ]

    result = manual_qa_evidence.validate_evidence(
        report,
        final_draft_artifact_evidence=artifact_report,
        require_final_draft_binding=True,
    )

    assert result.ok is False
    assert result.release_ready is False
    if reuse_kind in {
        "cursorwindow",
        "run_apk_name",
        "run_test_apk_name",
        "run_apk_source",
        "run_artifact_ref",
    }:
        assert any("result_ref must differ" in error for error in result.errors)
    if reuse_kind in {"cursorwindow", "test_apk"}:
        assert any("result_sha256 must differ" in error for error in result.errors)


def test_schema_v2_rejects_schema_v3_only_item_in_frozen_shape():
    report = _valid_report()
    report["schema_version"] = 2

    result = manual_qa_evidence.validate_evidence(report)

    assert result.ok is False
    assert result.structurally_complete is False
    assert result.release_ready is False
    assert any(
        "unexpected item(s) for schema v2: re_pair_outbox_high_water" in error
        for error in result.errors
    )


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
    assert any("schema_version must be 4, 3, or 2" in error for error in result.errors)


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

    assert result.ok is True
    assert result.release_ready is False


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


def test_outbox_base_helper_contract_matches_instrumented_test_source():
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
        / "data"
        / "OutboxBaseSeqTest.kt"
    ).read_text(encoding="utf-8")

    assert manual_qa_evidence.OUTBOX_BASE_TEST_CLASS.endswith(".OutboxBaseSeqTest")
    assert "class OutboxBaseSeqTest" in source
    instrumented_test_methods = tuple(
        re.findall(r"@Test\s+fun\s+([A-Za-z0-9_]+)\s*\(", source)
    )
    assert instrumented_test_methods == manual_qa_evidence.OUTBOX_BASE_TEST_METHODS


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
        lambda report: report["android_compatibility_qa"]["cursorwindow_large_payload"][
            "results"
        ][1].update({"sdk_evidence_ref": "JUnit output: /root/private/sdk.txt"}),
        lambda report: report["desktop_environment"].update(
            {"build_source": "Copied from /etc/clipvault/release.json"}
        ),
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


def test_rendered_public_references_cannot_inject_markdown_images_or_links():
    report = _valid_report()
    report["sections"]["android_device_qa"]["items"]["pairing"]["evidence"] = (
        "![remote](https://example.invalid/pixel.png)"
    )
    report["sections"]["sync_qa"]["items"]["public_clips_memory_sync"][
        "evidence"
    ] = "https://github.com/selinyi123/clipvault-personal/actions/runs/123"
    report["sections"]["sync_qa"]["items"]["secret_private_isolation"][
        "evidence"
    ] = r"left|right and slash\pipe|tail"

    result = manual_qa_evidence.validate_evidence(report)
    markdown = manual_qa_evidence.render_markdown(report, result)

    assert result.ok is True
    assert "![remote](https://example.invalid/pixel.png)" not in markdown
    assert r"\!\[remote\]\(https://example.invalid/pixel.png\)" in markdown
    assert (
        "<https://github.com/selinyi123/clipvault-personal/actions/runs/123>"
        in markdown
    )
    assert r"left\|right and slash\\pipe\|tail" in markdown
    assert r"left\\|right" not in markdown


def test_cli_writes_template_and_json_summary(tmp_path, capsys):
    template_path = tmp_path / "manual-qa.json"

    assert manual_qa_evidence.main(["--write-template", str(template_path)]) == 0
    loaded = json.loads(template_path.read_text(encoding="utf-8"))
    assert loaded["sections"]["android_device_qa"]["items"]["pairing"]["status"] == "blocked"

    valid_path = tmp_path / "valid.json"
    valid_path.write_text(json.dumps(_valid_report()), encoding="utf-8")
    assert manual_qa_evidence.main(["--input", str(valid_path), "--json"]) == 0
    blocked_output = json.loads(capsys.readouterr().out)
    assert blocked_output["ok"] is True
    assert blocked_output["structurally_complete"] is True
    assert blocked_output["release_ready"] is False
    assert manual_qa_evidence.main(
        ["--input", str(valid_path), "--json", "--no-fail"]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["release_ready"] is False
    assert output["final_draft_binding_assurance"] == "not_present_legacy_compatibility"
    assert output["item_counts"]["pass"] == 26

    markdown_path = tmp_path / "manual-qa-comment.md"
    assert manual_qa_evidence.main(
        [
            "--input",
            str(valid_path),
            "--output",
            str(markdown_path),
            "--no-fail",
        ]
    ) == 0
    assert "Manual QA evidence for Issue #36" in markdown_path.read_text(encoding="utf-8")


def test_cli_strict_mode_cross_checks_exact_final_draft_report(tmp_path, capsys):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    input_path = tmp_path / "manual-qa.json"
    artifact_path = tmp_path / "final-draft-artifacts.json"
    input_path.write_text(json.dumps(report), encoding="utf-8")
    artifact_path.write_text(json.dumps(artifact_report), encoding="utf-8")

    assert manual_qa_evidence.main([
        "--input",
        str(input_path),
        "--final-draft-artifact-evidence",
        str(artifact_path),
        "--require-final-draft-binding",
        "--json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["release_ready"] is True


def test_cli_release_ready_mode_rejects_frozen_schema_v2_without_output(tmp_path):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    report["schema_version"] = 2
    del report["sections"]["sync_qa"]["items"]["re_pair_outbox_high_water"]
    input_path = tmp_path / "manual-qa-v2.json"
    artifact_path = tmp_path / "final-draft-artifacts.json"
    output_path = tmp_path / "final-comment.md"
    input_path.write_text(json.dumps(report), encoding="utf-8")
    artifact_path.write_text(json.dumps(artifact_report), encoding="utf-8")

    assert manual_qa_evidence.main([
        "--input",
        str(input_path),
        "--final-draft-artifact-evidence",
        str(artifact_path),
        "--require-final-draft-binding",
        "--require-release-ready",
        "--output",
        str(output_path),
    ]) == 2
    assert output_path.exists() is False


def test_cli_release_ready_mode_writes_passing_schema_v4_output(tmp_path):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    input_path = tmp_path / "manual-qa-v4.json"
    artifact_path = tmp_path / "final-draft-artifacts.json"
    output_path = tmp_path / "final-comment.md"
    input_path.write_text(json.dumps(report), encoding="utf-8")
    artifact_path.write_text(json.dumps(artifact_report), encoding="utf-8")

    assert manual_qa_evidence.main([
        "--input",
        str(input_path),
        "--final-draft-artifact-evidence",
        str(artifact_path),
        "--require-final-draft-binding",
        "--require-release-ready",
        "--output",
        str(output_path),
    ]) == 0
    assert "Status: **PASS (OWNER-ATTESTED)**" in output_path.read_text(encoding="utf-8")


def test_cli_strict_artifact_report_rejects_duplicate_keys_and_nonfinite_numbers(
    tmp_path,
):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    input_path = tmp_path / "manual-qa.json"
    input_path.write_text(json.dumps(report), encoding="utf-8")

    duplicate_path = tmp_path / "duplicate-artifact.json"
    duplicate_raw = json.dumps(artifact_report).replace(
        '"conclusion": "success"',
        '"conclusion": "failure", "conclusion": "success"',
        1,
    )
    duplicate_path.write_text(duplicate_raw, encoding="utf-8")
    with pytest.raises(SystemExit):
        manual_qa_evidence.main([
            "--input",
            str(input_path),
            "--final-draft-artifact-evidence",
            str(duplicate_path),
            "--require-final-draft-binding",
            "--json",
        ])

    nonfinite_path = tmp_path / "nonfinite-artifact.json"
    nonfinite_path.write_text(
        json.dumps(artifact_report).replace('"attempt": 1', '"attempt": NaN', 1),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        manual_qa_evidence.main([
            "--input",
            str(input_path),
            "--final-draft-artifact-evidence",
            str(nonfinite_path),
            "--require-final-draft-binding",
            "--json",
        ])


def test_cli_strict_artifact_report_must_be_distinct_from_input_and_output(tmp_path):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    input_path = tmp_path / "manual-qa.json"
    artifact_path = tmp_path / "final-draft-artifacts.json"
    input_path.write_text(json.dumps(report), encoding="utf-8")
    artifact_path.write_text(json.dumps(artifact_report), encoding="utf-8")

    with pytest.raises(SystemExit):
        manual_qa_evidence.main([
            "--input",
            str(input_path),
            "--final-draft-artifact-evidence",
            str(input_path),
            "--require-final-draft-binding",
            "--json",
        ])
    with pytest.raises(SystemExit):
        manual_qa_evidence.main([
            "--input",
            str(input_path),
            "--final-draft-artifact-evidence",
            str(artifact_path),
            "--require-final-draft-binding",
            "--output",
            str(artifact_path),
            "--force",
        ])

    artifact_alias = tmp_path / "artifact-alias.json"
    try:
        os.link(artifact_path, artifact_alias)
    except (NotImplementedError, OSError):
        pytest.skip("hardlink creation is unavailable on this filesystem")
    with pytest.raises(SystemExit):
        manual_qa_evidence.main([
            "--input",
            str(artifact_alias),
            "--final-draft-artifact-evidence",
            str(artifact_path),
            "--require-final-draft-binding",
            "--json",
        ])


def test_cli_strict_artifact_report_rejects_output_hardlink_alias(tmp_path):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    input_path = tmp_path / "manual-qa.json"
    artifact_path = tmp_path / "final-draft-artifacts.json"
    input_path.write_text(json.dumps(report), encoding="utf-8")
    artifact_path.write_text(json.dumps(artifact_report), encoding="utf-8")
    original = artifact_path.read_bytes()

    hardlink_output = tmp_path / "hardlink-output.md"
    try:
        os.link(artifact_path, hardlink_output)
    except (NotImplementedError, OSError):
        pytest.skip("hardlink creation is unavailable on this filesystem")
    with pytest.raises(SystemExit):
        manual_qa_evidence.main([
            "--input",
            str(input_path),
            "--final-draft-artifact-evidence",
            str(artifact_path),
            "--require-final-draft-binding",
            "--output",
            str(hardlink_output),
            "--force",
        ])
    assert artifact_path.read_bytes() == original
    assert hardlink_output.read_bytes() == original


def test_cli_strict_artifact_report_rejects_output_symlink_alias(tmp_path):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    input_path = tmp_path / "manual-qa.json"
    artifact_path = tmp_path / "final-draft-artifacts.json"
    input_path.write_text(json.dumps(report), encoding="utf-8")
    artifact_path.write_text(json.dumps(artifact_report), encoding="utf-8")
    original = artifact_path.read_bytes()

    symlink_output = tmp_path / "symlink-output.md"
    try:
        symlink_output.symlink_to(artifact_path)
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation is unavailable on this filesystem")
    with pytest.raises(SystemExit):
        manual_qa_evidence.main([
            "--input",
            str(input_path),
            "--final-draft-artifact-evidence",
            str(artifact_path),
            "--require-final-draft-binding",
            "--output",
            str(symlink_output),
            "--force",
        ])
    assert artifact_path.read_bytes() == original


def test_cli_file_identity_comparison_error_fails_closed(tmp_path, monkeypatch, capsys):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    input_path = tmp_path / "manual-qa.json"
    artifact_path = tmp_path / "final-draft-artifacts.json"
    input_path.write_text(json.dumps(report), encoding="utf-8")
    artifact_path.write_text(json.dumps(artifact_report), encoding="utf-8")

    def fail_samefile(self, other):
        raise OSError("identity lookup unavailable")

    monkeypatch.setattr(Path, "samefile", fail_samefile)
    with pytest.raises(SystemExit):
        manual_qa_evidence.main([
            "--input",
            str(input_path),
            "--final-draft-artifact-evidence",
            str(artifact_path),
            "--require-final-draft-binding",
            "--json",
        ])

    assert "cannot safely compare file identities" in capsys.readouterr().err


def test_cli_file_identity_resolution_error_fails_closed(tmp_path, monkeypatch, capsys):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    input_path = tmp_path / "manual-qa.json"
    artifact_path = tmp_path / "final-draft-artifacts.json"
    input_path.write_text(json.dumps(report), encoding="utf-8")
    artifact_path.write_text(json.dumps(artifact_report), encoding="utf-8")

    def fail_resolve(self, strict=False):
        raise RuntimeError("resolution unavailable")

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(SystemExit):
        manual_qa_evidence.main([
            "--input",
            str(input_path),
            "--final-draft-artifact-evidence",
            str(artifact_path),
            "--require-final-draft-binding",
            "--json",
        ])

    assert "cannot safely compare file identities" in capsys.readouterr().err


def test_json_loader_bounds_input_and_markdown_redacts_binding_local_path(tmp_path):
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b'"' + b"x" * manual_qa_evidence.MAX_JSON_INPUT_BYTES + b'"')
    with pytest.raises(ValueError, match="input limit"):
        manual_qa_evidence.load_json(oversized)

    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(
        _valid_report(), artifact_report, evidence_ref=r"C:\\Private\\artifact.json"
    )
    result = manual_qa_evidence.validate_evidence(report)
    markdown = manual_qa_evidence.render_markdown(report, result)
    assert "C:\\Private" not in markdown
    assert "[redacted local evidence path]" in markdown


@pytest.mark.parametrize(
    "mutate",
    [
        lambda binding: binding["workflow_run"].__setitem__(
            "url", r"C:\Private\run.txt"
        ),
        lambda binding: binding["draft_release"].__setitem__(
            "url", r"\\server\private\release.txt"
        ),
        lambda binding: binding["android_signed_apk"].__setitem__(
            "name", "/home/owner/private/app.apk"
        ),
    ],
)
def test_renderer_redacts_private_paths_from_binding_identity_fields(mutate):
    artifact_report = _final_draft_artifact_report()
    report = _bind_report_to_final_draft(_valid_report(), artifact_report)
    mutate(report["release_artifact_binding"])

    result = manual_qa_evidence.validate_evidence(report)
    markdown = manual_qa_evidence.render_markdown(report, result)

    assert "C:\\Private" not in markdown
    assert "server\\private" not in markdown
    assert "/home/owner/private" not in markdown
    assert "[redacted local evidence path]" in markdown


def test_release_artifact_loader_restores_sys_modules_entry(monkeypatch):
    key = "release_artifact_evidence_for_manual_qa"
    sentinel = object()
    nested_sentinel = object()
    monkeypatch.setitem(sys.modules, key, sentinel)
    monkeypatch.setitem(sys.modules, "verify_release_manifest", nested_sentinel)
    monkeypatch.setattr(
        manual_qa_evidence, "_release_artifact_evidence_module", None
    )

    loaded = manual_qa_evidence._release_artifact_evidence()

    assert loaded is not sentinel
    assert sys.modules[key] is sentinel
    assert sys.modules["verify_release_manifest"] is nested_sentinel


def test_cli_refuses_to_overwrite_template_without_force(tmp_path):
    template_path = tmp_path / "manual-qa.json"
    template_path.write_bytes(b"OWNER EVIDENCE")

    with pytest.raises(SystemExit):
        manual_qa_evidence.main(["--write-template", str(template_path)])
    assert template_path.read_bytes() == b"OWNER EVIDENCE"

    assert manual_qa_evidence.main(
        ["--write-template", str(template_path), "--force"]
    ) == 0
    assert json.loads(template_path.read_text(encoding="utf-8"))["schema_version"] == 4


def test_cli_refuses_to_overwrite_rendered_output_without_force(tmp_path):
    input_path = tmp_path / "manual-qa.json"
    input_path.write_text(json.dumps(_valid_report()), encoding="utf-8")
    output_path = tmp_path / "comment.md"
    output_path.write_bytes(b"OWNER COMMENT")

    with pytest.raises(SystemExit):
        manual_qa_evidence.main(["--input", str(input_path), "--output", str(output_path)])
    assert output_path.read_bytes() == b"OWNER COMMENT"

    assert manual_qa_evidence.main(
        [
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--force",
            "--no-fail",
        ]
    ) == 0
    rendered = output_path.read_text(encoding="utf-8")
    assert "Status: **BLOCKED**" in rendered
    assert "final_draft_binding_assurance=not_present_legacy_compatibility" in rendered


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


def test_cli_requires_strict_binding_arguments_as_a_pair(tmp_path, capsys):
    input_path = tmp_path / "manual-qa.json"
    artifact_path = tmp_path / "final-draft-artifacts.json"
    input_path.write_text(json.dumps(_valid_report()), encoding="utf-8")
    artifact_path.write_text(json.dumps(_final_draft_artifact_report()), encoding="utf-8")

    with pytest.raises(SystemExit):
        manual_qa_evidence.main([
            "--input",
            str(input_path),
            "--require-final-draft-binding",
        ])
    assert "must be used together" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        manual_qa_evidence.main([
            "--input",
            str(input_path),
            "--final-draft-artifact-evidence",
            str(artifact_path),
        ])
    assert "must be used together" in capsys.readouterr().err


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
        [
            "--template",
            "--final-draft-artifact-evidence",
            "y.json",
            "--require-final-draft-binding",
        ],
    ],
)
def test_cli_requires_exactly_one_mode(argv):
    with pytest.raises(SystemExit):
        manual_qa_evidence.main(argv)
