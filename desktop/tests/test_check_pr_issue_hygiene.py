import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_pr_issue_hygiene.py"
_spec = importlib.util.spec_from_file_location("check_pr_issue_hygiene", _SCRIPT)
check_pr_issue_hygiene = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_pr_issue_hygiene
_spec.loader.exec_module(check_pr_issue_hygiene)


def test_safe_release_gate_wording_passes():
    body = """
    This PR hardens release-gate hygiene.

    Issue #36 remains open.
    This does not change issue state for #36 or #82.
    """

    assert check_pr_issue_hygiene.find_dangerous_references(body) == []
    assert check_pr_issue_hygiene.main(["--body", body]) == 0


def test_negative_auto_close_phrase_is_rejected():
    body = "This PR does not close #36 or #82."

    findings = check_pr_issue_hygiene.find_dangerous_references(body)

    assert [finding.issue for finding in findings] == [36, 82]
    assert findings[0].keyword == "close"
    assert check_pr_issue_hygiene.main(["--body", body]) == 1


def test_protected_issue_after_unprotected_issue_is_rejected():
    body = "Fixes #123 and #82 while Issue #36 remains open."

    findings = check_pr_issue_hygiene.find_dangerous_references(body)

    assert [(finding.issue, finding.keyword) for finding in findings] == [(82, "fixes")]


def test_multiple_github_reference_forms_are_rejected():
    body = "\n".join(
        [
            "Fixes: Issue #36",
            "resolves selinyi123/clipvault-personal#82",
            "closed https://github.com/selinyi123/clipvault-personal/issues/36",
        ]
    )

    findings = check_pr_issue_hygiene.find_dangerous_references(body)

    assert [(finding.line, finding.issue, finding.keyword) for finding in findings] == [
        (1, 36, "fixes"),
        (2, 82, "resolves"),
        (3, 36, "closed"),
    ]


def test_unprotected_issue_reference_is_ignored():
    body = "Fixes #123 while Issue #36 remains open."

    assert check_pr_issue_hygiene.find_dangerous_references(body) == []


def test_loads_pull_request_body_from_event_json(tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"pull_request": {"body": "Resolve #82 after owner approval."}}),
        encoding="utf-8",
    )

    assert check_pr_issue_hygiene.load_body_from_event(event_path) == (
        "Resolve #82 after owner approval."
    )
    assert check_pr_issue_hygiene.main(["--event-path", str(event_path)]) == 1


def test_loads_pull_request_title_and_body_from_event_json(tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "title": "Resolve #82",
                    "body": "Issue #36 remains open.",
                }
            }
        ),
        encoding="utf-8",
    )

    text = check_pr_issue_hygiene.load_text_from_event(event_path)

    assert "Resolve #82" in text
    assert "Issue #36 remains open." in text
    assert check_pr_issue_hygiene.main(["--event-path", str(event_path)]) == 1


def test_loads_push_commit_messages_from_event_json(tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "head_commit": {"message": "Safe merge commit"},
                "commits": [
                    {"message": "Document release gate"},
                    {"message": "Fixes selinyi123/clipvault-personal#82"},
                ],
            }
        ),
        encoding="utf-8",
    )

    text = check_pr_issue_hygiene.load_text_from_event(event_path)

    assert "Safe merge commit" in text
    assert "Fixes selinyi123/clipvault-personal#82" in text
    assert check_pr_issue_hygiene.main(["--event-path", str(event_path)]) == 1


def test_non_pull_request_event_is_safe_noop(tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"ref": "refs/heads/main"}), encoding="utf-8")

    assert check_pr_issue_hygiene.load_body_from_event(event_path) == ""
    assert check_pr_issue_hygiene.main(["--event-path", str(event_path)]) == 0
