#!/usr/bin/env python3
"""Read-only readiness report for the Issue #36 v1.6.0 release gate.

The script intentionally does not trigger workflows, set secrets, create
releases, upload artifacts, or close issues. It only reads GitHub state through
the `gh` CLI and reports which release-gate evidence is present or missing.
"""

from __future__ import annotations

import argparse
import hmac
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Iterable

DEFAULT_REPO = "selinyi123/clipvault-personal"
DEFAULT_VERSION = "v1.6.0"
DEFAULT_BRANCH = "main"
ISSUE_NUMBER = 36
REQUIRED_RELEASE_ENV_SECRETS = {
    "ANDROID_RELEASE_KEYSTORE_B64",
    "ANDROID_RELEASE_KEYSTORE_PASSWORD",
    "ANDROID_RELEASE_KEY_ALIAS",
    "ANDROID_RELEASE_KEY_PASSWORD",
}
REQUIRED_RELEASE_ENV_VARIABLE = "ANDROID_RELEASE_CERT_SHA256"
APPROVED_ANDROID_RELEASE_CERT_SHA256 = (
    "ef93502c8e5e68f1d0c8b46c36c521b84a09b11be8bc924030b5ada16d761757"
)
ANDROID_CERT_SHA256_RE = re.compile(r"[0-9a-f]{64}")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
CHECKLIST_ITEM_RE = re.compile(r"(?m)^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<text>.+?)\s*$")

