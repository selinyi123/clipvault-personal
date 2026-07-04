"""Unit tests for the Issue #36 manual QA evidence helper."""

import importlib.util
import json
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
    data["android_device"] = {
        "model": "Pixel 8",
        "android_version": "15",
        "app_version": "1.6.0",
        "apk_source": "Release artifact build run 123",
    }
    data["desktop_environment"] = {
        "os": "Windows 11",
        "app_version": "1.6.0",
        "build_source": "Release artifact build run 123",
    }
    for section in manual_qa_evidence.REQUIRED_SECTIONS:
        for item in section.items:
            data["sections"][section.key]["items"][item.key] = {
                "status": "pass",
                "evidence": f"Observed {item.key}",
                "next_step": "",
                "notes": item.label,
            }
    return data


def test_template_contains_every_required_manual_qa_item():
    template = manual_qa_evidence.build_template()

    assert template["version"] == "v1.6.0"
    assert template["scope_note"] == manual_qa_evidence.scope_note()
    for section in manual_qa_evidence.REQUIRED_SECTIONS:
        assert section.key in template["sections"]
        for item in section.items:
            item_data = template["sections"][section.key]["items"][item.key]
            assert item_data["status"] == "blocked"
            assert item_data["next_step"]


def test_valid_all_pass_evidence_is_release_ready_and_renders_issue_comment():
    report = _valid_report()

    result = manual_qa_evidence.validate_evidence(report)
    markdown = manual_qa_evidence.render_markdown(report, result)

    assert result.ok is True
    assert result.release_ready is True
    assert result.item_counts == {"blocked": 0, "fail": 0, "pass": 17}
    assert "Manual QA evidence for Issue #36" in markdown
    assert "Status: **PASS**" in markdown
    assert "Manual Android device QA" in markdown
    assert "Manual IME privacy QA" in markdown
    assert "Manual sync QA" in markdown
    assert "Manual Windows clipboard privacy QA" in markdown
    assert "does not replace signed artifact evidence" in markdown


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
    assert any("expected 17 QA items" in error for error in result.errors)


def test_metadata_must_pin_version_and_full_target_commit():
    report = _valid_report()
    report["version"] = "v1.5.10"
    report["target_commit"] = "abc123"

    result = manual_qa_evidence.validate_evidence(report)

    assert result.ok is False
    assert "version must be v1.6.0, got v1.5.10" in result.errors
    assert "target_commit must be a full 40-character hexadecimal commit SHA" in result.errors


def test_template_placeholders_are_not_valid_evidence():
    report = _valid_report()
    report["tester"] = "REPLACE_WITH_TESTER_NAME"
    report["android_device"]["model"] = "REPLACE_WITH_DEVICE_MODEL"
    report["sections"]["android_device_qa"]["items"]["pairing"]["evidence"] = "REPLACE_WITH_OBSERVATION"

    result = manual_qa_evidence.validate_evidence(report)

    assert result.ok is False
    assert "tester must replace the template placeholder" in result.errors
    assert "android_device.model must replace the template placeholder" in result.errors
    assert any("pairing.evidence" in error for error in result.errors)


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
    assert output["item_counts"]["pass"] == 17

    markdown_path = tmp_path / "manual-qa-comment.md"
    assert manual_qa_evidence.main(["--input", str(valid_path), "--output", str(markdown_path)]) == 0
    assert "Manual QA evidence for Issue #36" in markdown_path.read_text(encoding="utf-8")


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
    ],
)
def test_cli_requires_exactly_one_mode(argv):
    with pytest.raises(SystemExit):
        manual_qa_evidence.main(argv)
