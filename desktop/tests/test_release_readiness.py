"""Unit tests for the read-only Issue #36 release-readiness checker."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "release_readiness.py"
_spec = importlib.util.spec_from_file_location("release_readiness", _SCRIPT)
release_readiness = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = release_readiness
_spec.loader.exec_module(release_readiness)


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
    return release_readiness.CommandResult(0, stdout=stdout)


def _failure(stderr):
    return release_readiness.CommandResult(1, stderr=stderr)


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


def _base_responses(*, sha="a" * 40, environments=None, issue_body="- [ ] missing\n"):
    success_run = [{
        "databaseId": 123,
        "status": "completed",
        "conclusion": "success",
        "headSha": sha,
        "url": "https://github.com/owner/repo/actions/runs/123",
        "event": "push",
        "createdAt": "2026-07-04T00:00:00Z",
        "displayTitle": "CI fixture",
    }]
    return {
        (
            "gh",
            "api",
            "repos/owner/repo/branches/main",
            "--jq",
            ".commit.sha",
        ): _success(sha + "\n"),
        _run_list_command("CI"): _success(_json(success_run)),
        _run_list_command("Release candidate dry run"): _success(_json(success_run)),
        _run_list_command("Release artifact build"): _success(_json([])),
        (
            "gh",
            "api",
            "repos/owner/repo/environments",
        ): _success(_json({"total_count": len(environments or []), "environments": environments or []})),
        (
            "gh",
            "release",
            "view",
            "v1.6.0",
            "--repo",
            "owner/repo",
            "--json",
            "tagName,name,isDraft,isPrerelease,publishedAt,url,targetCommitish",
        ): _failure("release not found"),
        (
            "gh",
            "issue",
            "view",
            "36",
            "--repo",
            "owner/repo",
            "--json",
            "number,state,title,url,body",
        ): _success(_json({
            "number": 36,
            "state": "OPEN",
            "title": "v1.6.0 release gate",
            "url": "https://github.com/owner/repo/issues/36",
            "body": issue_body,
        })),
    }


def test_report_blocks_missing_owner_controlled_release_evidence():
    fake = FakeGh(_base_responses())

    report = release_readiness.build_report(
        runner=fake,
        repo="owner/repo",
        version="v1.6.0",
        branch="main",
    )

    gates = {gate["name"]: gate for gate in report["gates"]}
    assert report["status"] == "blocked"
    assert gates["current main commit"]["status"] == "pass"
    assert gates["CI"]["status"] == "pass"
    assert gates["Release candidate dry run"]["status"] == "pass"
    assert gates["release environment"]["status"] == "blocked"
    assert gates["release environment secrets"]["status"] == "blocked"
    assert gates["signed release artifact workflow"]["status"] == "blocked"
    assert gates["GitHub Release publication"]["status"] == "blocked"
    assert gates["Issue #36"]["status"] == "blocked"
    assert gates["Issue #36"]["metadata"]["unchecked_items"] == ["missing"]
    assert "does not trigger workflows" in report["scope_note"]


def test_issue_checklist_parser_returns_checked_and_unchecked_items():
    body = """
Automated evidence:
- [x] Confirm current-main GitHub Actions CI green.
- [X] Confirm current-main release-candidate dry run green.

