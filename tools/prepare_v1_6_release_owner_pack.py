#!/usr/bin/env python3
"""Prepare a local Owner action pack for ClipVault Issue #36.

This script writes a local coordination folder only. It does not call GitHub,
trigger workflows, configure repository settings, download artifacts, run device
QA, sign APKs, publish releases, or close Issue #36.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERSION = "v1.6.0"
ISSUE_URL = "https://github.com/selinyi123/clipvault-personal/issues/36"
ANDROID_SIGNING_VARIABLE_NAMES = [
    "ANDROID_RELEASE_KEYSTORE_B64",
    "ANDROID_RELEASE_KEYSTORE_PASSWORD",
    "ANDROID_RELEASE_KEY_ALIAS",
    "ANDROID_RELEASE_KEY_PASSWORD",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def manual_qa_template(version: str) -> dict[str, Any]:
    return {
        "schema": "clipvault.issue36.manual_qa.v1",
        "version": version,
        "created_at": utc_now(),
        "target_commit": "",
        "tester": "",
        "device_matrix": {
            "windows": {"os": "", "artifact": "", "notes": ""},
            "android": {"model": "", "android_version": "", "apk": "", "notes": ""},
        },
        "android_device_qa": [
            {"id": "android.pairing", "status": "pending", "evidence": ""},
            {"id": "android.share_capture", "status": "pending", "evidence": ""},
            {"id": "android.qs_tile_capture", "status": "pending", "evidence": ""},
            {"id": "android.panel_ime_paste", "status": "pending", "evidence": ""},
        ],
        "ime_privacy_qa": [
            {"id": "ime.password_suppresses_candidates", "status": "pending", "evidence": ""},
            {"id": "ime.incognito_suppresses_candidates", "status": "pending", "evidence": ""},
            {"id": "ime.unknown_editor_fails_closed", "status": "pending", "evidence": ""},
            {"id": "ime.no_typed_text_logging_observed", "status": "pending", "evidence": ""},
        ],
        "sync_qa": [
            {"id": "sync.desktop_to_android_public_clip", "status": "pending", "evidence": ""},
            {"id": "sync.android_to_desktop_public_clip", "status": "pending", "evidence": ""},
            {"id": "sync.secret_clip_isolated", "status": "pending", "evidence": ""},
            {"id": "sync.secret_memory_isolated", "status": "pending", "evidence": ""},
        ],
        "windows_clipboard_privacy_qa": [
            {"id": "windows.normal_text_captured", "status": "pending", "evidence": ""},
            {"id": "windows.exclude_monitor_not_captured", "status": "pending", "evidence": ""},
            {"id": "windows.can_include_history_zero_not_captured", "status": "pending", "evidence": ""},
            {"id": "windows.can_upload_cloud_zero_not_captured", "status": "pending", "evidence": ""},
            {"id": "windows.no_clip_content_in_logs", "status": "pending", "evidence": ""},
        ],
        "redaction_policy": "Do not include private credentials, private clipboard content, or unredacted logs.",
    }


def artifact_template(version: str) -> dict[str, Any]:
    return {
        "schema": "clipvault.issue36.release_artifacts.v1",
        "version": version,
        "created_at": utc_now(),
        "target_commit": "",
        "release_workflow_run_url": "",
        "downloaded_artifacts_root": "",
        "windows": {
            "directory": "",
            "expected_assets": [
                "ClipVault-Setup-v1.6.0.exe",
                "ClipVault-Desktop-v1.6.0-portable.exe",
                "SHA256SUMS.txt",
                "RELEASE_MANIFEST.json",
            ],
            "verification": {
                "release_manifest_json": "pending",
                "sha256sums_txt": "pending",
                "required_assets_present": "pending",
                "checksums_match_downloaded_bytes": "pending",
            },
        },
        "android": {
            "directory": "",
            "expected_assets": [
                "ClipVault-Android-v1.6.0.apk",
                "ANDROID_APKSIGNER_VERIFY.txt",
                "SHA256SUMS.txt",
                "RELEASE_MANIFEST.json",
            ],
            "verification": {
                "release_manifest_json": "pending",
                "sha256sums_txt": "pending",
                "required_assets_present": "pending",
                "checksums_match_downloaded_bytes": "pending",
                "apksigner_verify": "pending",
            },
        },
        "notes": "A green release workflow run is not artifact-content proof until downloaded bytes are checked.",
    }


def owner_action_pack(version: str, issue_url: str) -> str:
    signing_names = "\n".join(f"   - `{name}`" for name in ANDROID_SIGNING_VARIABLE_NAMES)
    return f"""# Owner Release Action Pack — ClipVault {version}

Generated: {utc_now()}

Issue: {issue_url}

## 1. Purpose

This local folder coordinates Issue #36 evidence collection. It does not replace Owner-controlled GitHub environment setup, Android signing configuration, manual device QA, or release publication.

## 2. Command order

### Step A — read-only readiness

```powershell
python tools/release_readiness.py
```

Record current blockers before running any release workflow.

### Step B — Owner-only release environment

In GitHub repository settings:

1. Create or configure environment `release`.
2. Set the intended approval policy.
3. Add these Android signing variable names:
{signing_names}

