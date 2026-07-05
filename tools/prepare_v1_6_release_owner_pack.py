#!/usr/bin/env python3
"""Generate the local Owner execution pack for Issue #36.

This helper is intentionally local-only. It creates release-gate templates and
issue-comment drafts for v1.6.0 evidence collection. It does not call GitHub,
trigger workflows, set secrets, download artifacts, run manual QA, sign APKs,
publish releases, or close Issue #36.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_VERSION = "v1.6.0"
DEFAULT_ISSUE_URL = "https://github.com/selinyi123/clipvault-personal/issues/36"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, data: dict[str, Any]) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def manual_qa_template(version: str) -> dict[str, Any]:
    return {
        "schema": "clipvault.issue36.manual_qa.v1",
        "version": version,
        "created_at": utc_now(),
        "target_commit": "REPLACE_WITH_40_HEX_MAIN_COMMIT",
        "tester": "REPLACE_WITH_TESTER_NAME",
        "tested_at": "REPLACE_WITH_ISO_8601_TIMESTAMP",
        "android_device": {
            "model": "REPLACE_WITH_DEVICE_MODEL",
            "android_version": "REPLACE_WITH_ANDROID_VERSION",
            "apk_source": "REPLACE_WITH_SIGNED_RELEASE_APK_OR_WORKFLOW_ARTIFACT",
        },
        "desktop_environment": {
            "os": "REPLACE_WITH_WINDOWS_VERSION",
            "build_source": "REPLACE_WITH_FINAL_WINDOWS_ARTIFACT",
        },
        "sections": {
            "android_device_qa": {
                "pairing": blocked("Pair Android with desktop using a one-time pairing code."),
                "share_capture": blocked("Share text from another app into ClipVault and confirm local capture."),
                "qs_tile_capture": blocked("Use Quick Settings tile to explicitly save current clipboard content."),
                "panel_ime_paste": blocked("Enable ClipVault Panel IME and confirm a candidate tap commits text."),
            },
            "ime_privacy_qa": {
                "password_suppresses_candidates": blocked("Password fields suppress candidates."),
                "incognito_suppresses_candidates": blocked("Incognito/private/no-suggestions fields suppress candidates."),
                "unknown_editor_fails_closed": blocked("Unknown EditorInfo behavior is fail-closed if tested."),
                "typed_text_not_logged": blocked("Typed text is not written to Room, outbox, logs, sync payloads, or desktop storage."),
            },
            "sync_qa": {
                "desktop_to_android_public_clip": blocked("Public clip syncs desktop -> Android."),
                "android_to_desktop_public_clip": blocked("Public clip syncs Android -> desktop."),
                "secret_clip_isolated": blocked("Secret clip remains local/quarantined and does not sync."),
                "secret_memory_isolated": blocked("Secret/private memory remains isolated."),
            },
            "windows_clipboard_privacy_qa": {
                "normal_text_captured": blocked("Normal text clipboard item is captured."),
                "exclude_monitor_not_captured": blocked("ExcludeClipboardContentFromMonitorProcessing prevents capture."),
                "viewer_ignore_not_captured": blocked("Clipboard Viewer Ignore prevents capture."),
                "history_zero_not_captured": blocked("CanIncludeInClipboardHistory=0 prevents capture."),
                "cloud_zero_not_captured": blocked("CanUploadToCloudClipboard=0 prevents capture."),
                "no_clip_content_in_logs": blocked("Logs contain IDs/hashes/lengths only, not clip bodies."),
            },
        },
        "redaction_policy": "Do not include raw secrets, private clipboard contents, bearer tokens, signing material, or unredacted logs.",
    }


def blocked(notes: str) -> dict[str, str]:
    return {
        "status": "blocked",
        "evidence": "",
        "next_step": "Run the manual check and replace status with pass/fail.",
        "notes": notes,
    }


def artifact_template(version: str) -> dict[str, Any]:
    return {
        "schema": "clipvault.issue36.release_artifacts.v1",
        "version": version,
        "created_at": utc_now(),
        "target_commit": "REPLACE_WITH_40_HEX_MAIN_COMMIT",
        "release_workflow_run_url": "REPLACE_WITH_GITHUB_ACTIONS_RUN_URL",
        "downloaded_artifacts_root": "REPLACE_WITH_LOCAL_ARTIFACT_ROOT",
        "windows": {
            "directory": "REPLACE_WITH_WINDOWS_ARTIFACT_DIR",
            "expected_assets": [
                "ClipVault-Setup-v1.6.0.exe",
                "ClipVault-Desktop-v1.6.0-portable.exe",
                "SHA256SUMS.txt",
                "RELEASE_MANIFEST.json",
            ],
            "verification": {
                "release_manifest_json": "blocked",
                "sha256sums_txt": "blocked",
                "required_assets_present": "blocked",
                "checksums_match_downloaded_bytes": "blocked",
            },
        },
        "android": {
            "directory": "REPLACE_WITH_ANDROID_ARTIFACT_DIR",
            "expected_assets": [
                "ClipVault-Android-v1.6.0.apk",
                "ANDROID_APKSIGNER_VERIFY.txt",
                "SHA256SUMS.txt",
                "RELEASE_MANIFEST.json",
            ],
            "verification": {
                "release_manifest_json": "blocked",
                "sha256sums_txt": "blocked",
                "required_assets_present": "blocked",
                "checksums_match_downloaded_bytes": "blocked",
                "apksigner_verify": "blocked",
            },
        },
        "notes": "A green workflow run is not artifact-content proof until downloaded bytes are checked.",
    }


def owner_action_pack(version: str, issue_url: str) -> str:
    return f"""# Owner Release Action Pack — ClipVault {version}