Owner evidence:
- [ ] Create/configure the `release` GitHub environment.
* [ ] Manual Windows clipboard privacy QA: source app formats are not captured.
"""

    items = release_readiness.parse_issue_checklist(body)
    metadata = release_readiness.issue_checklist_metadata(items)

    assert metadata["checked_count"] == 2
    assert metadata["unchecked_count"] == 2
    assert metadata["checked_items"] == [
        "Confirm current-main GitHub Actions CI green.",
        "Confirm current-main release-candidate dry run green.",
    ]
    assert metadata["unchecked_items"] == [
        "Create/configure the `release` GitHub environment.",
        "Manual Windows clipboard privacy QA: source app formats are not captured.",
    ]


def test_release_environment_secret_names_are_checked_without_values():
    responses = _base_responses(environments=[{"name": "release"}])
    responses[(
        "gh",
        "secret",
        "list",
        "--repo",
        "owner/repo",
        "--env",
        "release",
        "--json",
        "name,updatedAt",
    )] = _success(_json([
        {"name": "ANDROID_RELEASE_KEYSTORE_B64", "updatedAt": "2026-07-04T00:00:00Z"},
        {"name": "ANDROID_RELEASE_KEYSTORE_PASSWORD", "updatedAt": "2026-07-04T00:00:00Z"},
    ]))
    fake = FakeGh(responses)

    report = release_readiness.build_report(
        runner=fake,
        repo="owner/repo",
        version="v1.6.0",
        branch="main",
    )

    gate = {gate["name"]: gate for gate in report["gates"]}["release environment secrets"]
    assert gate["status"] == "blocked"
    assert "ANDROID_RELEASE_KEY_ALIAS" in gate["detail"]
    assert "ANDROID_RELEASE_KEY_PASSWORD" in gate["detail"]
    assert "ANDROID_RELEASE_KEYSTORE_B64=" not in gate["detail"]
    assert "ANDROID_RELEASE_KEYSTORE_PASSWORD=" not in gate["detail"]


def test_successful_release_artifact_run_with_matching_dispatch_title_is_warning_until_artifacts_are_inspected():
    sha = "b" * 40
    responses = _base_responses(
        sha=sha,
        environments=[{"name": "release"}],
        issue_body="all checklist rows recorded\n",
    )
    responses[(
        "gh",
        "secret",
        "list",
        "--repo",
        "owner/repo",
        "--env",
        "release",
        "--json",
        "name,updatedAt",
    )] = _success(_json([
        {"name": name, "updatedAt": "2026-07-04T00:00:00Z"}
        for name in sorted(release_readiness.REQUIRED_RELEASE_ENV_SECRETS)
    ]))
    responses[_run_list_command("Release artifact build")] = _success(_json([{
        "databaseId": 456,
        "status": "completed",
        "conclusion": "success",
        "headSha": sha,
        "url": "https://github.com/owner/repo/actions/runs/456",
        "event": "workflow_dispatch",
        "createdAt": "2026-07-04T00:00:00Z",
        "displayTitle": "Release artifacts v1.6.0 from main draft=false",
    }]))
    fake = FakeGh(responses)

    report = release_readiness.build_report(
        runner=fake,
        repo="owner/repo",
        version="v1.6.0",
        branch="main",
    )

    gate = {gate["name"]: gate for gate in report["gates"]}["signed release artifact workflow"]
    assert gate["status"] == "warn"
    assert "matching displayed dispatch inputs" in gate["detail"]
    assert "ANDROID_APKSIGNER_VERIFY.txt" in gate["next_step"]
    assert gate["metadata"]["display_title"] == "Release artifacts v1.6.0 from main draft=false"


def test_release_artifact_run_blocks_when_displayed_dispatch_inputs_do_not_match():
    sha = "e" * 40
    responses = _base_responses(
        sha=sha,
        environments=[{"name": "release"}],
        issue_body="all checklist rows recorded\n",
    )
    responses[(
        "gh",
        "secret",
        "list",
        "--repo",
        "owner/repo",
        "--env",
        "release",
        "--json",
        "name,updatedAt",
    )] = _success(_json([
        {"name": name, "updatedAt": "2026-07-04T00:00:00Z"}
        for name in sorted(release_readiness.REQUIRED_RELEASE_ENV_SECRETS)
    ]))
    responses[_run_list_command("Release artifact build")] = _success(_json([{
        "databaseId": 457,
        "status": "completed",
        "conclusion": "success",
        "headSha": sha,
        "url": "https://github.com/owner/repo/actions/runs/457",
        "event": "workflow_dispatch",
        "createdAt": "2026-07-04T00:00:00Z",
        "displayTitle": "Release artifacts v1.5.10 from main draft=false",
    }]))
    fake = FakeGh(responses)

    report = release_readiness.build_report(
        runner=fake,
        repo="owner/repo",
        version="v1.6.0",
        branch="main",
    )

    gate = {gate["name"]: gate for gate in report["gates"]}["signed release artifact workflow"]
    assert gate["status"] == "blocked"
    assert "do not prove the expected v1.6.0 / main release run" in gate["detail"]
    assert "Release artifacts v1.6.0 from main draft=false" in gate["next_step"]
    assert gate["metadata"]["display_title"] == "Release artifacts v1.5.10 from main draft=false"


def test_workflow_success_must_match_current_main_sha():
    sha = "c" * 40
    responses = _base_responses(sha=sha)
    responses[_run_list_command("CI")] = _success(_json([{
        "databaseId": 789,
        "status": "completed",
        "conclusion": "success",
        "headSha": "d" * 40,
        "url": "https://github.com/owner/repo/actions/runs/789",
        "event": "push",
        "createdAt": "2026-07-04T00:00:00Z",
        "displayTitle": "stale CI fixture",
    }]))
    fake = FakeGh(responses)

    report = release_readiness.build_report(
        runner=fake,
        repo="owner/repo",
        version="v1.6.0",
        branch="main",
    )

    gate = {gate["name"]: gate for gate in report["gates"]}["CI"]
    assert gate["status"] == "blocked"
    assert sha in gate["detail"]


@pytest.mark.parametrize("args", [
    ["gh", "workflow", "run", "Release artifact build"],
    ["gh", "release", "create", "v1.6.0"],
    ["gh", "secret", "set", "ANDROID_RELEASE_KEYSTORE_B64"],
    ["gh", "issue", "close", "36"],
])
def test_gh_command_guard_rejects_write_operations(args):
    with pytest.raises(ValueError, match="non-read-only"):
        release_readiness._assert_read_only_gh(args)


def test_text_renderer_marks_blocked_gates():
    report = {
        "repo": "owner/repo",
        "version": "v1.6.0",
        "status": "blocked",
        "blocked": 1,
        "warnings": 0,
        "main_sha": "a" * 40,
        "scope_note": "read-only",
        "gates": [{
            "name": "release environment",
            "status": "blocked",
            "detail": "missing",
            "evidence": "",
            "next_step": "create environment",
            "metadata": {
                "unchecked_items": [
                    "Create/configure the `release` GitHub environment.",
                    "Manual Android device QA.",
                ],
            },
        }],
    }

    text = release_readiness._render_text(report)

    assert "[ ] release environment: missing" in text
    assert "next: create environment" in text
    assert "unchecked:" in text
    assert "Create/configure the `release` GitHub environment." in text
    assert "Manual Android device QA." in text
