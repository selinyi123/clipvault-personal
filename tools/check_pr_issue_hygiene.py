"""Guard GitHub event text against protected release-gate issue auto-close.

GitHub interprets closing keywords in a pull request description when the pull
request targets the default branch. Release-gate issues such as #36 and #82 must
remain Owner-controlled, so this checker fails when those protected references
appear shortly after one of GitHub's auto-close keywords.

The checker is intentionally narrow: it is meant for PR/push metadata hygiene in
CI, not for rewriting project documentation or deciding release readiness.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_PROTECTED_ISSUES = (36, 82)
GITHUB_REPO = "selinyi123/clipvault-personal"

_KEYWORDS = (
    "close",
    "closes",
    "closed",
    "fix",
    "fixes",
    "fixed",
    "resolve",
    "resolves",
    "resolved",
)


@dataclass(frozen=True)
class Finding:
    line: int
    issue: int
    keyword: str
    snippet: str


def _issue_reference_pattern(issue: int) -> str:
    escaped_repo = re.escape(GITHUB_REPO)
    return (
        rf"(?:"
        rf"#\s*{issue}\b"
        rf"|{escaped_repo}\s*#\s*{issue}\b"
        rf"|https://github\.com/{escaped_repo}/issues/{issue}\b"
        rf"|Issue\s+#\s*{issue}\b"
        rf"|issue\s+#\s*{issue}\b"
        rf")"
    )


def _any_issue_reference_pattern() -> str:
    escaped_repo = re.escape(GITHUB_REPO)
    return (
        rf"(?:"
        rf"#\s*\d+\b"
        rf"|{escaped_repo}\s*#\s*\d+\b"
        rf"|https://github\.com/{escaped_repo}/issues/\d+\b"
        rf"|Issue\s+#\s*\d+\b"
        rf"|issue\s+#\s*\d+\b"
        rf")"
    )


def _danger_pattern(issue: int) -> re.Pattern[str]:
    keyword_pattern = "|".join(re.escape(keyword) for keyword in _KEYWORDS)
    any_issue_pattern = _any_issue_reference_pattern()
    issue_pattern = _issue_reference_pattern(issue)
    issue_separator = r"(?:[\t ]*(?:,[\t ]*(?:and|or)?|/|and|or)[\t ]*)"
    return re.compile(
        rf"\b(?P<keyword>{keyword_pattern})\b"
        rf"(?:[\t ]|:)*"
        rf"(?:{any_issue_pattern}{issue_separator})*?"
        rf"(?P<issue>{issue_pattern})",
        re.IGNORECASE,
    )


def _line_number(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _compact_snippet(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip()
    if len(collapsed) <= 96:
        return collapsed
    return f"{collapsed[:93]}..."


def find_dangerous_references(
    text: str,
    *,
    protected_issues: Iterable[int] = DEFAULT_PROTECTED_ISSUES,
) -> list[Finding]:
    findings: list[Finding] = []
    for issue in protected_issues:
        pattern = _danger_pattern(issue)
        for match in pattern.finditer(text):
            findings.append(
                Finding(
                    line=_line_number(text, match.start()),
                    issue=issue,
                    keyword=match.group("keyword").lower(),
                    snippet=_compact_snippet(match.group(0)),
                )
            )
    return sorted(findings, key=lambda finding: (finding.line, finding.issue, finding.keyword))


def load_body_from_event(path: Path) -> str:
    event = json.loads(path.read_text(encoding="utf-8"))
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        return ""
    body = pull_request.get("body")
    if body is None:
        return ""
    if not isinstance(body, str):
        raise ValueError("pull_request.body must be a string when present")
    return body


def _append_string(values: list[str], value: object, *, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string when present")
    if value:
        values.append(value)


def load_text_from_event(path: Path) -> str:
    event = json.loads(path.read_text(encoding="utf-8"))
    values: list[str] = []

    pull_request = event.get("pull_request")
    if isinstance(pull_request, dict):
        _append_string(values, pull_request.get("title"), field="pull_request.title")
        _append_string(values, pull_request.get("body"), field="pull_request.body")

    head_commit = event.get("head_commit")
    if isinstance(head_commit, dict):
        _append_string(values, head_commit.get("message"), field="head_commit.message")

    commits = event.get("commits")
    if isinstance(commits, list):
        for index, commit in enumerate(commits):
            if isinstance(commit, dict):
                _append_string(values, commit.get("message"), field=f"commits[{index}].message")

    return "\n\n".join(values)


def _read_body(args: argparse.Namespace) -> str:
    sources = [
        args.body is not None,
        args.body_file is not None,
        args.event_path is not None,
    ]
    if sum(sources) > 1:
        raise ValueError("choose only one of --body, --body-file, or --event-path")

    if args.body is not None:
        return args.body
    if args.body_file is not None:
        return Path(args.body_file).read_text(encoding="utf-8")
    if args.event_path is not None:
        return load_text_from_event(Path(args.event_path))

    env_event_path = os.environ.get("GITHUB_EVENT_PATH")
    if env_event_path:
        return load_text_from_event(Path(env_event_path))
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail when GitHub event text can auto-close protected release-gate issues.",
    )
    parser.add_argument("--body", help="PR body text to check")
    parser.add_argument("--body-file", help="path to a PR body text file")
    parser.add_argument("--event-path", help="path to a GitHub pull_request event JSON file")
    parser.add_argument(
        "--protected-issue",
        type=int,
        action="append",
        dest="protected_issues",
        help="protected issue number; defaults to 36 and 82",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        body = _read_body(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    protected_issues = args.protected_issues or list(DEFAULT_PROTECTED_ISSUES)
    findings = find_dangerous_references(body, protected_issues=protected_issues)
    if not findings:
        print(
            "PR issue hygiene passed: no auto-close keyword patterns found for "
            f"protected issues {', '.join(f'#{issue}' for issue in protected_issues)}."
        )
        return 0

    print("PR issue hygiene failed: protected release-gate issue references are at risk.")
    for finding in findings:
        print(
            f"- line {finding.line}: keyword {finding.keyword!r} appears before "
            f"protected issue #{finding.issue}: {finding.snippet!r}"
        )
    print(
        "Use neutral wording such as 'Issue #36 remains open' or "
        "'does not change issue state for #36 or #82'."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
