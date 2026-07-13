#!/usr/bin/env python3
"""Prepare a local, fail-closed Owner action pack for Issue #36.

The pack is coordination material only. It does not call GitHub, read secret
values, trigger workflows, validate artifact provenance, run device QA, sign
artifacts, publish v1.6.0, or close Issue #36.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
VERSION = "v1.6.0"
ISSUE_URL = "https://github.com/selinyi123/clipvault-personal/issues/36"
DEFAULT_OUTPUT_DIR = Path(".field-test-artifacts") / "v1.6.0-owner-pack"
ANDROID_SIGNING_SECRET_NAMES = (
    "ANDROID_RELEASE_KEYSTORE_B64",
    "ANDROID_RELEASE_KEYSTORE_PASSWORD",
    "ANDROID_RELEASE_KEY_ALIAS",
    "ANDROID_RELEASE_KEY_PASSWORD",
)
EXPECTED_OUTPUT_FILES = (
    "OWNER_RELEASE_ACTION_PACK.md",
    "agent-cluster.md",
    "issue-36-comment-draft.md",
    "manual-qa-v1.6.0.template.json",
    "pack-summary.json",
    "release-artifacts-v1.6.0.template.json",
)


def _load_local_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


manual_qa_evidence = _load_local_module(
    "manual_qa_evidence_for_v1_6_owner_pack",
    "tools/manual_qa_evidence.py",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_scope(version: str, issue_url: str = ISSUE_URL) -> None:
    if version != VERSION:
        raise ValueError(f"Owner pack only supports {VERSION}")
    if issue_url != ISSUE_URL:
        raise ValueError(f"Owner pack only supports {ISSUE_URL}")


def manual_qa_template(version: str) -> dict[str, Any]:
    """Return the canonical schema-v2 template without copying its contract."""

    _validate_scope(version)
    return manual_qa_evidence.build_template(version)


def artifact_template(version: str) -> dict[str, Any]:
    """Return a coordination worksheet, not validator or release evidence."""

    _validate_scope(version)
    numeric = version.removeprefix("v")
    return {
        "schema": "clipvault.issue36.release_artifacts.coordination.v2",
        "version": version,
        "created_at": utc_now(),
        "coordination_only": True,
        "validator_input": False,
        "target_commit": "REPLACE_WITH_40_HEX_MAIN_COMMIT",
        "release_workflow_run_url": "REPLACE_WITH_EXACT_DRAFT_TRUE_RUN_URL",
        "draft_release_url": "REPLACE_WITH_DRAFT_RELEASE_URL",
        "windows": {
            "directory": "REPLACE_WITH_DOWNLOADED_WINDOWS_ARTIFACT_DIRECTORY",
            "expected_assets": [
                f"ClipVault-Desktop-v{numeric}-portable.exe",
                f"ClipVault-Setup-v{numeric}.exe",
                "SHA256SUMS.txt",
                "RELEASE_MANIFEST.json",
            ],
        },
        "android": {
            "directory": "REPLACE_WITH_DOWNLOADED_ANDROID_ARTIFACT_DIRECTORY",
            "expected_assets": [
                f"ClipVault-Android-{version}-release-signed.apk",
                "ANDROID_APKSIGNER_VERIFY.txt",
                "SHA256SUMS.txt",
                "RELEASE_MANIFEST.json",
            ],
            "owner_certificate_identity": "BLOCKED_UNTIL_ANDROID_RELEASE_CERT_SHA256_IS_CONFIGURED_AND_ENFORCED",
        },
        "notes": (
            "Coordination worksheet only. It is not accepted by a validator and does not prove "
            "GitHub run provenance, downloaded bytes, Owner signer identity, manual QA, or publication."
        ),
    }


def owner_action_pack(
    version: str,
    issue_url: str,
    *,
    generated_at: str | None = None,
) -> str:
    _validate_scope(version, issue_url)
    generated_at = generated_at or utc_now()
    secret_names = "\n".join(f"   - `{name}`" for name in ANDROID_SIGNING_SECRET_NAMES)
    return f"""# Owner Release Action Pack - ClipVault {version}