READ_ONLY_GH_SUBCOMMANDS = {
    ("api",),
    ("issue", "view"),
    ("release", "view"),
    ("run", "list"),
    ("secret", "list"),
    ("variable", "list"),
}
WRITE_CAPABLE_GH_API_FLAGS = {
    "-X",
    "--method",
    "-F",
    "--field",
    "-f",
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


def _assert_read_only_gh(args: list[str]) -> None:
    if not args or args[0] != "gh":
        raise ValueError("release readiness runner only supports gh commands")
    if len(args) < 2:
        raise ValueError("missing gh subcommand")

    if args[1] == "api":
        for arg in args[2:]:
            flag = arg.split("=", 1)[0]
            if flag in WRITE_CAPABLE_GH_API_FLAGS:
                raise ValueError(
                    f"refusing write-capable gh api flag in read-only release readiness check: {arg}"
                )

    command = tuple(args[1:3])
    if command in READ_ONLY_GH_SUBCOMMANDS:
        return
    command = tuple(args[1:2])
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


def fetch_main_sha(runner: Runner, repo: str, branch: str) -> tuple[str | None, Gate]:
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
) -> Gate:
    if sha is None:
        return _blocked(
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
        return _blocked(
            workflow,
            f"Could not read {workflow} workflow runs.",
            evidence=str(exc),
            next_step="Inspect GitHub Actions permissions or workflow name.",
        )
    run = latest_success_for_sha(runs, sha)
    if run is None:
        return _blocked(
            workflow,
            f"No completed successful {workflow} run found for {branch} SHA {sha}.",
            next_step=f"Wait for or rerun {workflow} on {branch}, then record the run URL on Issue #{ISSUE_NUMBER}.",
        )
    return _pass(
        workflow,
        f"Successful {workflow} run targets current {branch} SHA {sha}.",
        evidence=str(run.get("url", "")),
    )


def check_release_environment(runner: Runner, repo: str) -> tuple[bool, Gate]:
    try:
        data = _run_json(
            runner,
            ["gh", "api", f"repos/{repo}/environments"],
            "release environments",
        )
    except ValueError as exc:
        return False, _blocked(
            "release environment",
            "Could not read repository environments.",
            evidence=str(exc),
            next_step="Confirm gh authentication and repository admin visibility.",
        )
    environments = data.get("environments", []) if isinstance(data, dict) else []
    names = {
        env.get("name")
        for env in environments
        if isinstance(env, dict) and isinstance(env.get("name"), str)
    }
    if "release" not in names:
        return False, _blocked(
            "release environment",
            "GitHub environment `release` is not present.",
            next_step="Owner must create/configure the protected `release` environment.",
        )
    return True, _pass("release environment", "GitHub environment `release` exists.")


def check_release_environment_secrets(runner: Runner, repo: str, env_exists: bool) -> Gate:
    if not env_exists:
        return _blocked(
            "release environment secrets",
            "Skipped because GitHub environment `release` is missing.",
            next_step="Create the `release` environment, then add Android signing environment secrets.",
        )
    try:
        data = _run_json(
            runner,
            [
                "gh",
                "secret",
                "list",
                "--repo",
                repo,
                "--env",
                "release",
                "--json",
                "name,updatedAt",
            ],
            "release environment secrets",
        )
    except ValueError as exc:
        return _blocked(
            "release environment secrets",
            "Could not list `release` environment secret names.",
            evidence=str(exc),
            next_step="Owner must confirm release-environment secret visibility and configuration.",
        )
    names = {
        item.get("name")
        for item in data
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    } if isinstance(data, list) else set()
    missing = sorted(REQUIRED_RELEASE_ENV_SECRETS - names)
    if missing:
        return _blocked(
            "release environment secrets",
            "Missing required Android signing environment secret names: " + ", ".join(missing),
            next_step="Owner must set the missing secrets with `gh secret set --env release`.",
        )
    return _pass(
        "release environment secrets",
        "All required Android signing environment secret names are present. Secret values are not readable by design.",
    )


def check_release_environment_signer_variable(
    runner: Runner,
    repo: str,
    env_exists: bool,
) -> Gate:
    gate_name = "release environment Owner certificate"
    if not env_exists:
        return _blocked(
            gate_name,
            "Skipped because GitHub environment `release` is missing.",
            next_step=(
                "Create the `release` environment, then configure the non-secret "
                f"{REQUIRED_RELEASE_ENV_VARIABLE} trust anchor."
            ),
        )
    try:
        data = _run_json(
            runner,
            [
                "gh",
                "variable",
                "list",
                "--repo",
                repo,
                "--env",
                "release",
                "--json",
                "name,value,updatedAt",
            ],
            gate_name,
        )
    except ValueError as exc:
        return _blocked(
            gate_name,
            "Could not list `release` environment variables.",
            evidence=str(exc),
            next_step="Owner must confirm release-environment variable visibility and configuration.",
        )
    rows = [
        item
        for item in data
        if isinstance(item, dict) and item.get("name") == REQUIRED_RELEASE_ENV_VARIABLE
    ] if isinstance(data, list) else []
    if len(rows) != 1:
        return _blocked(
            gate_name,
            f"Required non-secret environment variable {REQUIRED_RELEASE_ENV_VARIABLE} is missing.",
            next_step=(
                "Owner must independently confirm the long-lived Android release certificate "
                "and configure its canonical SHA-256 in the `release` environment."
            ),
        )
    value = rows[0].get("value")
    if not isinstance(value, str) or ANDROID_CERT_SHA256_RE.fullmatch(value) is None:
        return _blocked(
            gate_name,
            f"{REQUIRED_RELEASE_ENV_VARIABLE} is present but is not canonical 64-character lowercase hex.",
            next_step="Owner must correct the public certificate trust anchor before any signed release run.",
            metadata={"updated_at": str(rows[0].get("updatedAt") or "")},
        )
    if not hmac.compare_digest(value, APPROVED_ANDROID_RELEASE_CERT_SHA256):
        return _blocked(
            gate_name,
            f"{REQUIRED_RELEASE_ENV_VARIABLE} does not match the approved v1.6.0 trust anchor.",
            next_step="Owner must update the public certificate trust anchor before any signed release run.",
            metadata={"updated_at": str(rows[0].get("updatedAt") or "")},
        )
    return _pass(
        gate_name,
        "The Owner certificate trust-anchor variable matches the approved v1.6.0 signer; its value is not printed.",
        metadata={"updated_at": str(rows[0].get("updatedAt") or "")},
    )


def check_release_artifact_run(
    runner: Runner,
    *,
    repo: str,
    branch: str,
    version: str,
    sha: str | None,
) -> Gate:
    if sha is None:
        return _blocked(
            "signed release artifact workflow",
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
                "Release artifact build",
                "--branch",
                branch,
                "--limit",
                "10",
                "--json",
                "databaseId,status,conclusion,headSha,url,event,createdAt,displayTitle",
            ],
            "Release artifact build",
        )
    except ValueError as exc:
        return _blocked(
            "signed release artifact workflow",
            "Could not read Release artifact build workflow runs.",
            evidence=str(exc),
            next_step="Confirm the workflow is enabled and visible.",
        )
    run = latest_success_for_sha(runs, sha)
    if run is None:
        return _blocked(
            "signed release artifact workflow",
            f"No successful Release artifact build run found for current {branch} SHA {sha}.",
            next_step=(
                "After release environment/secrets exist, Owner runs Release artifact build "
                f"on `{branch}` with version={version} and create_draft_release=false."
            ),
        )
    if run.get("event") != "workflow_dispatch":
        return _blocked(
            "signed release artifact workflow",
            "Successful Release artifact build run did not come from workflow_dispatch.",
            evidence=str(run.get("url", "")),
            next_step="Use the Owner-controlled manual release workflow path from the v1.6.0 runbook.",
        )

    expected_titles = {
        f"Release artifacts {version} from {branch} draft=false",
        f"Release artifacts {version} from {branch} draft=true",
    }
    display_title = str(run.get("displayTitle") or "")
    if display_title not in expected_titles:
        return _blocked(
            "signed release artifact workflow",
            (
                f"Successful Release artifact build run exists for current {branch}, "
                "but its displayed dispatch inputs do not prove the expected "
                f"{version} / {branch} release run."
            ),
            evidence=str(run.get("url", "")),
            next_step=(
                "Run the current workflow from `main`; its run name must be "
                f"`Release artifacts {version} from {branch} draft=false` for "
                "the pre-publication artifact pass, or `draft=true` for the "
                "Owner-approved draft-release pass."
            ),
            metadata={
                "display_title": display_title,
                "expected_display_titles": sorted(expected_titles),
            },
        )
    return _warn(
        "signed release artifact workflow",
        (
            f"A successful Release artifact build run exists for current {branch} "
            "with matching displayed dispatch inputs, but this script cannot verify "
            "downloaded artifact contents."
        ),
        evidence=str(run.get("url", "")),
        next_step="Inspect artifacts for ANDROID_APKSIGNER_VERIFY.txt, SHA256SUMS.txt, and RELEASE_MANIFEST.json with signed=true.",
        metadata={"display_title": display_title},
    )