Generated: {utc_now()}
Issue: {issue_url}

## 1. Scope

This pack coordinates Issue #36 evidence collection. It does not replace Owner-controlled GitHub environment setup, signing secrets, manual device QA, or release publication.

## 2. Command order

### Step A — read-only readiness

```powershell
python tools/release_readiness.py
```

Record current blockers before running any release workflow.

### Step B — Owner-only release environment

In GitHub repository settings:

1. Create/configure environment `release`.
2. Set the intended approval policy.
3. Add environment secrets:
   - `ANDROID_RELEASE_KEYSTORE_B64`
   - `ANDROID_RELEASE_KEYSTORE_PASSWORD`
   - `ANDROID_RELEASE_KEY_ALIAS`
   - `ANDROID_RELEASE_KEY_PASSWORD`

Do not paste secret values into issues, logs, commits, screenshots, or this pack.

### Step C — release artifact build

Run GitHub Actions workflow `Release artifact build` manually from `main` with:

```text
version={version}
create_draft_release=false
```

Download the resulting Windows and Android release artifact directories.

### Step D — artifact byte verification

Fill `release-artifacts-v1.6.0.template.json`, then run the existing helper:

```powershell
python tools/release_artifact_evidence.py --help
```

Use the helper's expected arguments to validate downloaded Windows and Android artifact directories.

### Step E — manual QA

Fill `manual-qa-v1.6.0.template.json`, then run the existing helper:

```powershell
python tools/manual_qa_evidence.py --help
```

Required rows: Android pairing, Android Share capture, Android QS Tile capture, Android Panel IME paste, IME privacy, sync isolation, and Windows clipboard privacy.

### Step F — Issue #36 comment

Use `issue-36-comment-draft.md` as the evidence skeleton. Replace every `PENDING` row with evidence URLs or validated local evidence summaries.

### Step G — release publication

Only after all evidence rows pass:

1. Create/review/publish GitHub Release `{version}`.
2. Attach Windows installer, Windows portable executable, signed Android APK, checksums, and manifests.
3. Add final Release URL to Issue #36.
4. Close Issue #36 only after Owner review.

## 3. Hard blockers

If any item below is missing, Issue #36 remains open:

- release environment or signing secrets missing;
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
- Environment Agent: verify release environment shape. Secret value handling remains Owner-only.

## Parallel wave 2

Requires release environment and signing secrets.

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

| Gate | Status | Evidence |
|---|---|---|
| Target commit | PENDING |  |
| Current-main CI | PENDING |  |
| Release-candidate dry run | PENDING |  |
| `release` environment approval policy | PENDING |  |
| Required Android signing secret names present | PENDING | values not disclosed |
| Release artifact build on `main` | PENDING |  |
| Windows downloaded artifact byte verification | PENDING |  |
| Android signed APK byte/signature verification | PENDING |  |
| Android device QA | PENDING |  |
| IME privacy QA | PENDING |  |
| Sync QA | PENDING |  |
| Windows clipboard privacy QA | PENDING |  |
| GitHub Release `{version}` | PENDING |  |

Closure recommendation: `BLOCKED`

Reason: this draft is generated before Owner-controlled signing, downloaded artifact byte verification, manual QA, and final Release publication are recorded.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="release-owner-pack-v1.6.0")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--issue-url", default=DEFAULT_ISSUE_URL)
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
            "set secrets",
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