Generated: {generated_at}

Issue: {issue_url}

## 1. Scope and current blocker

This ignored local folder coordinates Issue #36 evidence collection. It does not
perform or prove Owner-controlled signing, artifact provenance, API 26/27 or
physical-device QA, publication approval, Release publication, or Issue closure.

The release workflow rejects a missing, malformed, multi-signer, or mismatched
APK certificate. The Android gate nevertheless remains **BLOCKED** until the
Owner configures the independently confirmed 64-hex
`ANDROID_RELEASE_CERT_SHA256` and a real workflow run proves that comparison.
A generic `apksigner --print-certs` text file is not proof by itself.

## 2. Fixed target and command order

### Step A - freeze one clean current-main target

```powershell
git fetch origin main
git status --short
$targetCommit = git rev-parse origin/main
```

Stop if the worktree is not clean. Record current-main CI and release-candidate
dry-run success for this exact `$targetCommit`; evidence from an older main SHA
does not satisfy Issue #36.

### Step B - Owner-only release environment

Create or configure GitHub environment `release`, define its Owner-approved
review policy, and add these **environment secrets**:

{secret_names}

Also configure the non-secret Owner certificate fingerprint. The workflow
requires this canonical lowercase value and compares it with the sole APK signer
before attestation or upload:

```text
ANDROID_RELEASE_CERT_SHA256=<64 lowercase hex characters confirmed independently by Owner>
```

Never place private values, keystore bytes, passwords, clipboard content, local
absolute paths, or unredacted logs in this pack, Git, screenshots, or Issue #36.

### Step C - signed no-draft preflight only

Run `Release artifact build` from the exact target on `main` with:

```text
version={version}
create_draft_release=false
```

This run uses the signing secrets and may produce a signed APK, but it does not
create the final draft. Treat it only as a non-final preflight; do not bind final
QA or publication approval to its bytes.

### Step D - create the final draft asset set

Only after signer identity and artifact provenance verification are fail-closed,
run the same workflow from the frozen target with:

```text
version={version}
create_draft_release=true
```

That run creates the draft Release and rebuilds the final assets. All subsequent
artifact evidence and manual QA must use the **same draft=true run**, exact target
commit, draft Release, and asset SHA-256 values. Publishing must not rebuild them.

### Step E - download and compare the exact run and mutable draft assets

`release-artifacts-v1.6.0.template.json` is a coordination worksheet only; never
post or treat it as validator evidence. Use a new directory scoped by run ID and
stop after every failed native command:

