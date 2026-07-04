"""Unit tests for the read-only Issue #82 field-test readiness checker."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "field_test_readiness.py"
_spec = importlib.util.spec_from_file_location("field_test_readiness", _SCRIPT)
field_test_readiness = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = field_test_readiness
_spec.loader.exec_module(field_test_readiness)


class FakeGh:
    def __init__(self, responses):
        self.responses = responses
        self.commands = []

    def __call__(self, args):
        key = tuple(args)
        self.commands.append(key)
        try:
            return self.responses[key]
        except KeyError as exc:
            raise AssertionError(f"unexpected gh command: {args!r}") from exc


def _json(data):
    return json.dumps(data)


def _success(stdout):
    return field_test_readiness.CommandResult(0, stdout=stdout)


def _failure(stderr):
    return field_test_readiness.CommandResult(1, stderr=stderr)


def _run_list_command(workflow):
    return (
        "gh",
        "run",
        "list",
        "--repo",
        "owner/repo",
        "--workflow",
        workflow,
        "--branch",
        "main",
        "--limit",
        "10",
        "--json",
        "databaseId,status,conclusion,headSha,url,event,createdAt,displayTitle",
    )


def _artifact_inventory_command(run_id=222):
    return (
        "gh",
        "api",
        f"repos/owner/repo/actions/runs/{run_id}/artifacts?per_page=100",
    )


def _base_responses(*, sha="a" * 40, issue_body=None, issue_comments=None, artifacts=None):
    ci_run = [{
        "databaseId": 111,
        "status": "completed",
        "conclusion": "success",
        "headSha": sha,
        "url": "https://github.com/owner/repo/actions/runs/111",
        "event": "push",
        "createdAt": "2026-07-04T00:00:00Z",
        "displayTitle": "CI fixture",
    }]
    candidate_run = [{
        "databaseId": 222,
        "status": "completed",
        "conclusion": "success",
        "headSha": sha,
        "url": "https://github.com/owner/repo/actions/runs/222",
        "event": "push",
        "createdAt": "2026-07-04T00:01:00Z",
        "displayTitle": "Release candidate fixture",
    }]
    artifact_rows = artifacts if artifacts is not None else [
        {
            "id": 9001,
            "name": field_test_readiness.EXPECTED_WINDOWS_ARTIFACT_NAME,
            "expired": False,
            "expires_at": "2026-10-02T00:00:00Z",
            "size_in_bytes": 123,
        },
        {
            "id": 9002,
            "name": field_test_readiness.EXPECTED_ANDROID_ARTIFACT_NAME,
            "expired": False,
            "expires_at": "2026-10-02T00:00:00Z",
            "size_in_bytes": 456,
        },
    ]
    body = issue_body or """
Automated:
- [x] CI green.
- [x] Release-candidate run green.
- [ ] Owner downloads candidate artifacts.
- [ ] Android field-test smoke.
"""
    comments = issue_comments if issue_comments is not None else [{
        "body": (
            f"Evidence for {sha}\n"
            "CI: https://github.com/owner/repo/actions/runs/111\n"
            "RC: https://github.com/owner/repo/actions/runs/222\n"
        )
    }]
    return {
        (
            "gh",
            "api",
            "repos/owner/repo/branches/main",
            "--jq",
            ".commit.sha",
        ): _success(sha + "\n"),
        _run_list_command("CI"): _success(_json(ci_run)),
        _run_list_command("Release candidate dry run"): _success(_json(candidate_run)),
        _artifact_inventory_command(): _success(_json({
            "total_count": len(artifact_rows),
            "artifacts": artifact_rows,
        })),
        (
            "gh",
            "issue",
            "view",
            "82",
            "--repo",
            "owner/repo",
            "--json",
            "number,state,title,url,body,comments",
        ): _success(_json({
            "number": 82,
            "state": "OPEN",
            "title": "v1.7 field-test and release-gate checklist",
            "url": "https://github.com/owner/repo/issues/82",
            "body": body,
            "comments": comments,
        })),
    }


def test_report_reads_live_candidate_inventory_but_blocks_unchecked_issue_rows():
    fake = FakeGh(_base_responses())

    report = field_test_readiness.build_report(
        runner=fake,
        repo="owner/repo",
        branch="main",
    )

    gates = {gate["name"]: gate for gate in report["gates"]}
    assert report["status"] == "blocked"
    assert gates["current main commit"]["status"] == "pass"
    assert gates["CI"]["status"] == "pass"
    assert gates["Release candidate dry run"]["status"] == "pass"
    assert gates["candidate artifact inventory"]["status"] == "pass"
    assert gates["candidate artifact inventory"]["metadata"]["present_required_artifacts"] == [
        field_test_readiness.EXPECTED_ANDROID_ARTIFACT_NAME,
        field_test_readiness.EXPECTED_WINDOWS_ARTIFACT_NAME,
    ]
    assert gates["Issue #82"]["status"] == "blocked"
    assert gates["Issue #82"]["metadata"]["unchecked_count"] == 2
    assert gates["Issue #82 current-run evidence markers"]["status"] == "warn"
    assert gates["Issue #82 current-run evidence markers"]["metadata"]["body_or_comments_have_all_markers"] is True
    assert "does not trigger workflows" in report["scope_note"]
    assert "does not download artifacts" in report["scope_note"]


def test_candidate_artifact_inventory_blocks_missing_or_expired_required_artifacts():
    fake = FakeGh(_base_responses(artifacts=[
        {
            "id": 9001,
            "name": field_test_readiness.EXPECTED_WINDOWS_ARTIFACT_NAME,
            "expired": True,
            "expires_at": "2026-10-02T00:00:00Z",
            "size_in_bytes": 123,
        },
    ]))

    report = field_test_readiness.build_report(
        runner=fake,
        repo="owner/repo",
        branch="main",
    )

    gate = {gate["name"]: gate for gate in report["gates"]}["candidate artifact inventory"]
    assert gate["status"] == "blocked"
    assert field_test_readiness.EXPECTED_ANDROID_ARTIFACT_NAME in gate["metadata"]["missing_required_artifacts"]
    assert field_test_readiness.EXPECTED_WINDOWS_ARTIFACT_NAME in gate["metadata"]["expired_required_artifacts"]


def test_issue_checklist_parser_returns_checked_and_unchecked_items():
    body = """