def _resolve_release_tag_commit(runner: Runner, *, repo: str, version: str) -> str:
    """Resolve one exact lightweight or annotated release tag to a commit."""

    exact_ref = f"refs/tags/{version}"
    records = _run_json(
        runner,
        ["gh", "api", f"repos/{repo}/git/matching-refs/tags/{version}"],
        "release tag ref lookup",
    )
    if not isinstance(records, list):
        raise ValueError("release tag ref lookup did not return a JSON array")
    exact = [
        row
        for row in records
        if isinstance(row, dict) and row.get("ref") == exact_ref
    ]
    if len(exact) != 1:
        raise ValueError(f"expected exactly one {exact_ref} ref, found {len(exact)}")

    obj = exact[0].get("object")
    if not isinstance(obj, dict):
        raise ValueError("release tag ref is missing its Git object")
    seen: set[str] = set()
    for _ in range(8):
        object_type = obj.get("type")
        object_sha = obj.get("sha")
        if not isinstance(object_sha, str) or GIT_SHA_RE.fullmatch(object_sha) is None:
            raise ValueError("release tag object has an invalid Git SHA")
        if object_type == "commit":
            return object_sha
        if object_type != "tag":
            raise ValueError("release tag does not resolve to a commit")
        if object_sha in seen:
            raise ValueError("release tag contains an object cycle")
        seen.add(object_sha)
        tag_record = _run_json(
            runner,
            ["gh", "api", f"repos/{repo}/git/tags/{object_sha}"],
            "annotated release tag lookup",
        )
        if not isinstance(tag_record, dict) or not isinstance(tag_record.get("object"), dict):
            raise ValueError("annotated release tag is missing its target object")
        obj = tag_record["object"]
    raise ValueError("release tag annotation chain is too deep")