```powershell
$ErrorActionPreference = "Stop"
function Assert-NativeSuccess([string]$label) {{
  if ($LASTEXITCODE -ne 0) {{ throw "$label failed with exit code $LASTEXITCODE" }}
}}

$runId = "REPLACE_WITH_DRAFT_TRUE_RUN_ID"
$artifactRoot = ".field-test-artifacts/v1.6.0-draft-run-$runId"
if (Test-Path $artifactRoot) {{ throw "Refusing stale evidence directory: $artifactRoot" }}

$run = gh run view $runId `
  --repo selinyi123/clipvault-personal `
  --json displayTitle,event,headBranch,headSha,conclusion,url | ConvertFrom-Json
Assert-NativeSuccess "gh run view"
if ($run.displayTitle -ne "Release artifacts {version} from main draft=true" -or
    $run.event -ne "workflow_dispatch" -or $run.headBranch -ne "main" -or
    $run.headSha -ne $targetCommit -or $run.conclusion -ne "success") {{
  throw "Run is not the successful draft=true build for the frozen target"
}}

$actionsRoot = "$artifactRoot/actions"
$releaseRoot = "$artifactRoot/draft-release"
New-Item -ItemType Directory -Path $actionsRoot,$releaseRoot | Out-Null
gh run download $runId `
  --repo selinyi123/clipvault-personal `
  --name clipvault-windows-release-artifacts `
  --name clipvault-android-signed-release-artifacts `
  --dir $actionsRoot
Assert-NativeSuccess "gh run download"

$release = gh release view {version} `
  --repo selinyi123/clipvault-personal `
  --json tagName,name,isDraft,isPrerelease,targetCommitish,url,assets | ConvertFrom-Json
Assert-NativeSuccess "gh release view"
if ($release.tagName -ne "{version}" -or $release.name -ne "ClipVault Personal {version}" -or
    -not $release.isDraft -or $release.isPrerelease -or
    $release.targetCommitish -ne $targetCommit) {{
  throw "Draft Release metadata does not match the frozen target"
}}
$expectedAssets = @(
  "ANDROID_APKSIGNER_VERIFY.txt",
  "ClipVault-Android-{version}-release-signed.apk",
  "ClipVault-Desktop-v1.6.0-portable.exe",
  "ClipVault-Setup-v1.6.0.exe",
  "android-RELEASE_MANIFEST.json",
  "android-SHA256SUMS.txt",
  "windows-RELEASE_MANIFEST.json",
  "windows-SHA256SUMS.txt"
) | Sort-Object
$actualAssets = @($release.assets | ForEach-Object name | Sort-Object)
if (@(Compare-Object $expectedAssets $actualAssets).Count -ne 0) {{
  throw "Draft Release asset inventory mismatch"
}}
if (@($release.assets | Where-Object size -LE 0).Count -ne 0) {{
  throw "Draft Release contains an empty asset"
}}

gh release download {version} `
  --repo selinyi123/clipvault-personal `
  --dir $releaseRoot
Assert-NativeSuccess "gh release download"

function Assert-SameSha([string]$left, [string]$right) {{
  $leftHash = (Get-FileHash -Algorithm SHA256 $left).Hash
  $rightHash = (Get-FileHash -Algorithm SHA256 $right).Hash
  if ($leftHash -ne $rightHash) {{ throw "Draft/action byte mismatch: $right" }}
}}
Assert-SameSha "$actionsRoot/clipvault-windows-release-artifacts/ClipVault-Desktop-v1.6.0-portable.exe" "$releaseRoot/ClipVault-Desktop-v1.6.0-portable.exe"
Assert-SameSha "$actionsRoot/clipvault-windows-release-artifacts/ClipVault-Setup-v1.6.0.exe" "$releaseRoot/ClipVault-Setup-v1.6.0.exe"
Assert-SameSha "$actionsRoot/clipvault-windows-release-artifacts/SHA256SUMS.txt" "$releaseRoot/windows-SHA256SUMS.txt"
Assert-SameSha "$actionsRoot/clipvault-windows-release-artifacts/RELEASE_MANIFEST.json" "$releaseRoot/windows-RELEASE_MANIFEST.json"
Assert-SameSha "$actionsRoot/clipvault-android-signed-release-artifacts/ClipVault-Android-{version}-release-signed.apk" "$releaseRoot/ClipVault-Android-{version}-release-signed.apk"
Assert-SameSha "$actionsRoot/clipvault-android-signed-release-artifacts/ANDROID_APKSIGNER_VERIFY.txt" "$releaseRoot/ANDROID_APKSIGNER_VERIFY.txt"
Assert-SameSha "$actionsRoot/clipvault-android-signed-release-artifacts/SHA256SUMS.txt" "$releaseRoot/android-SHA256SUMS.txt"
Assert-SameSha "$actionsRoot/clipvault-android-signed-release-artifacts/RELEASE_MANIFEST.json" "$releaseRoot/android-RELEASE_MANIFEST.json"

$digestReport = "$artifactRoot/draft-release-SHA256SUMS.txt"
Get-ChildItem $releaseRoot -File | Sort-Object Name | ForEach-Object {{
  "{{0}}  {{1}}" -f (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant(), $_.Name
}} | Set-Content -Encoding ascii $digestReport

if ($env:ANDROID_RELEASE_CERT_SHA256 -cnotmatch '^[0-9a-f]{{64}}$') {{
  throw "Set the independently confirmed 64-lowercase-hex ANDROID_RELEASE_CERT_SHA256 locally"
}}

python tools/release_artifact_evidence.py `
  --windows-dir "$actionsRoot/clipvault-windows-release-artifacts" `
  --android-dir "$actionsRoot/clipvault-android-signed-release-artifacts" `
  --version {version} `
  --commit $targetCommit `
  --run-url $run.url `
  --expected-android-cert-sha256 $env:ANDROID_RELEASE_CERT_SHA256 `
  --output "$artifactRoot/artifact-structure-comment.md"