- [x] Current-main CI green.
- [X] Current-main release-candidate dry run green.
* [ ] Owner downloads artifacts.
* [ ] Owner runs device smoke.
"""

    items = field_test_readiness.parse_issue_checklist(body)
    metadata = field_test_readiness.issue_checklist_metadata(items)

    assert metadata["checked_items"] == [
        "Current-main CI green.",
        "Current-main release-candidate dry run green.",
    ]
    assert metadata["unchecked_items"] == [
        "Owner downloads artifacts.",
        "Owner runs device smoke.",
    ]


@pytest.mark.parametrize("args", [
    ["gh", "workflow", "run", "Release candidate dry run"],
    ["gh", "run", "download", "222"],
    ["gh", "issue", "comment", "82", "--body", "done"],
    ["gh", "issue", "close", "82"],
    ["gh", "api", "-X", "DELETE", "repos/owner/repo/actions/artifacts/1"],
    ["gh", "api", "repos/owner/repo/issues/82", "--method=PATCH"],
    ["gh", "api", "repos/owner/repo/issues/82", "-f", "state=closed"],
])
def test_gh_command_guard_rejects_write_operations(args):
    with pytest.raises(ValueError, match="refusing"):
        field_test_readiness._assert_read_only_gh(args)


def test_text_renderer_includes_artifacts_and_unchecked_rows():
    fake = FakeGh(_base_responses())
    report = field_test_readiness.build_report(
        runner=fake,
        repo="owner/repo",
        branch="main",
    )

    text = field_test_readiness._render_text(report)

    assert "candidate artifact inventory" in text
    assert field_test_readiness.EXPECTED_WINDOWS_ARTIFACT_NAME in text
    assert field_test_readiness.EXPECTED_ANDROID_ARTIFACT_NAME in text
    assert "unchecked:" in text
    assert "Owner downloads candidate artifacts." in text
    assert "current-run evidence markers" in text


def test_report_blocks_when_candidate_workflow_is_not_current_main_success():
    sha = "b" * 40
    responses = _base_responses(sha=sha)
    responses[_run_list_command("Release candidate dry run")] = _success(_json([{
        "databaseId": 333,
        "status": "completed",
        "conclusion": "success",
        "headSha": "c" * 40,
        "url": "https://github.com/owner/repo/actions/runs/333",
        "event": "push",
        "createdAt": "2026-07-04T00:00:00Z",
        "displayTitle": "stale Release candidate fixture",
    }]))
    del responses[_artifact_inventory_command()]
    fake = FakeGh(responses)

    report = field_test_readiness.build_report(
        runner=fake,
        repo="owner/repo",
        branch="main",
    )

    gates = {gate["name"]: gate for gate in report["gates"]}
    assert gates["Release candidate dry run"]["status"] == "blocked"
    assert gates["candidate artifact inventory"]["status"] == "blocked"
    assert gates["Issue #82 current-run evidence markers"]["status"] == "blocked"
    assert sha in gates["Release candidate dry run"]["detail"]


def test_artifact_inventory_read_failure_is_reported_as_blocked():
    responses = _base_responses()
    responses[_artifact_inventory_command()] = _failure("actions artifacts hidden")
    fake = FakeGh(responses)

    report = field_test_readiness.build_report(
        runner=fake,
        repo="owner/repo",
        branch="main",
    )

    gate = {gate["name"]: gate for gate in report["gates"]}["candidate artifact inventory"]
    assert gate["status"] == "blocked"
    assert "actions artifacts hidden" in gate["evidence"]


def test_issue_current_markers_pass_when_body_mentions_current_run_evidence():
    sha = "d" * 40
    body = f"""
- [x] CI green.
- [x] Release-candidate run green.
- [x] Issue #82 mentions {sha}.
- [x] CI URL https://github.com/owner/repo/actions/runs/111.
- [x] RC URL https://github.com/owner/repo/actions/runs/222.
"""
    fake = FakeGh(_base_responses(sha=sha, issue_body=body, issue_comments=[]))

    report = field_test_readiness.build_report(
        runner=fake,
        repo="owner/repo",
        branch="main",
    )

    gate = {gate["name"]: gate for gate in report["gates"]}[
        "Issue #82 current-run evidence markers"
    ]
    assert gate["status"] == "pass"
    assert gate["metadata"]["body_has_all_markers"] is True


def test_issue_current_markers_block_when_body_and_comments_are_stale():
    fake = FakeGh(_base_responses(issue_comments=[{
        "body": "old evidence for https://github.com/owner/repo/actions/runs/999"
    }]))

    report = field_test_readiness.build_report(
        runner=fake,
        repo="owner/repo",
        branch="main",
    )

    gate = {gate["name"]: gate for gate in report["gates"]}[
        "Issue #82 current-run evidence markers"
    ]
    assert gate["status"] == "blocked"
    assert "CI run URL" in gate["metadata"]["body_or_comments_missing_markers"]