Do not paste private values into issues, logs, commits, screenshots, or this pack.

### Step C — release artifact build

Run GitHub Actions workflow `Release artifact build` manually from `main` with:

```text
version={version}
create_draft_release=false
```

Download the resulting Windows and Android release artifact directories.

### Step D — artifact byte verification

Fill:

```text
release-artifacts-v1.6.0.template.json
```

Then run the existing artifact evidence helper if present:

```powershell
python tools/release_artifact_evidence.py --help
```

Use that tool's expected arguments to validate downloaded Windows and Android artifact directories.

### Step E — manual QA

Fill:

```text
manual-qa-v1.6.0.template.json
```

Then run the existing manual QA evidence helper if present:

```powershell
python tools/manual_qa_evidence.py --help
```

Required rows:

- Android pairing
- Android Share capture
- Android QS Tile capture
- Android Panel IME paste
- password/incognito/unknown-field candidate suppression
- no typed-text logging
- desktop <-> Android public sync
- secret/private isolation
- Windows clipboard exclusion-format privacy checks

### Step F — issue comment

Use `issue-36-comment-draft.md` as the final evidence skeleton. Replace every `PENDING` row with evidence URLs or validated local evidence summaries.

### Step G — release publication

Only after all evidence rows pass:

1. Create/review/publish GitHub Release `{version}`.
2. Attach Windows installer, Windows portable executable, signed Android APK, checksums, and manifests.
3. Add final Release URL to Issue #36.
4. Close Issue #36 only after Owner review.

## 3. Hard blockers

If any item below is missing, Issue #36 remains open:

- release environment or Android signing configuration missing;
- release artifact build not run on `main`;
- downloaded artifact bytes not verified;
- Android manual QA not recorded;
- IME privacy QA not recorded;
- sync QA not recorded;
- Windows clipboard privacy QA not recorded;
- GitHub Release `{version}` absent or not Owner-approved.
"""


def agent_cluster_markdown(version: str) -> str:
    return f"""# Agent Cluster Runtime Plan — Issue #36 / {version}

## Parallel wave 1

- Release Coordinator: prepare truth table.
- CI Evidence Agent: collect current-main CI and release-candidate dry-run evidence.
- Environment Agent: verify release environment shape. Private value handling remains Owner-only.

## Parallel wave 2

Requires release environment and Android signing configuration.

- Artifact Agent: run/verify final release artifacts.
- Windows QA Agent: prepare Windows smoke and clipboard privacy matrix.
- Android QA Agent: prepare Android device QA matrix.
- IME Privacy Agent: prepare IME field privacy cases.
- Sync QA Agent: prepare public/secret sync cases.

## Parallel wave 3

Requires signed/final artifacts.

- Windows QA Agent executes final Windows checks.
- Android QA Agent executes final Android checks.
- IME Privacy Agent executes privacy checks.
- Sync QA Agent executes sync checks.

## Final serial gate

- Release Coordinator merges evidence.
- Release Publisher creates/reviews/publishes GitHub Release only after Owner approval.
- Issue #36 remains open until the final Release URL and all evidence rows are recorded.
"""


def issue_comment_draft(version: str, issue_url: str) -> str:
    return f"""## Issue #36 release-gate evidence update

Target version: `{version}`
Issue: {issue_url}

### Evidence table

| Gate | Status | Evidence |
|---|---|---|
| Target commit | PENDING |  |
| Current-main CI | PENDING |  |
| Release-candidate dry run | PENDING |  |
| `release` environment approval policy | PENDING |  |
| Required Android signing variable names present | PENDING | values not disclosed |
| Release artifact build on `main` | PENDING |  |
| Windows downloaded artifact byte verification | PENDING |  |
| Android signed APK byte/signature verification | PENDING |  |
| Android device QA | PENDING |  |
| IME privacy QA | PENDING |  |
| Sync QA | PENDING |  |
| Windows clipboard privacy QA | PENDING |  |
| GitHub Release `{version}` | PENDING |  |

### Closure recommendation

`BLOCKED`

Reason: this draft is generated before Owner-controlled signing, downloaded artifact byte verification, manual QA, and final Release publication are recorded.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="release-owner-pack-v1.6.0")
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--issue-url", default=ISSUE_URL)
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    write_text(out / "OWNER_RELEASE_ACTION_PACK.md", owner_action_pack(args.version, args.issue_url))
    write_text(out / "agent-cluster.md", agent_cluster_markdown(args.version))
    write_text(out / "issue-36-comment-draft.md", issue_comment_draft(args.version, args.issue_url))
    write_json(out / "manual-qa-v1.6.0.template.json", manual_qa_template(args.version))
    write_json(out / "release-artifacts-v1.6.0.template.json", artifact_template(args.version))

    summary = {
        "schema": "clipvault.issue36.owner_pack.summary.v1",
        "version": args.version,
        "issue_url": args.issue_url,
        "created_at": utc_now(),
        "generated_files": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()),
        "does_not": [
            "call GitHub",
            "trigger workflows",
            "configure repository settings",
            "download artifacts",
            "run device QA",
            "sign APKs",
            "publish releases",
            "close Issue #36",
        ],
    }
    write_json(out / "pack-summary.json", summary)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
