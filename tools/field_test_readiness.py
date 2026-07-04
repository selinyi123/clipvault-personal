#!/usr/bin/env python3
"""Read-only readiness report for the Issue #82 v1.7 field-test gate.

This helper aggregates live GitHub state for the current-main field-test
candidate lane. It intentionally does not trigger workflows, download
artifacts, verify downloaded manifest/checksum bytes, install apps, run device
QA, post issue comments, edit issues, close issues, sign or publish releases,
or claim v1.7 stable.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterable

DEFAULT_REPO = "selinyi123/clipvault-personal"
DEFAULT_BRANCH = "main"
DEFAULT_SOURCE_VERSION = "1.6.0"
ISSUE_NUMBER = 82
EXPECTED_WINDOWS_ARTIFACT_NAME = "clipvault-windows-release-candidate"
EXPECTED_ANDROID_ARTIFACT_NAME = "clipvault-android-release-candidate"
REQUIRED_CANDIDATE_ARTIFACTS = {
    EXPECTED_WINDOWS_ARTIFACT_NAME,
    EXPECTED_ANDROID_ARTIFACT_NAME,
}
CHECKLIST_ITEM_RE = re.compile(r"(?m)^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<text>.+?)\s*$")
COMMIT_RE = re.compile(r"\b[0-9a-f]{40}\b")

READ_ONLY_GH_SUBCOMMANDS = {
    ("issue", "view"),
    ("run", "list"),
}
FORBIDDEN_GH_API_FLAGS = {
    "-X",
    "--method",
    "-f",
    "--field",
    "-F",
    "--raw-field",
    "--input",
}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


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


Runner = Callable[[list[str]], CommandResult]


def _pass(
    name: str,
    detail: str,
    *,
    evidence: str = "",
    metadata: dict[str, object] | None = None,
) -> Gate:
    return Gate(name=name, status="pass", detail=detail, evidence=evidence, metadata=metadata)


def _blocked(
    name: str,
    detail: str,
    *,
    evidence: str = "",
    next_step: str = "",
    metadata: dict[str, object] | None = None,
) -> Gate:
    return Gate(
        name=name,
        status="blocked",
        detail=detail,
        evidence=evidence,
        next_step=next_step,
        metadata=metadata,
    )


def _warn(
    name: str,
    detail: str,
    *,
    evidence: str = "",
    next_step: str = "",
    metadata: dict[str, object] | None = None,
) -> Gate:
    return Gate(
        name=name,
        status="warn",
        detail=detail,
        evidence=evidence,
        next_step=next_step,
        metadata=metadata,
    )


def _assert_read_only_gh(args: list[str]) -> None:
    if not args or args[0] != "gh":
        raise ValueError("field-test readiness runner only supports gh commands")
    if len(args) < 2:
        raise ValueError("missing gh subcommand")

    if args[1] == "api":
        for index, arg in enumerate(args):
            if arg in FORBIDDEN_GH_API_FLAGS:
                raise ValueError(f"refusing write-capable gh api flag (non-read-only): {arg}")
            if arg.startswith("--method=") or arg.startswith("-X"):
                raise ValueError(f"refusing write-capable gh api flag (non-read-only): {arg}")
            if index > 1 and arg.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                raise ValueError(f"refusing non-read-only gh api method: {arg}")
        return

    command = tuple(args[1:3])
    if command in READ_ONLY_GH_SUBCOMMANDS:
        return
    raise ValueError(f"refusing non-read-only gh command: {' '.join(args)}")


def run_command(args: list[str]) -> CommandResult:
    _assert_read_only_gh(args)
    completed = subprocess.run(
        args,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _load_json(result: CommandResult, label: str) -> object:
    if result.returncode != 0:
        raise ValueError(f"{label} command failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} did not return valid JSON") from exc


def _run_json(runner: Runner, args: list[str], label: str) -> object:
    return _load_json(runner(args), label)


def parse_issue_checklist(body: str) -> list[dict[str, object]]:
    """Parse GitHub Markdown task-list rows from an issue body."""
    items: list[dict[str, object]] = []
    for match in CHECKLIST_ITEM_RE.finditer(body):
        text = " ".join(match.group("text").split())
        if not text:
            continue
        items.append({
            "checked": match.group("mark").lower() == "x",
            "text": text,
        })
    return items


def issue_checklist_metadata(items: list[dict[str, object]]) -> dict[str, object]:
    checked_items = [
        str(item["text"])
        for item in items
        if item.get("checked") is True
    ]
    unchecked_items = [
        str(item["text"])
        for item in items
        if item.get("checked") is False
    ]
    return {
        "checked_count": len(checked_items),
        "unchecked_count": len(unchecked_items),
        "checked_items": checked_items,
        "unchecked_items": unchecked_items,
    }


def fetch_branch_sha(runner: Runner, repo: str, branch: str) -> tuple[str | None, Gate]:
    result = runner([
        "gh",
        "api",
        f"repos/{repo}/branches/{branch}",
        "--jq",
        ".commit.sha",
    ])
    if result.returncode != 0:
        return None, _blocked(
            "current main commit",
            f"Could not read {branch} branch commit.",
            evidence=result.stderr.strip() or result.stdout.strip(),
            next_step="Confirm gh authentication and repository access.",
        )
    sha = result.stdout.strip()
    if not sha:
        return None, _blocked(
            "current main commit",
            f"{branch} branch lookup returned an empty SHA.",
            next_step="Re-run after GitHub returns branch metadata.",
        )
    return sha, _pass("current main commit", f"{branch} is {sha}.", evidence=sha)


def latest_success_for_sha(runs: object, sha: str) -> dict[str, object] | None:
    if not isinstance(runs, list):
        return None
    for run in runs:
        if not isinstance(run, dict):
            continue
        if (
            run.get("headSha") == sha
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
        ):
            return run
    return None


def check_workflow_success(
    runner: Runner,
    *,
    repo: str,
    branch: str,
    workflow: str,
    sha: str | None,
) -> tuple[dict[str, object] | None, Gate]:
    if sha is None:
        return None, _blocked(
            workflow,
            "Skipped because the current main SHA is unknown.",
            next_step="Fix the current main commit check first.",
        )
    try:
        runs = _run_json(
            runner,
            [
                "gh",
                "run",
                "list",
                "--repo",
                repo,
                "--workflow",
                workflow,
                "--branch",
                branch,
                "--limit",
                "10",
                "--json",
                "databaseId,status,conclusion,headSha,url,event,createdAt,displayTitle",
            ],
            workflow,
        )
    except ValueError as exc:
        return None, _blocked(
            workflow,
            f"Could not read {workflow} workflow runs.",
            evidence=str(exc),
            next_step="Inspect GitHub Actions permissions or workflow name.",
        )
    run = latest_success_for_sha(runs, sha)
    if run is None:
        return None, _blocked(
            workflow,
            f"No completed successful {workflow} run found for {branch} SHA {sha}.",
            next_step=f"Wait for or rerun {workflow} on {branch}, then record the run URL on Issue #{ISSUE_NUMBER}.",
        )
    return run, _pass(
        workflow,
        f"Successful {workflow} run targets current {branch} SHA {sha}.",
        evidence=str(run.get("url", "")),
        metadata={
            "database_id": run.get("databaseId"),
            "created_at": run.get("createdAt"),
            "display_title": run.get("displayTitle"),
        },
    )


def candidate_artifact_metadata(artifacts: object) -> dict[str, object]:
    rows = artifacts if isinstance(artifacts, list) else []
    normalized: list[dict[str, object]] = []
    names: set[str] = set()
    expired_names: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not isinstance(name, str):
            continue
        names.add(name)
        if row.get("expired") is True:
            expired_names.append(name)
        normalized.append({
            "id": row.get("id"),
            "name": name,
            "expired": bool(row.get("expired")),
            "expires_at": row.get("expires_at"),
            "size_in_bytes": row.get("size_in_bytes"),
            "digest": row.get("digest"),
        })
    missing = sorted(REQUIRED_CANDIDATE_ARTIFACTS - names)
    present = sorted(REQUIRED_CANDIDATE_ARTIFACTS & names)
    return {
        "required_artifacts": sorted(REQUIRED_CANDIDATE_ARTIFACTS),
        "present_required_artifacts": present,
        "missing_required_artifacts": missing,
        "expired_required_artifacts": sorted(
            name for name in expired_names if name in REQUIRED_CANDIDATE_ARTIFACTS
        ),
        "artifacts": normalized,
    }


def check_candidate_artifacts(
    runner: Runner,
    *,
    repo: str,
    release_candidate_run: dict[str, object] | None,
) -> Gate:
    if release_candidate_run is None:
        return _blocked(
            "candidate artifact inventory",
            "Skipped because no successful current-main Release candidate dry run was found.",
            next_step="Get a successful Release candidate dry run for the current main SHA first.",
        )
    run_id = release_candidate_run.get("databaseId")
    if not isinstance(run_id, int):
        return _blocked(
            "candidate artifact inventory",
            "Release candidate run metadata did not include a numeric databaseId.",
            next_step="Inspect the workflow run manually and rerun this helper after GitHub returns run metadata.",
        )
    try:
        data = _run_json(
            runner,
            [
                "gh",
                "api",
                f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100",
            ],
            "release-candidate artifacts",
        )
    except ValueError as exc:
        return _blocked(
            "candidate artifact inventory",
            "Could not read release-candidate artifact inventory.",
            evidence=str(exc),
            next_step="Confirm gh authentication and Actions artifact visibility.",
        )
    artifacts = data.get("artifacts", []) if isinstance(data, dict) else []
    metadata = candidate_artifact_metadata(artifacts)
    missing = metadata["missing_required_artifacts"]
    expired = metadata["expired_required_artifacts"]
    if missing:
        return _blocked(
            "candidate artifact inventory",
            "The current-main release-candidate run is missing required candidate artifacts.",
            next_step="Rerun or fix the Release candidate dry run until both platform artifacts are uploaded.",
            metadata=metadata,
        )
    if expired:
        return _blocked(
            "candidate artifact inventory",
            "Required candidate artifacts are present but expired.",
            next_step="Rerun the Release candidate dry run for fresh downloadable artifacts.",
            metadata=metadata,
        )
    return _pass(
        "candidate artifact inventory",
        "Required Windows and Android release-candidate artifacts are present and not expired.",
        evidence=str(release_candidate_run.get("url", "")),
        metadata=metadata,
    )


def fetch_issue_data(runner: Runner, repo: str) -> tuple[dict[str, object] | None, Gate | None]:
    try:
        data = _run_json(
            runner,
            [
                "gh",
                "issue",
                "view",
                str(ISSUE_NUMBER),
                "--repo",
                repo,
                "--json",
                "number,state,title,url,body,comments",
            ],
            f"Issue #{ISSUE_NUMBER}",
        )
    except ValueError as exc:
        return None, _blocked(
            f"Issue #{ISSUE_NUMBER}",
            f"Could not read Issue #{ISSUE_NUMBER}.",
            evidence=str(exc),
            next_step="Confirm gh authentication and issue visibility.",
        )
    if not isinstance(data, dict):
        return None, _blocked(
            f"Issue #{ISSUE_NUMBER}",
            f"Issue #{ISSUE_NUMBER} lookup returned unexpected JSON.",
            next_step="Inspect issue state manually.",
        )
    return data, None


def check_issue_checklist(issue_data: dict[str, object]) -> Gate:
    state = issue_data.get("state")
    body = issue_data.get("body", "")
    checklist_items = parse_issue_checklist(body if isinstance(body, str) else "")
    metadata = issue_checklist_metadata(checklist_items)
    unchecked = int(metadata["unchecked_count"])
    if not checklist_items:
        return _blocked(
            f"Issue #{ISSUE_NUMBER}",
            f"Issue #{ISSUE_NUMBER} body has no GitHub task-list checklist items.",
            evidence=str(issue_data.get("url", "")),
            next_step="Restore explicit field-test/release-gate checklist rows before treating it as ready.",
            metadata=metadata,
        )
    if state != "OPEN":
        return _warn(
            f"Issue #{ISSUE_NUMBER}",
            f"Issue #{ISSUE_NUMBER} is {state}; verify Owner approval before treating the field-test gate as closed.",
            evidence=str(issue_data.get("url", "")),
            next_step="Confirm every checklist row and Owner decision before using this issue as v1.7 evidence.",
            metadata=metadata,
        )
    if unchecked:
        return _blocked(
            f"Issue #{ISSUE_NUMBER}",
            f"Issue #{ISSUE_NUMBER} is open with {unchecked} unchecked field-test/release-gate checklist items.",
            evidence=str(issue_data.get("url", "")),
            next_step="Record real evidence for every unchecked row before claiming field-test completion.",
            metadata=metadata,
        )
    return _pass(
        f"Issue #{ISSUE_NUMBER}",
        f"Issue #{ISSUE_NUMBER} is open and its body has no unchecked checklist items.",
        evidence=str(issue_data.get("url", "")),
        metadata=metadata,
    )


def _issue_comments_text(issue_data: dict[str, object]) -> str:
    comments = issue_data.get("comments")
    if not isinstance(comments, list):
        return ""
    bodies: list[str] = []
    for comment in comments:
        if isinstance(comment, dict) and isinstance(comment.get("body"), str):
            bodies.append(comment["body"])
    return "\n".join(bodies)


def check_issue_current_markers(
    issue_data: dict[str, object],
    *,
    main_sha: str | None,
    ci_run: dict[str, object] | None,
    candidate_run: dict[str, object] | None,
) -> Gate:
    if main_sha is None or ci_run is None or candidate_run is None:
        return _blocked(
            f"Issue #{ISSUE_NUMBER} current-run evidence markers",
            "Skipped because current main, CI, or release-candidate run markers are unknown.",
            next_step="Resolve current-main CI and release-candidate checks first.",
        )
    markers = {
        "main SHA": main_sha,
        "CI run URL": str(ci_run.get("url", "")),
        "Release candidate run URL": str(candidate_run.get("url", "")),
    }
    markers = {label: value for label, value in markers.items() if value}
    if not markers:
        return _blocked(
            f"Issue #{ISSUE_NUMBER} current-run evidence markers",
            "Skipped because current main/CI/release-candidate run markers are unknown.",
            next_step="Resolve current-main CI and release-candidate checks first.",
        )

    body = issue_data.get("body", "")
    body_text = body if isinstance(body, str) else ""
    comments_text = _issue_comments_text(issue_data)
    combined_text = body_text + "\n" + comments_text
    body_missing = [
        label
        for label, value in markers.items()
        if value not in body_text
    ]
    combined_missing = [
        label
        for label, value in markers.items()
        if value not in combined_text
    ]
    metadata = {
        "body_missing_markers": body_missing,
        "body_has_all_markers": not body_missing,
        "body_or_comments_missing_markers": combined_missing,
        "body_or_comments_have_all_markers": not combined_missing,
        "comment_count": len(issue_data.get("comments", [])) if isinstance(issue_data.get("comments"), list) else 0,
    }
    if not body_missing:
        return _pass(
            f"Issue #{ISSUE_NUMBER} current-run evidence markers",
            "Issue body mentions the current main SHA, CI run URL, and release-candidate run URL.",
            evidence=str(issue_data.get("url", "")),
            metadata=metadata,
        )
    if not combined_missing:
        return _warn(
            f"Issue #{ISSUE_NUMBER} current-run evidence markers",
            "Issue body is missing current run markers, but issue comments mention the current main SHA, CI run URL, and release-candidate run URL.",
            evidence=str(issue_data.get("url", "")),
            next_step="When Owner updates Issue #82, copy the current marker evidence into the checklist/body or keep a linked evidence comment.",
            metadata=metadata,
        )
    return _blocked(
        f"Issue #{ISSUE_NUMBER} current-run evidence markers",
        "Issue body/comments do not mention all current main SHA, CI run URL, and release-candidate run URL markers.",
        evidence=str(issue_data.get("url", "")),
        next_step="Post or record a field-test evidence comment that names the current main SHA and matching CI/release-candidate run URLs.",
        metadata=metadata,
    )


def build_report(
    *,
    runner: Runner = run_command,
    repo: str = DEFAULT_REPO,
    branch: str = DEFAULT_BRANCH,
    source_version: str = DEFAULT_SOURCE_VERSION,
) -> dict[str, object]:
    main_sha, main_gate = fetch_branch_sha(runner, repo, branch)
    ci_run, ci_gate = check_workflow_success(
        runner,
        repo=repo,
        branch=branch,
        workflow="CI",
        sha=main_sha,
    )
    candidate_run, candidate_gate = check_workflow_success(
        runner,
        repo=repo,
        branch=branch,
        workflow="Release candidate dry run",
        sha=main_sha,
    )
    issue_data, issue_fetch_gate = fetch_issue_data(runner, repo)
    gates = [
        main_gate,
        ci_gate,
        candidate_gate,
        check_candidate_artifacts(
            runner,
            repo=repo,
            release_candidate_run=candidate_run,
        ),
    ]
    if issue_fetch_gate is not None:
        gates.extend([
            issue_fetch_gate,
            _blocked(
                f"Issue #{ISSUE_NUMBER} current-run evidence markers",
                "Skipped because Issue #82 could not be read.",
                next_step="Fix issue visibility/read access first.",
            ),
        ])
    else:
        assert issue_data is not None
        gates.extend([
            check_issue_checklist(issue_data),
            check_issue_current_markers(
                issue_data,
                main_sha=main_sha,
                ci_run=ci_run,
                candidate_run=candidate_run,
            ),
        ])
    blocked = sum(1 for gate in gates if gate.status == "blocked")
    warnings = sum(1 for gate in gates if gate.status == "warn")
    status = "ready" if blocked == 0 and warnings == 0 else "blocked"
    return {
        "repo": repo,
        "branch": branch,
        "source_version": source_version,
        "main_sha": main_sha,
        "status": status,
        "blocked": blocked,
        "warnings": warnings,
        "ci_run_url": ci_run.get("url") if ci_run else "",
        "candidate_run_url": candidate_run.get("url") if candidate_run else "",
        "gates": [gate.as_dict() for gate in gates],
        "scope_note": (
            "Read-only report. It does not trigger workflows, does not download artifacts, "
            "does not verify downloaded manifest/checksum bytes, install apps, run device QA, "
            "post comments, edit issues, sign or publish releases, close Issue #82/#36, or claim v1.7 stable."
        ),
    }


def _render_text(report: dict[str, object]) -> str:
    lines = [
        f"ClipVault field-test readiness: {report['repo']} Issue #{ISSUE_NUMBER}",
        f"status: {report['status']} (blocked={report['blocked']}, warnings={report['warnings']})",
        f"source_version: {report['source_version']}",
        f"main_sha: {report.get('main_sha') or 'unknown'}",
        "",
        "Gates:",
    ]
    for gate in report["gates"]:
        assert isinstance(gate, dict)
        prefix = {"pass": "[x]", "blocked": "[ ]", "warn": "[!]"}[str(gate["status"])]
        lines.append(f"- {prefix} {gate['name']}: {gate['detail']}")
        if gate.get("evidence"):
            lines.append(f"  evidence: {gate['evidence']}")
        if gate.get("next_step"):
            lines.append(f"  next: {gate['next_step']}")
        metadata = gate.get("metadata")
        if isinstance(metadata, dict):
            artifacts = metadata.get("artifacts")
            if isinstance(artifacts, list) and artifacts:
                lines.append("  artifacts:")
                for artifact in artifacts:
                    if not isinstance(artifact, dict):
                        continue
                    lines.append(
                        "    - "
                        f"{artifact.get('name')} "
                        f"expired={artifact.get('expired')} "
                        f"size={artifact.get('size_in_bytes')} "
                        f"expires_at={artifact.get('expires_at')} "
                        f"digest={artifact.get('digest')}"
                    )
            unchecked_items = metadata.get("unchecked_items")
            if isinstance(unchecked_items, list) and unchecked_items:
                lines.append("  unchecked:")
                for item in unchecked_items:
                    lines.append(f"    - {item}")
    lines.extend(["", str(report["scope_note"])])
    return "\n".join(lines) + "\n"


def _statuses(gates: Iterable[dict[str, object]]) -> set[str]:
    return {str(gate["status"]) for gate in gates}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Issue #82 field-test readiness report.")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--source-version", default=DEFAULT_SOURCE_VERSION)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="return exit code 0 even when field-test gates are blocked",
    )
    args = parser.parse_args(argv)

    report = build_report(repo=args.repo, branch=args.branch, source_version=args.source_version)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_render_text(report), end="")

    statuses = _statuses(report["gates"])
    if args.no_fail:
        return 0
    return 0 if statuses == {"pass"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