Assert-NativeSuccess "release_artifact_evidence.py"
```

Use the APK and EXEs downloaded from `$releaseRoot` for final QA. The artifact
helper binds captured signer evidence to the supplied Owner certificate, but it
does not independently verify the run, attestations, or downloaded APK with real
`apksigner`. Its output and the manual parity commands above remain Owner-attested
prechecks; the artifact gate stays blocked.

### Step F - execute manual QA against exact bytes

Fill `manual-qa-v1.6.0.template.json`. Follow
`docs/MANUAL_QA_V1_6_0.md` exactly, including:

- the named CursorWindow regression on API 26 and API 27 with non-skipped JUnit,
  SDK-version, `app-debug.apk`, and `app-debug-androidTest.apk` SHA-256 evidence;
- physical-device Android, IME privacy, and sync QA using the exact signed APK
  `ClipVault-Android-{version}-release-signed.apk` from the draft=true run;
- Windows installer/portable and clipboard privacy QA using the exact draft assets.

The schema-v2 validator machine-binds Android rows to the exact signed APK run.
Windows observations are Owner-attested: cite `$digestReport`, the draft Release
URL, and the exact EXE names/SHA-256 values in their evidence; the manual helper
does not independently cross-check Windows artifact bytes.

Render only through the fail-closed validator:

```powershell
$packRoot = ".field-test-artifacts/v1.6.0-owner-pack"
python tools/manual_qa_evidence.py --input "$packRoot/manual-qa-v1.6.0.template.json" --no-fail
python tools/manual_qa_evidence.py `
  --input "$packRoot/manual-qa-v1.6.0.template.json" `
  --output "$packRoot/manual-qa-issue-comment.md"
```

Only a validator-rendered `PASS (OWNER-ATTESTED)` report is eligible for review.
It still attests Owner observations; it does not independently fetch evidence.

### Step G - consolidate Issue #36 evidence

`issue-36-comment-draft.md` intentionally remains BLOCKED. Do not manually flip
its rows. Replace the draft with verified helper output and exact GitHub URLs only
after every gate is bound to the same target commit and final asset digests.

### Step H - publish the existing draft, then close

After an Owner approval statement binds the target commit, draft Release URL,
and final digest set, re-download the still-mutable draft into another fresh
directory and compare it with the recorded digest set:

```powershell
$runId = "REPLACE_WITH_DRAFT_TRUE_RUN_ID"
$targetCommit = "REPLACE_WITH_FROZEN_40_HEX_MAIN_COMMIT"
$artifactRoot = ".field-test-artifacts/v1.6.0-draft-run-$runId"
$digestReport = "$artifactRoot/draft-release-SHA256SUMS.txt"
$prepublishRoot = ".field-test-artifacts/v1.6.0-prepublish-$runId"
if (Test-Path $prepublishRoot) {{ throw "Refusing stale prepublish directory" }}
New-Item -ItemType Directory -Path $prepublishRoot | Out-Null
$release = gh release view {version} `
  --repo selinyi123/clipvault-personal `
  --json tagName,name,isDraft,isPrerelease,targetCommitish,url,assets | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {{ throw "gh release view failed" }}