def check_release_publication(
    runner: Runner,
    repo: str,
    version: str,
    *,
    sha: str | None = None,
) -> Gate:
    gate_name = "GitHub Release publication"
    if sha is None:
        return _blocked(
            gate_name,
            "Skipped because the current main SHA is unknown.",
            next_step="Fix the current main commit check first.",
        )
    result = runner([
        "gh",
        "release",
        "view",
        version,
        "--repo",
        repo,
        "--json",
        "tagName,name,isDraft,isPrerelease,publishedAt,url,targetCommitish",
    ])
    if result.returncode != 0:
        return _blocked(
            gate_name,
            f"GitHub Release {version} is not present.",
            evidence=result.stderr.strip() or result.stdout.strip(),
            next_step=f"Only after signed artifacts and manual QA are recorded, Owner may create/review/publish {version}.",
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return _blocked(
            gate_name,
            f"GitHub Release {version} lookup did not return valid JSON.",
            next_step="Inspect `gh release view` output manually.",
        )
    if not isinstance(data, dict):
        return _blocked(
            gate_name,
            f"GitHub Release {version} lookup did not return a JSON object.",
            next_step="Inspect `gh release view` output manually.",
        )

    expected_title = f"ClipVault Personal {version}"
    mismatches: list[str] = []
    if data.get("tagName") != version:
        mismatches.append("tag")
    if data.get("name") != expected_title:
        mismatches.append("title")
    if data.get("isPrerelease") is not False:
        mismatches.append("prerelease state")
    if data.get("targetCommitish") != sha:
        mismatches.append("target commit")
    if not isinstance(data.get("isDraft"), bool):
        mismatches.append("draft state")
    if mismatches:
        return _blocked(
            gate_name,
            (
                f"GitHub Release {version} does not match the exact publication identity: "
                f"{', '.join(mismatches)}."
            ),
            evidence=str(data.get("url", "")),
            next_step=(
                f"Use the Owner-controlled {version} draft targeting exact main SHA {sha}; "
                f"the title must be `{expected_title}` and prerelease must be false."
            ),
            metadata={"expected_tag_commit": sha, "expected_title": expected_title},
        )
    if data["isDraft"]:
        return _warn(
            gate_name,
            f"Exact GitHub Release {version} exists as a non-prerelease draft for current main.",
            evidence=str(data.get("url", "")),
            next_step=f"Do not publish until Issue #{ISSUE_NUMBER} has signed-artifact and manual-QA evidence.",
            metadata={"target_commit": sha, "title": expected_title},
        )

    published_at = data.get("publishedAt")
    if not isinstance(published_at, str) or not published_at.strip():
        return _blocked(
            gate_name,
            f"GitHub Release {version} is non-draft but has no publication timestamp.",
            evidence=str(data.get("url", "")),
            next_step="Inspect the live Release state before treating it as published.",
        )
    try:
        tag_commit = _resolve_release_tag_commit(runner, repo=repo, version=version)
    except ValueError as exc:
        return _blocked(
            gate_name,
            f"GitHub Release {version} tag identity could not be verified.",
            evidence=str(exc),
            next_step=f"Restore exactly one refs/tags/{version} ref resolving to current main SHA {sha}.",
        )
    if tag_commit != sha:
        return _blocked(
            gate_name,
            f"GitHub Release {version} tag resolves to {tag_commit}, not current main SHA {sha}.",
            evidence=str(data.get("url", "")),
            next_step="Do not treat this Release as the v1.6.0 publication; investigate the moved or incorrect tag.",
            metadata={"tag_commit": tag_commit, "expected_tag_commit": sha},
        )
    return _pass(
        gate_name,
        f"GitHub Release {version} is published with exact identity and resolves to current main SHA {sha}.",
        evidence=str(data.get("url", "")),
        metadata={
            "published_at": published_at,
            "tag_commit": tag_commit,
            "target_commit": sha,
            "title": expected_title,
        },
    )


def check_issue_state(runner: Runner, repo: str) -> Gate:
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
                "number,state,title,url,body",
            ],
            f"Issue #{ISSUE_NUMBER}",
        )
    except ValueError as exc:
        return _blocked(
            f"Issue #{ISSUE_NUMBER}",
            f"Could not read Issue #{ISSUE_NUMBER}.",
            evidence=str(exc),
            next_step="Confirm gh authentication and issue visibility.",
        )
    if not isinstance(data, dict):
        return _blocked(
            f"Issue #{ISSUE_NUMBER}",
            f"Issue #{ISSUE_NUMBER} lookup returned unexpected JSON.",
            next_step="Inspect issue state manually.",
        )
    state = data.get("state")
    body = data.get("body", "")
    checklist_items = parse_issue_checklist(body if isinstance(body, str) else "")
    checklist_metadata = issue_checklist_metadata(checklist_items)
    unchecked = int(checklist_metadata["unchecked_count"])
    if state != "OPEN":
        return _warn(
            f"Issue #{ISSUE_NUMBER}",
            f"Issue #{ISSUE_NUMBER} is {state}; expected OPEN until all release evidence is recorded.",
            evidence=str(data.get("url", "")),
            next_step="Reopen or verify every owner-controlled release gate before treating v1.6.0 as stable.",
            metadata=checklist_metadata,
        )
    if not checklist_items:
        return _blocked(
            f"Issue #{ISSUE_NUMBER}",
            f"Issue #{ISSUE_NUMBER} body has no GitHub task-list checklist items.",
            evidence=str(data.get("url", "")),
            next_step="Restore explicit release-gate checklist items before treating the issue as closable.",
            metadata=checklist_metadata,
        )
    if unchecked:
        return _blocked(
            f"Issue #{ISSUE_NUMBER}",
            f"Issue #{ISSUE_NUMBER} is open with {unchecked} unchecked release-gate checklist items.",
            evidence=str(data.get("url", "")),
            next_step="Record real evidence for each unchecked item before closing.",
            metadata=checklist_metadata,
        )
    return _pass(
        f"Issue #{ISSUE_NUMBER}",
        f"Issue #{ISSUE_NUMBER} is open and its body has no unchecked checklist items.",
        evidence=str(data.get("url", "")),
        metadata=checklist_metadata,
    )