if ($release.tagName -ne "{version}" -or
    $release.name -ne "ClipVault Personal {version}" -or
    -not $release.isDraft -or $release.isPrerelease -or
    $release.targetCommitish -ne $targetCommit) {{
  throw "Draft Release metadata changed after QA"
}}
gh release download {version} `
  --repo selinyi123/clipvault-personal `
  --dir $prepublishRoot
if ($LASTEXITCODE -ne 0) {{ throw "gh release download failed" }}
$prepublishDigests = @(Get-ChildItem $prepublishRoot -File | Sort-Object Name | ForEach-Object {{
  "{{0}}  {{1}}" -f (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant(), $_.Name
}})
if (@(Compare-Object @(Get-Content $digestReport) $prepublishDigests).Count -ne 0) {{
  throw "Draft assets changed after QA; discard approval and repeat verification"
}}

gh release edit {version} `
  --repo selinyi123/clipvault-personal `
  --draft=false
if ($LASTEXITCODE -ne 0) {{ throw "GitHub Release publication failed" }}

$published = gh release view {version} `
  --repo selinyi123/clipvault-personal `
  --json tagName,name,isDraft,isPrerelease,targetCommitish,url,assets | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {{ throw "post-publication gh release view failed" }}
if ($published.tagName -ne "{version}" -or
    $published.name -ne "ClipVault Personal {version}" -or
    $published.isDraft -or $published.isPrerelease -or
    $published.targetCommitish -ne $targetCommit) {{
  throw "Published Release metadata mismatch"
}}
$approvedAssetNames = @(Get-Content $digestReport | ForEach-Object {{
  ($_ -split '\\s{{2,}}', 2)[1]
}} | Sort-Object)
$publishedAssetNames = @($published.assets | ForEach-Object name | Sort-Object)
if (@(Compare-Object $approvedAssetNames $publishedAssetNames).Count -ne 0 -or
    @($published.assets | Where-Object size -LE 0).Count -ne 0) {{
  throw "Published Release asset inventory mismatch"
}}

$postpublishRoot = ".field-test-artifacts/v1.6.0-postpublish-$runId"
if (Test-Path $postpublishRoot) {{ throw "Refusing stale post-publish directory" }}
New-Item -ItemType Directory -Path $postpublishRoot | Out-Null
gh release download {version} `
  --repo selinyi123/clipvault-personal `
  --dir $postpublishRoot
if ($LASTEXITCODE -ne 0) {{ throw "post-publication gh release download failed" }}
$publishedDigests = @(Get-ChildItem $postpublishRoot -File | Sort-Object Name | ForEach-Object {{
  "{{0}}  {{1}}" -f (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant(), $_.Name
}})
if (@(Compare-Object @(Get-Content $digestReport) $publishedDigests).Count -ne 0) {{
  throw "Published assets differ from the approved digest set"
}}
```

The `gh release edit` command is an Owner publication action and must run only
after the bound approval described above. The commands then recheck the mutable
Release metadata, inventory, sizes, and bytes after publication. Close Issue #36
only after readiness independently reports no blocker; publication alone is not
closure authorization.

## 3. Hard blockers

Issue #36 remains open if any of these is missing:

- current-main CI and release-candidate dry run success on one exact target SHA;
- defined release environment approval policy and required secret names;
- Owner-confirmed Android signer certificate identity enforced by the workflow;
- downloaded final draft asset bytes, provenance, manifests, and digests verified;
- non-skipped API 26 and API 27 compatibility evidence;
- physical-device QA bound to the exact final signed APK digest;
- IME privacy, sync, and Windows clipboard privacy evidence;
- Owner publication approval bound to the same target, draft, and digest set;
- published, non-prerelease `{version}` Release with exact target and assets.
"""


def agent_cluster_markdown(version: str) -> str:
    _validate_scope(version)
    return f"""# Agent Cluster Runtime Plan - Issue #36 / {version}

## Parallel read-only wave

- Release Coordinator: freeze one target SHA and maintain the truth table.
- CI Evidence Agent: verify current-main CI and RC dry run on that SHA.
- Environment Agent: verify names and policy shape; secret values remain Owner-only.

## Serial artifact wave

```text
frozen main SHA
  -> signer identity enforcement
  -> draft=false preflight
  -> draft=true final draft asset build
  -> exact-run provenance and byte verification
  -> API 26 + API 27 compatibility QA
  -> physical signed-APK and Windows QA
  -> Owner publication approval
  -> publish the existing draft without rebuilding
  -> readiness review and Issue #36 closure candidate
```

No agent may infer private signing inputs, device observations, or Owner approval.
"""


def issue_comment_draft(version: str, issue_url: str) -> str:
    _validate_scope(version, issue_url)
    return f"""## Issue #36 release-gate evidence draft

Target version: `{version}`
Issue: {issue_url}

This generated coordination draft is intentionally **BLOCKED**. Do not manually
change statuses. Replace it with validator-rendered evidence and exact GitHub URLs.

| Gate | Status | Required binding |
|---|---|---|
| Frozen current-main target | BLOCKED | exact 40-hex SHA |
| Current-main CI | BLOCKED | success on exact target SHA |
| Release-candidate dry run | BLOCKED | success on exact target SHA |
| `release` environment policy | BLOCKED | Owner-confirmed policy |
| Android signing secrets | BLOCKED | required names only; values never disclosed |
| Owner Android certificate identity | BLOCKED | enforced 64-hex SHA-256 |
| Draft=true final artifact run | BLOCKED | exact target, run URL, draft URL |
| Windows byte/provenance evidence | BLOCKED | exact names, sizes, SHA-256 values |
| Android byte/provenance/signer evidence | BLOCKED | exact signed APK and Owner certificate |
| API 26 compatibility | BLOCKED | non-skipped named test, SDK/JUnit/APK evidence |
| API 27 compatibility | BLOCKED | non-skipped named test, SDK/JUnit/APK evidence |
| Physical final signed APK QA | BLOCKED | exact draft APK SHA-256 |
| IME privacy QA | BLOCKED | exact physical signed run |
| Sync QA | BLOCKED | exact physical signed run |
| Windows clipboard privacy QA | BLOCKED | exact draft Windows assets |
| Owner publication approval | BLOCKED | exact target, draft URL, digest set |
| Published GitHub Release `{version}` | BLOCKED | non-draft, non-prerelease, exact target/assets |

Closure recommendation: `BLOCKED`
"""


def _json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _text(value: str) -> str:
    return value.rstrip() + "\n"


def build_pack_files(version: str, issue_url: str) -> dict[str, str]:
    _validate_scope(version, issue_url)
    generated_at = utc_now()
    summary = {
        "schema": "clipvault.issue36.owner_pack.summary.v2",
        "version": version,
        "issue_url": issue_url,
        "created_at": generated_at,
        "generated_files": sorted(EXPECTED_OUTPUT_FILES),
        "coordination_only": True,
        "does_not": [
            "call GitHub",
            "read secret values",
            "trigger workflows",
            "validate artifact provenance or signer identity",
            "run device QA",
            "sign artifacts",
            "publish releases",
            "close Issue #36",
        ],
    }
    files = {
        "OWNER_RELEASE_ACTION_PACK.md": _text(
            owner_action_pack(version, issue_url, generated_at=generated_at)
        ),
        "agent-cluster.md": _text(agent_cluster_markdown(version)),
        "issue-36-comment-draft.md": _text(issue_comment_draft(version, issue_url)),
        "manual-qa-v1.6.0.template.json": _json_text(manual_qa_template(version)),
        "pack-summary.json": _json_text(summary),
        "release-artifacts-v1.6.0.template.json": _json_text(artifact_template(version)),
    }
    if set(files) != set(EXPECTED_OUTPUT_FILES):
        raise RuntimeError("Owner pack output contract is inconsistent")
    return files


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None and is_junction():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    for candidate in (absolute, *absolute.parents):
        if _is_link_like(candidate):
            raise ValueError(
                f"refusing output through symlink, junction, or reparse-point component: {candidate}"
            )


def _validate_existing_target(path: Path) -> None:
    if _is_link_like(path):
        raise ValueError(f"refusing to replace symlink, junction, or reparse-point output: {path}")
    if not path.exists():
        return
    if not path.is_file():
        raise ValueError(f"Owner pack output must be a regular file: {path}")
    if path.stat().st_nlink != 1:
        raise ValueError(f"refusing to replace hard-linked Owner pack output: {path}")


def preflight_output_dir(output_dir: Path, *, force: bool) -> bool:
    """Validate all conflicts before writing and return whether the dir exists."""

    _reject_symlink_components(output_dir)
    if output_dir.exists():
        if not output_dir.is_dir():
            raise ValueError(f"Owner pack output must be a directory: {output_dir}")
        entries = list(output_dir.iterdir())
        if entries and not force:
            raise FileExistsError(
                f"output directory is not empty; use --force to replace known pack files: {output_dir}"
            )
        for name in EXPECTED_OUTPUT_FILES:
            _validate_existing_target(output_dir / name)
        return True

    parent = output_dir.parent
    _reject_symlink_components(parent)
    if parent.exists() and not parent.is_dir():
        raise ValueError(f"Owner pack output parent must be a directory: {parent}")
    return False


def _replace_existing_pack(output_dir: Path, stage: Path) -> None:
    backup = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.backup-", dir=output_dir.parent)
    )
    moved_originals: list[str] = []
    installed: list[str] = []
    preserve_backup = False
    try:
        for name in EXPECTED_OUTPUT_FILES:
            target = output_dir / name
            if target.exists():
                os.replace(target, backup / name)
                moved_originals.append(name)

        for name in EXPECTED_OUTPUT_FILES:
            os.replace(stage / name, output_dir / name)
            installed.append(name)
    except BaseException as exc:
        rollback_errors: list[str] = []
        for name in reversed(installed):
            try:
                (output_dir / name).unlink()
            except OSError as rollback_exc:
                rollback_errors.append(f"remove {name}: {type(rollback_exc).__name__}")
        for name in moved_originals:
            try:
                os.replace(backup / name, output_dir / name)
            except OSError as rollback_exc:
                rollback_errors.append(f"restore {name}: {type(rollback_exc).__name__}")
        if rollback_errors:
            preserve_backup = True
            raise RuntimeError(
                "Owner pack replacement failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
                + f"; original files preserved at: {backup.resolve(strict=False)}"
            ) from exc
        raise
    finally:
        if not preserve_backup:
            shutil.rmtree(backup, ignore_errors=True)


def write_pack(output_dir: Path, files: dict[str, str], *, force: bool = False) -> None:
    existed = preflight_output_dir(output_dir, force=force)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage: Path | None = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent)
    )
    try:
        for name in EXPECTED_OUTPUT_FILES:
            (stage / name).write_text(files[name], encoding="utf-8", newline="")

        if not existed:
            if output_dir.exists():
                raise FileExistsError(f"output directory appeared during generation: {output_dir}")
            os.replace(stage, output_dir)
            stage = None
            return

        _replace_existing_pack(output_dir, stage)
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the fixed-scope v1.6.0 Issue #36 Owner coordination pack."
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--version", choices=(VERSION,), default=VERSION)
    parser.add_argument("--issue-url", choices=(ISSUE_URL,), default=ISSUE_URL)
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace only known regular pack files; preserve unknown files",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        files = build_pack_files(args.version, args.issue_url)
        write_pack(args.out_dir, files, force=args.force)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))

    print(f"Wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