def build_report(
    *,
    runner: Runner = run_command,
    repo: str = DEFAULT_REPO,
    version: str = DEFAULT_VERSION,
    branch: str = DEFAULT_BRANCH,
) -> dict[str, object]:
    main_sha, main_gate = fetch_main_sha(runner, repo, branch)
    gates: list[Gate] = [
        main_gate,
        check_workflow_success(
            runner,
            repo=repo,
            branch=branch,
            workflow="CI",
            sha=main_sha,
        ),
        check_workflow_success(
            runner,
            repo=repo,
            branch=branch,
            workflow="Release candidate dry run",
            sha=main_sha,
        ),
    ]
    env_exists, env_gate = check_release_environment(runner, repo)
    gates.append(env_gate)
    gates.append(check_release_environment_secrets(runner, repo, env_exists))
    gates.append(check_release_environment_signer_variable(runner, repo, env_exists))
    gates.append(check_release_artifact_run(runner, repo=repo, branch=branch, version=version, sha=main_sha))
    gates.append(check_release_publication(runner, repo, version, sha=main_sha))
    gates.append(check_issue_state(runner, repo))

    blocked = sum(1 for gate in gates if gate.status == "blocked")
    warnings = sum(1 for gate in gates if gate.status == "warn")
    status = "ready" if blocked == 0 and warnings == 0 else "blocked"
    return {
        "repo": repo,
        "version": version,
        "branch": branch,
        "main_sha": main_sha,
        "status": status,
        "blocked": blocked,
        "warnings": warnings,
        "gates": [gate.as_dict() for gate in gates],
        "scope_note": (
            "Read-only report. It does not trigger workflows, read secret values, "
            "verify downloaded artifacts, complete manual QA, create releases, or close Issue #36."
        ),
    }


def _render_text(report: dict[str, object]) -> str:
    lines = [
        f"ClipVault release readiness: {report['repo']} {report['version']}",
        f"status: {report['status']} (blocked={report['blocked']}, warnings={report['warnings']})",
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
            unchecked_items = metadata.get("unchecked_items")
            if isinstance(unchecked_items, list) and unchecked_items:
                lines.append("  unchecked:")
                for item in unchecked_items:
                    lines.append(f"    - {item}")
    lines.extend(["", str(report["scope_note"])])
    return "\n".join(lines) + "\n"


def _statuses(gates: Iterable[dict[str, object]]) -> set[str]:
    return {gate["status"] for gate in gates}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Issue #36 release readiness report.")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="return exit code 0 even when release gates are blocked",
    )
    args = parser.parse_args(argv)

    report = build_report(repo=args.repo, version=args.version, branch=args.branch)
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
