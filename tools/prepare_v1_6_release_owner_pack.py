#!/usr/bin/env python3
"""Prepare a local, fail-closed Owner action pack for Issue #36.

The pack is coordination material only. It does not call GitHub, read secret
values, trigger workflows, validate artifact provenance, run device QA, sign
artifacts, publish v1.6.0, or close Issue #36.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import types
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
    """Load trusted repository source without consulting ignored bytecode caches."""

    candidate = ROOT / relative_path
    if candidate.is_symlink():
        raise RuntimeError(f"trusted source must not be a symlink: {candidate}")
    path = candidate.resolve(strict=True)
    if not path.is_file():
        raise RuntimeError(f"trusted source must be a regular file: {path}")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = ""
    module.__cached__ = None
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        code = compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
        exec(code, module.__dict__)
    except BaseException:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
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
    """Return the canonical schema-v3 template without copying its contract."""

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
        "owner_approved_artifact_binding_sha256": "REPLACE_WITH_OWNER_APPROVED_64_HEX_BINDING",
        "draft_release_directory": "REPLACE_WITH_DOWNLOADED_DRAFT_RELEASE_DIRECTORY",
        "git_exe_path": "REPLACE_WITH_ABSOLUTE_TRUSTED_GIT_EXE_PATH_OUTSIDE_WORKSPACE",
        "gh_cli_path": "REPLACE_WITH_ABSOLUTE_TRUSTED_GH_EXE_PATH_OUTSIDE_WORKSPACE",
        "python_exe_path": "REPLACE_WITH_ABSOLUTE_TRUSTED_PYTHON_EXE_PATH_OUTSIDE_WORKSPACE",
        "apksigner_jar_path": "REPLACE_WITH_ANDROID_SDK_BUILD_TOOLS_LIB_APKSIGNER_JAR",
        "java_exe_path": "REPLACE_WITH_ABSOLUTE_TRUSTED_JAVA_EXE_PATH",
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
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Remove-Item Env:GH_FORCE_TTY -ErrorAction SilentlyContinue
$env:GH_PAGER = ""
$env:PAGER = ""
$env:GH_PROMPT_DISABLED = "1"
$env:NO_COLOR = "1"
function Reset-ReleaseGitEnvironment() {{
  @(
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_NAMESPACE", "GIT_SHALLOW_FILE",
    "GIT_QUARANTINE_PATH", "GIT_REPLACE_REF_BASE", "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL",
    "GIT_EXEC_PATH", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS", "SSH_ASKPASS"
  ) | ForEach-Object {{ Remove-Item "Env:$_" -ErrorAction SilentlyContinue }}
  Get-ChildItem Env: | Where-Object {{
    $_.Name -match '^GIT_CONFIG_(KEY|VALUE)_[0-9]+$'
  }} | ForEach-Object {{ Remove-Item "Env:$($_.Name)" -ErrorAction SilentlyContinue }}
  $env:GIT_NO_REPLACE_OBJECTS = "1"
  $env:GIT_TERMINAL_PROMPT = "0"
}}
Reset-ReleaseGitEnvironment
function Test-FullyQualifiedWindowsPath([string]$path) {{
  if ([string]::IsNullOrWhiteSpace($path) -or -not [IO.Path]::IsPathRooted($path)) {{
    return $false
  }}
  $root = [IO.Path]::GetPathRoot($path)
  $driveRoot = ($root.Length -eq 3 -and $root[1] -eq ':' -and
    ($root[2] -eq [char]92 -or $root[2] -eq [char]47))
  if (-not $driveRoot) {{
    return $false
  }}
  try {{
    $drive = [IO.DriveInfo]::new($root.Substring(0, 1))
    if ($drive.DriveType -ne [IO.DriveType]::Fixed) {{ return $false }}
    [void][IO.Path]::GetFullPath($path)
  }} catch {{ return $false }}
  return $true
}}
$workingDirectory = [IO.Path]::GetFullPath((Get-Location).Path).TrimEnd('\', '/')
$separator = [IO.Path]::DirectorySeparatorChar
function Assert-NoReparsePathComponent([IO.FileSystemInfo]$item, [string]$label) {{
  $cursor = $item
  while ($null -ne $cursor) {{
    if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{
      throw "$label must not traverse a symlink, junction, or other reparse point"
    }}
    if ($cursor -is [IO.FileInfo]) {{
      $cursor = $cursor.Directory
    }} else {{
      $cursor = $cursor.Parent
    }}
  }}
}}
if ([string]::IsNullOrWhiteSpace($env:GIT_EXE_PATH) -or
    -not (Test-FullyQualifiedWindowsPath $env:GIT_EXE_PATH)) {{
  throw "GIT_EXE_PATH must be an absolute trusted executable path"
}}
$gitItem = Get-Item -LiteralPath $env:GIT_EXE_PATH -Force -ErrorAction Stop
Assert-NoReparsePathComponent $gitItem "GIT_EXE_PATH"
$gitPath = $gitItem.FullName
if (-not (Test-FullyQualifiedWindowsPath $gitPath) -or
    $gitItem.PSIsContainer -or
    (($gitItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or
    [IO.Path]::GetExtension($gitPath).ToLowerInvariant() -ne ".exe") {{
  throw "GIT_EXE_PATH must resolve to a trusted absolute non-reparse .exe"
}}
$repoRoot = (& $gitPath -C $workingDirectory rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) {{ throw "repository root lookup failed" }}
$repoRoot = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\', '/')
if (-not $workingDirectory.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase)) {{
  throw "Run every Owner release step from the repository root"
}}
if ($gitPath.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $gitPath.StartsWith("$repoRoot$separator", [StringComparison]::OrdinalIgnoreCase)) {{
  throw "GIT_EXE_PATH must resolve outside the repository workspace"
}}
if ([string]::IsNullOrWhiteSpace($env:GH_CLI_PATH) -or
    -not (Test-FullyQualifiedWindowsPath $env:GH_CLI_PATH)) {{
  throw "GH_CLI_PATH must be an absolute trusted executable path"
}}
$ghItem = Get-Item -LiteralPath $env:GH_CLI_PATH -Force -ErrorAction Stop
Assert-NoReparsePathComponent $ghItem "GH_CLI_PATH"
$ghPath = $ghItem.FullName
if ($ghItem.PSIsContainer -or
    [IO.Path]::GetExtension($ghPath).ToLowerInvariant() -ne ".exe" -or
    $ghPath.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $ghPath.StartsWith("$repoRoot$separator", [StringComparison]::OrdinalIgnoreCase)) {{
  throw "GH_CLI_PATH must be a trusted non-reparse .exe outside the workspace"
}}
$targetCommit = & $ghPath api -X GET `
  --hostname github.com `
  "repos/selinyi123/clipvault-personal/branches/main" `
  --jq .commit.sha
if ($LASTEXITCODE -ne 0) {{ throw "current main lookup failed" }}
$targetCommit = $targetCommit.Trim()
if ($targetCommit -cnotmatch '^[0-9a-f]{{40}}$') {{ throw "current main SHA is invalid" }}
$status = @(& $gitPath -C $repoRoot -c core.fsmonitor=false status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) {{ throw "git status failed" }}
if ($status.Count -ne 0) {{ throw "Worktree must be clean, including untracked files" }}
$localCommit = (& $gitPath -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {{ throw "local HEAD lookup failed" }}
if ($localCommit -ne $targetCommit) {{
  throw "Run the release tools only from the exact clean current-main commit"
}}
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
Set-StrictMode -Version Latest
function Reset-ReleaseGitEnvironment() {{
  @(
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_NAMESPACE", "GIT_SHALLOW_FILE",
    "GIT_QUARANTINE_PATH", "GIT_REPLACE_REF_BASE", "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL",
    "GIT_EXEC_PATH", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS", "SSH_ASKPASS"
  ) | ForEach-Object {{ Remove-Item "Env:$_" -ErrorAction SilentlyContinue }}
  Get-ChildItem Env: | Where-Object {{
    $_.Name -match '^GIT_CONFIG_(KEY|VALUE)_[0-9]+$'
  }} | ForEach-Object {{ Remove-Item "Env:$($_.Name)" -ErrorAction SilentlyContinue }}
  $env:GIT_NO_REPLACE_OBJECTS = "1"
  $env:GIT_TERMINAL_PROMPT = "0"
}}
Reset-ReleaseGitEnvironment
function Test-FullyQualifiedWindowsPath([string]$path) {{
  if ([string]::IsNullOrWhiteSpace($path) -or -not [IO.Path]::IsPathRooted($path)) {{
    return $false
  }}
  $root = [IO.Path]::GetPathRoot($path)
  $driveRoot = ($root.Length -eq 3 -and $root[1] -eq ':' -and
    ($root[2] -eq [char]92 -or $root[2] -eq [char]47))
  if (-not $driveRoot) {{
    return $false
  }}
  try {{
    $drive = [IO.DriveInfo]::new($root.Substring(0, 1))
    if ($drive.DriveType -ne [IO.DriveType]::Fixed) {{ return $false }}
    [void][IO.Path]::GetFullPath($path)
  }} catch {{ return $false }}
  return $true
}}
Remove-Item Env:GH_FORCE_TTY -ErrorAction SilentlyContinue
$env:GH_PAGER = ""
$env:PAGER = ""
$env:GH_PROMPT_DISABLED = "1"
$env:NO_COLOR = "1"
function Assert-NativeSuccess([string]$label) {{
  if ($LASTEXITCODE -ne 0) {{ throw "$label failed with exit code $LASTEXITCODE" }}
}}
$workingDirectory = [IO.Path]::GetFullPath((Get-Location).Path).TrimEnd('\', '/')
$separator = [IO.Path]::DirectorySeparatorChar
function Assert-NoReparsePathComponent([IO.FileSystemInfo]$item, [string]$label) {{
  $cursor = $item
  while ($null -ne $cursor) {{
    if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{
      throw "$label must not traverse a symlink, junction, or other reparse point"
    }}
    if ($cursor -is [IO.FileInfo]) {{
      $cursor = $cursor.Directory
    }} else {{
      $cursor = $cursor.Parent
    }}
  }}
}}
if ([string]::IsNullOrWhiteSpace($env:GIT_EXE_PATH) -or
    -not (Test-FullyQualifiedWindowsPath $env:GIT_EXE_PATH)) {{
  throw "GIT_EXE_PATH must be an absolute trusted executable path"
}}
$gitItem = Get-Item -LiteralPath $env:GIT_EXE_PATH -Force -ErrorAction Stop
Assert-NoReparsePathComponent $gitItem "GIT_EXE_PATH"
$gitPath = $gitItem.FullName
if (-not (Test-FullyQualifiedWindowsPath $gitPath) -or
    $gitItem.PSIsContainer -or
    (($gitItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or
    [IO.Path]::GetExtension($gitPath).ToLowerInvariant() -ne ".exe") {{
  throw "GIT_EXE_PATH must resolve to a trusted absolute non-reparse .exe"
}}
$repoRoot = (& $gitPath -C $workingDirectory rev-parse --show-toplevel).Trim()
Assert-NativeSuccess "repository root lookup"
$repoRoot = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\', '/')
if (-not $workingDirectory.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase)) {{
  throw "Run every Owner release step from the repository root"
}}
if ($gitPath.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $gitPath.StartsWith("$repoRoot$separator", [StringComparison]::OrdinalIgnoreCase)) {{
  throw "GIT_EXE_PATH must resolve outside the repository workspace"
}}
function Resolve-TrustedExecutablePath([string]$environmentName) {{
  $raw = [Environment]::GetEnvironmentVariable($environmentName)
  if ([string]::IsNullOrWhiteSpace($raw)) {{
    throw "Set $environmentName to an absolute trusted executable outside the repository workspace"
  }}
  if (-not (Test-FullyQualifiedWindowsPath $raw)) {{
    throw "$environmentName must be an absolute trusted executable path"
  }}
  $item = Get-Item -LiteralPath $raw -Force -ErrorAction Stop
  Assert-NoReparsePathComponent $item $environmentName
  $resolved = $item.FullName
  $extension = [IO.Path]::GetExtension($resolved).ToLowerInvariant()
  if (-not (Test-FullyQualifiedWindowsPath $resolved) -or
      $item.PSIsContainer -or
      (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or
      $extension -ne ".exe") {{
    throw "$environmentName must resolve to a real absolute non-reparse .exe file"
  }}
  if ($resolved.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase) -or
      $resolved.StartsWith("$repoRoot$separator", [StringComparison]::OrdinalIgnoreCase)) {{
    throw "$environmentName must not resolve inside the repository workspace"
  }}
  return $resolved
}}
$ghPath = Resolve-TrustedExecutablePath "GH_CLI_PATH"
$pythonPath = Resolve-TrustedExecutablePath "PYTHON_EXE_PATH"
$evidenceTool = Join-Path $repoRoot "tools/release_artifact_evidence.py"

$targetCommit = "REPLACE_WITH_FROZEN_40_HEX_MAIN_COMMIT"
$localCommit = (& $gitPath -C $repoRoot rev-parse HEAD).Trim()
Assert-NativeSuccess "local HEAD lookup"
$status = @(& $gitPath -C $repoRoot -c core.fsmonitor=false status --porcelain=v1 --untracked-files=all)
Assert-NativeSuccess "git status"
if ($localCommit -ne $targetCommit -or $status.Count -ne 0) {{
  throw "Artifact verifier must run from the exact clean frozen target"
}}
function Assert-TrackedSourceMatchesCommit([string]$relativePath) {{
  $sourceItem = Get-Item -LiteralPath (Join-Path $repoRoot $relativePath) -Force -ErrorAction Stop
  if ($sourceItem.PSIsContainer -or
      (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {{
    throw "Tracked release source must be a regular non-reparse file: $relativePath"
  }}
  $objectSpec = $targetCommit + ":" + $relativePath
  $expectedBlob = (& $gitPath -C $repoRoot rev-parse $objectSpec).Trim()
  Assert-NativeSuccess "target blob lookup for $relativePath"
  $actualBlob = (& $gitPath -C $repoRoot hash-object --no-filters -- $sourceItem.FullName).Trim()
  Assert-NativeSuccess "worktree blob lookup for $relativePath"
  if ($expectedBlob -cnotmatch '^[0-9a-f]{{40}}$' -or $actualBlob -cne $expectedBlob) {{
    throw "Tracked release source differs from the frozen target: $relativePath"
  }}
}}
Assert-TrackedSourceMatchesCommit "tools/release_artifact_evidence.py"
Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"
$runId = "REPLACE_WITH_DRAFT_TRUE_RUN_ID"
$artifactRoot = ".field-test-artifacts/v1.6.0-draft-run-$runId"
if (Test-Path $artifactRoot) {{ throw "Refusing stale evidence directory: $artifactRoot" }}

$run = & $ghPath run view $runId `
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
& $ghPath run download $runId `
  --repo selinyi123/clipvault-personal `
  --name clipvault-windows-release-artifacts `
  --name clipvault-android-signed-release-artifacts `
  --dir $actionsRoot
Assert-NativeSuccess "gh run download"

$release = & $ghPath release view {version} `
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

& $ghPath release download {version} `
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
if ([string]::IsNullOrWhiteSpace($env:APKSIGNER_JAR_PATH) -or
    -not (Test-FullyQualifiedWindowsPath $env:APKSIGNER_JAR_PATH)) {{
  throw "Set APKSIGNER_JAR_PATH to an absolute Android SDK lib/apksigner.jar path"
}}
$apksignerItem = Get-Item -LiteralPath $env:APKSIGNER_JAR_PATH -Force -ErrorAction Stop
Assert-NoReparsePathComponent $apksignerItem "APKSIGNER_JAR_PATH"
$apksignerPath = $apksignerItem.FullName
if ($apksignerItem.PSIsContainer -or
    [IO.Path]::GetExtension($apksignerPath).ToLowerInvariant() -ne ".jar" -or
    $apksignerPath.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $apksignerPath.StartsWith("$repoRoot$separator", [StringComparison]::OrdinalIgnoreCase)) {{
  throw "APKSIGNER_JAR_PATH must be a trusted non-reparse .jar outside the workspace"
}}
if ([string]::IsNullOrWhiteSpace($env:JAVA_EXE_PATH) -or
    -not (Test-FullyQualifiedWindowsPath $env:JAVA_EXE_PATH)) {{
  throw "Set JAVA_EXE_PATH to an absolute trusted java.exe"
}}
$javaItem = Get-Item -LiteralPath $env:JAVA_EXE_PATH -Force -ErrorAction Stop
Assert-NoReparsePathComponent $javaItem "JAVA_EXE_PATH"
$javaPath = $javaItem.FullName
if ($javaItem.PSIsContainer -or
    [IO.Path]::GetExtension($javaPath).ToLowerInvariant() -ne ".exe" -or
    $javaPath.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $javaPath.StartsWith("$repoRoot$separator", [StringComparison]::OrdinalIgnoreCase)) {{
  throw "JAVA_EXE_PATH must be a trusted non-reparse .exe outside the workspace"
}}

& $pythonPath -I -S $evidenceTool `
  --windows-dir "$actionsRoot/clipvault-windows-release-artifacts" `
  --android-dir "$actionsRoot/clipvault-android-signed-release-artifacts" `
  --draft-release-dir "$releaseRoot" `
  --gh "$ghPath" `
  --apksigner "$apksignerPath" `
  --java "$javaPath" `
  --version {version} `
  --commit $targetCommit `
  --run-url $run.url `
  --expected-android-cert-sha256 $env:ANDROID_RELEASE_CERT_SHA256 `
  --require-live-final-draft `
  --evidence-output "$artifactRoot/final-draft-artifact-evidence.json" `
  --comment-output "$artifactRoot/final-draft-artifact-comment.md"
Assert-NativeSuccess "release_artifact_evidence.py"
$finalLocalCommit = (& $gitPath -C $repoRoot rev-parse HEAD).Trim()
Assert-NativeSuccess "final local HEAD lookup"
$finalStatus = @(& $gitPath -C $repoRoot -c core.fsmonitor=false status --porcelain=v1 --untracked-files=all)
Assert-NativeSuccess "final git status"
if ($finalLocalCommit -ne $targetCommit -or $finalStatus.Count -ne 0) {{
  throw "Artifact verifier checkout changed during evidence collection"
}}
Assert-TrackedSourceMatchesCommit "tools/release_artifact_evidence.py"
Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"
```

Use the APK and EXEs downloaded from `$releaseRoot` for final QA. The artifact
helper now fails unless it verifies current `main`, the exact successful
`draft=true` run and attempt, both Actions bundles, all eight per-file
attestations, the mutable draft Release metadata/API digests, exact Actions-to-
draft bytes, and an independent real `apksigner` result against the Owner trust
anchor. Its JSON is a non-self-authenticating machine snapshot: readiness must
rerun the checks or independently cross-check the binding and live GitHub state.
The helper checks the release-environment certificate variable at both ends of
collection. It does not replace manual QA,
Owner publication approval, final publication, or Issue #36 closure.

### Step F - execute manual QA against exact bytes

Fill `manual-qa-v1.6.0.template.json`. Follow
`docs/MANUAL_QA_V1_6_0.md` exactly, including:

- the named CursorWindow regression and `OutboxBaseSeqTest` on API 26 and API 27
  with non-skipped JUnit, SDK-version, `app-debug.apk`, and
  `app-debug-androidTest.apk` SHA-256 evidence;
- run the CursorWindow method and `OutboxBaseSeqTest` as separate filtered
  connected-test invocations, snapshotting each redacted JUnit XML immediately;
  never reuse one aggregate XML for both evidence rows;
- hash both debug APK inputs after each filtered invocation and require the
  second hashes to exactly match the first; otherwise discard both results;
- before each invocation, move any connected-test result directory aside;
  abort on a nonzero Gradle exit or if no fresh result directory is created;
- physical-device Android, IME privacy, and sync QA using the exact signed APK
  `ClipVault-Android-{version}-release-signed.apk` from the draft=true run;
- the schema-v3 `re_pair_outbox_high_water` row, covering both an empty
  acknowledged high-water mark and a pending-row re-pair without clip content;
- Windows installer/portable and clipboard privacy QA using the exact draft assets.

The schema-v3 validator machine-binds Android rows to the exact signed APK run.
Populate `release_artifact_binding` from the final-draft report with this exact
field mapping: `artifact_evidence_type <- evidence_type`,
`artifact_binding_sha256 <- artifact_binding_sha256`,
`target_commit <- target_commit`, `workflow_run.id/url/attempt <-` the same
workflow fields, `draft_release.id/url/tag_name <-` the same draft fields, and
`android_signed_apk.name/sha256 <- release_name/sha256` from the unique
`artifacts` row whose role is `android_signed_apk`. Choose one path-free
`evidence_ref` and repeat it in the final signed Android run. Do not type an
alternate run, draft Release, APK name, or digest. Strict mode recomputes the
artifact binding and separately cross-checks the snapshot URL. The numeric draft
Release ID, not that URL, is part of the binding digest. Windows observations are Owner-attested.
Cite `$digestReport`, the draft Release URL, and the exact EXE names/SHA-256
values in their evidence; the manual helper does not independently
cross-check the physical Windows observations.

Render only through the fail-closed validator:

```powershell
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
function Reset-ReleaseGitEnvironment() {{
  @(
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_NAMESPACE", "GIT_SHALLOW_FILE",
    "GIT_QUARANTINE_PATH", "GIT_REPLACE_REF_BASE", "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL",
    "GIT_EXEC_PATH", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS", "SSH_ASKPASS"
  ) | ForEach-Object {{ Remove-Item "Env:$_" -ErrorAction SilentlyContinue }}
  Get-ChildItem Env: | Where-Object {{
    $_.Name -match '^GIT_CONFIG_(KEY|VALUE)_[0-9]+$'
  }} | ForEach-Object {{ Remove-Item "Env:$($_.Name)" -ErrorAction SilentlyContinue }}
  $env:GIT_NO_REPLACE_OBJECTS = "1"
  $env:GIT_TERMINAL_PROMPT = "0"
}}
Reset-ReleaseGitEnvironment
function Test-FullyQualifiedWindowsPath([string]$path) {{
  if ([string]::IsNullOrWhiteSpace($path) -or -not [IO.Path]::IsPathRooted($path)) {{
    return $false
  }}
  $root = [IO.Path]::GetPathRoot($path)
  $driveRoot = ($root.Length -eq 3 -and $root[1] -eq ':' -and
    ($root[2] -eq [char]92 -or $root[2] -eq [char]47))
  if (-not $driveRoot) {{
    return $false
  }}
  try {{
    $drive = [IO.DriveInfo]::new($root.Substring(0, 1))
    if ($drive.DriveType -ne [IO.DriveType]::Fixed) {{ return $false }}
    [void][IO.Path]::GetFullPath($path)
  }} catch {{ return $false }}
  return $true
}}
$targetCommit = "REPLACE_WITH_FROZEN_40_HEX_MAIN_COMMIT"
$workingDirectory = [IO.Path]::GetFullPath((Get-Location).Path).TrimEnd('\', '/')
$separator = [IO.Path]::DirectorySeparatorChar
function Assert-NoReparsePathComponent([IO.FileSystemInfo]$item, [string]$label) {{
  $cursor = $item
  while ($null -ne $cursor) {{
    if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{
      throw "$label must not traverse a symlink, junction, or other reparse point"
    }}
    if ($cursor -is [IO.FileInfo]) {{
      $cursor = $cursor.Directory
    }} else {{
      $cursor = $cursor.Parent
    }}
  }}
}}
if ([string]::IsNullOrWhiteSpace($env:GIT_EXE_PATH) -or
    -not (Test-FullyQualifiedWindowsPath $env:GIT_EXE_PATH)) {{
  throw "GIT_EXE_PATH must be an absolute trusted executable path"
}}
$gitItem = Get-Item -LiteralPath $env:GIT_EXE_PATH -Force -ErrorAction Stop
Assert-NoReparsePathComponent $gitItem "GIT_EXE_PATH"
$gitPath = $gitItem.FullName
if (-not (Test-FullyQualifiedWindowsPath $gitPath) -or
    $gitItem.PSIsContainer -or
    (($gitItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or
    [IO.Path]::GetExtension($gitPath).ToLowerInvariant() -ne ".exe") {{
  throw "GIT_EXE_PATH must resolve to a trusted absolute non-reparse .exe"
}}
$repoRoot = (& $gitPath -C $workingDirectory rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) {{ throw "repository root lookup failed" }}
$repoRoot = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\', '/')
if (-not $workingDirectory.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase)) {{
  throw "Run every Owner release step from the repository root"
}}
if ($gitPath.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $gitPath.StartsWith("$repoRoot$separator", [StringComparison]::OrdinalIgnoreCase)) {{
  throw "GIT_EXE_PATH must resolve outside the repository workspace"
}}
if ([string]::IsNullOrWhiteSpace($env:PYTHON_EXE_PATH) -or
    -not (Test-FullyQualifiedWindowsPath $env:PYTHON_EXE_PATH)) {{
  throw "PYTHON_EXE_PATH must be an absolute trusted executable path"
}}
$pythonItem = Get-Item -LiteralPath $env:PYTHON_EXE_PATH -Force -ErrorAction Stop
Assert-NoReparsePathComponent $pythonItem "PYTHON_EXE_PATH"
$pythonPath = $pythonItem.FullName
if (-not (Test-FullyQualifiedWindowsPath $pythonPath) -or
    $pythonItem.PSIsContainer -or
    (($pythonItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or
    [IO.Path]::GetExtension($pythonPath).ToLowerInvariant() -ne ".exe" -or
    $pythonPath.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $pythonPath.StartsWith("$repoRoot$separator", [StringComparison]::OrdinalIgnoreCase)) {{
  throw "PYTHON_EXE_PATH must resolve to a trusted absolute non-reparse .exe outside the workspace"
}}
$manualQaTool = Join-Path $repoRoot "tools/manual_qa_evidence.py"
$packRoot = Join-Path $repoRoot ".field-test-artifacts/v1.6.0-owner-pack"
$manualQaEvidence = Join-Path $packRoot "manual-qa-v1.6.0.template.json"
$finalOutput = Join-Path $packRoot "manual-qa-issue-comment.md"
$pendingOutput = Join-Path $packRoot ("manual-qa-issue-comment.pending-" + [Guid]::NewGuid().ToString("N") + ".md")
$runId = "REPLACE_WITH_DRAFT_TRUE_RUN_ID"
$artifactRoot = Join-Path $repoRoot ".field-test-artifacts/v1.6.0-draft-run-$runId"
$finalDraftEvidence = Join-Path $artifactRoot "final-draft-artifact-evidence.json"
$finalDraftEvidenceItem = Get-Item -LiteralPath $finalDraftEvidence -Force -ErrorAction Stop
Assert-NoReparsePathComponent $finalDraftEvidenceItem "final-draft artifact evidence"
if ($finalDraftEvidenceItem.PSIsContainer -or
    (($finalDraftEvidenceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {{
  throw "Final-draft artifact evidence must be a regular non-reparse file"
}}
$localCommit = (& $gitPath -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {{ throw "local HEAD lookup failed" }}
$status = @(& $gitPath -C $repoRoot -c core.fsmonitor=false status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) {{ throw "git status failed" }}
if ($localCommit -ne $targetCommit -or $status.Count -ne 0) {{
  throw "Manual QA validator must run from the exact clean frozen target"
}}
function Assert-TrackedSourceMatchesCommit([string]$relativePath) {{
  $sourceItem = Get-Item -LiteralPath (Join-Path $repoRoot $relativePath) -Force -ErrorAction Stop
  if ($sourceItem.PSIsContainer -or
      (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {{
    throw "Tracked release source must be a regular non-reparse file: $relativePath"
  }}
  $objectSpec = $targetCommit + ":" + $relativePath
  $expectedBlob = (& $gitPath -C $repoRoot rev-parse $objectSpec).Trim()
  if ($LASTEXITCODE -ne 0) {{ throw "target blob lookup failed for $relativePath" }}
  $actualBlob = (& $gitPath -C $repoRoot hash-object --no-filters -- $sourceItem.FullName).Trim()
  if ($LASTEXITCODE -ne 0) {{ throw "worktree blob lookup failed for $relativePath" }}
  if ($expectedBlob -cnotmatch '^[0-9a-f]{{40}}$' -or $actualBlob -cne $expectedBlob) {{
    throw "Tracked release source differs from the frozen target: $relativePath"
  }}
}}
function Get-TrustedEvidenceSha256([string]$path, [string]$label) {{
  $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
  Assert-NoReparsePathComponent $item $label
  if ($item.PSIsContainer -or
      (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {{
    throw "$label must be a regular non-reparse file"
  }}
  return (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
}}
$manualQaEvidenceSha256 = Get-TrustedEvidenceSha256 $manualQaEvidence "manual QA evidence"
$finalDraftEvidenceSha256 = Get-TrustedEvidenceSha256 $finalDraftEvidence "final-draft artifact evidence"
Assert-TrackedSourceMatchesCommit "tools/manual_qa_evidence.py"
Assert-TrackedSourceMatchesCommit "tools/release_artifact_evidence.py"
Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"
& $pythonPath -I -S $manualQaTool `
  --input "$manualQaEvidence" `
  --final-draft-artifact-evidence "$finalDraftEvidence" `
  --require-final-draft-binding `
  --require-release-ready `
  --no-fail
if ($LASTEXITCODE -ne 0) {{ throw "manual QA preview failed" }}
Assert-TrackedSourceMatchesCommit "tools/manual_qa_evidence.py"
Assert-TrackedSourceMatchesCommit "tools/release_artifact_evidence.py"
Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"
if ((Get-TrustedEvidenceSha256 $manualQaEvidence "manual QA evidence") -cne $manualQaEvidenceSha256 -or
    (Get-TrustedEvidenceSha256 $finalDraftEvidence "final-draft artifact evidence") -cne $finalDraftEvidenceSha256) {{
  throw "Manual QA or final-draft artifact evidence changed after preview"
}}
if (Test-Path -LiteralPath $finalOutput) {{
  throw "Final manual QA output already exists; review or remove it before rerunning Step F"
}}
try {{
  & $pythonPath -I -S $manualQaTool `
    --input "$manualQaEvidence" `
    --final-draft-artifact-evidence "$finalDraftEvidence" `
    --require-final-draft-binding `
    --require-release-ready `
    --output "$pendingOutput"
  if ($LASTEXITCODE -ne 0) {{ throw "manual QA validation failed or remains blocked" }}
  $finalLocalCommit = (& $gitPath -C $repoRoot rev-parse HEAD).Trim()
  if ($LASTEXITCODE -ne 0) {{ throw "final local HEAD lookup failed" }}
  $finalStatus = @(& $gitPath -C $repoRoot -c core.fsmonitor=false status --porcelain=v1 --untracked-files=all)
  if ($LASTEXITCODE -ne 0) {{ throw "final git status failed" }}
  if ($finalLocalCommit -ne $targetCommit -or $finalStatus.Count -ne 0) {{
    throw "Manual QA validator checkout changed during validation"
  }}
  Assert-TrackedSourceMatchesCommit "tools/manual_qa_evidence.py"
  Assert-TrackedSourceMatchesCommit "tools/release_artifact_evidence.py"
  Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"
  if ((Get-TrustedEvidenceSha256 $manualQaEvidence "manual QA evidence") -cne $manualQaEvidenceSha256 -or
      (Get-TrustedEvidenceSha256 $finalDraftEvidence "final-draft artifact evidence") -cne $finalDraftEvidenceSha256) {{
    throw "Manual QA or final-draft artifact evidence changed during final render"
  }}
  $pendingItem = Get-Item -LiteralPath $pendingOutput -Force -ErrorAction Stop
  Assert-NoReparsePathComponent $pendingItem "pending manual QA output"
  if ($pendingItem.PSIsContainer -or
      (($pendingItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {{
    throw "Pending manual QA output must be a regular non-reparse file"
  }}
  if (Test-Path -LiteralPath $finalOutput) {{
    throw "Final manual QA output appeared during validation"
  }}
  Move-Item -LiteralPath $pendingOutput -Destination $finalOutput -ErrorAction Stop
}} finally {{
  if (Test-Path -LiteralPath $pendingOutput) {{
    Remove-Item -LiteralPath $pendingOutput -Force -ErrorAction SilentlyContinue
  }}
}}
```

Only a validator-rendered `PASS (OWNER-ATTESTED)` report whose header records
`final_draft_binding_assurance=verified_external_snapshot` is eligible for review.
The strict cross-check validates the exact local final-draft snapshot but does
not make that snapshot self-authenticating; live verification remains mandatory.
The report still attests Owner observations and does not independently prove them.

### Step G - consolidate Issue #36 evidence

`issue-36-comment-draft.md` intentionally remains BLOCKED. Do not manually flip
its rows. Replace the draft with verified helper output and exact GitHub URLs only
after every gate is bound to the same target commit and final asset digests.

### Step H - publish the existing draft, then verify published state

After an Owner approval statement binds the target commit, draft Release URL,
and final digest set, re-download the still-mutable draft into another fresh
directory and compare it with the recorded digest set. Copy the 64-hex artifact
binding from that already-posted Owner approval into `$ownerApprovedBinding`;
the local evidence JSON is not the approval source:

```powershell
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
function Reset-ReleaseGitEnvironment() {{
  @(
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_NAMESPACE", "GIT_SHALLOW_FILE",
    "GIT_QUARANTINE_PATH", "GIT_REPLACE_REF_BASE", "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL",
    "GIT_EXEC_PATH", "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS", "SSH_ASKPASS"
  ) | ForEach-Object {{ Remove-Item "Env:$_" -ErrorAction SilentlyContinue }}
  Get-ChildItem Env: | Where-Object {{
    $_.Name -match '^GIT_CONFIG_(KEY|VALUE)_[0-9]+$'
  }} | ForEach-Object {{ Remove-Item "Env:$($_.Name)" -ErrorAction SilentlyContinue }}
  $env:GIT_NO_REPLACE_OBJECTS = "1"
  $env:GIT_TERMINAL_PROMPT = "0"
}}
Reset-ReleaseGitEnvironment
function Test-FullyQualifiedWindowsPath([string]$path) {{
  if ([string]::IsNullOrWhiteSpace($path) -or -not [IO.Path]::IsPathRooted($path)) {{
    return $false
  }}
  $root = [IO.Path]::GetPathRoot($path)
  $driveRoot = ($root.Length -eq 3 -and $root[1] -eq ':' -and
    ($root[2] -eq [char]92 -or $root[2] -eq [char]47))
  if (-not $driveRoot) {{
    return $false
  }}
  try {{
    $drive = [IO.DriveInfo]::new($root.Substring(0, 1))
    if ($drive.DriveType -ne [IO.DriveType]::Fixed) {{ return $false }}
    [void][IO.Path]::GetFullPath($path)
  }} catch {{ return $false }}
  return $true
}}
Remove-Item Env:GH_FORCE_TTY -ErrorAction SilentlyContinue
$env:GH_PAGER = ""
$env:PAGER = ""
$env:GH_PROMPT_DISABLED = "1"
$env:NO_COLOR = "1"
$runId = "REPLACE_WITH_DRAFT_TRUE_RUN_ID"
$targetCommit = "REPLACE_WITH_FROZEN_40_HEX_MAIN_COMMIT"
$ownerApprovedBinding = "REPLACE_WITH_OWNER_APPROVED_64_HEX_BINDING"
if ($ownerApprovedBinding -cnotmatch '^[0-9a-f]{{64}}$') {{
  throw "Copy the exact 64-lowercase-hex binding from the Owner approval statement"
}}
$workingDirectory = [IO.Path]::GetFullPath((Get-Location).Path).TrimEnd('\', '/')
$separator = [IO.Path]::DirectorySeparatorChar
function Assert-NoReparsePathComponent([IO.FileSystemInfo]$item, [string]$label) {{
  $cursor = $item
  while ($null -ne $cursor) {{
    if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{
      throw "$label must not traverse a symlink, junction, or other reparse point"
    }}
    if ($cursor -is [IO.FileInfo]) {{
      $cursor = $cursor.Directory
    }} else {{
      $cursor = $cursor.Parent
    }}
  }}
}}
if ([string]::IsNullOrWhiteSpace($env:GIT_EXE_PATH) -or
    -not (Test-FullyQualifiedWindowsPath $env:GIT_EXE_PATH)) {{
  throw "GIT_EXE_PATH must be an absolute trusted executable path"
}}
$gitItem = Get-Item -LiteralPath $env:GIT_EXE_PATH -Force -ErrorAction Stop
Assert-NoReparsePathComponent $gitItem "GIT_EXE_PATH"
$gitPath = $gitItem.FullName
if (-not (Test-FullyQualifiedWindowsPath $gitPath) -or
    $gitItem.PSIsContainer -or
    (($gitItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or
    [IO.Path]::GetExtension($gitPath).ToLowerInvariant() -ne ".exe") {{
  throw "GIT_EXE_PATH must resolve to a trusted absolute non-reparse .exe"
}}
$repoRoot = (& $gitPath -C $workingDirectory rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) {{ throw "repository root lookup failed" }}
$repoRoot = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\', '/')
if (-not $workingDirectory.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase)) {{
  throw "Run every Owner release step from the repository root"
}}
if ($gitPath.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $gitPath.StartsWith("$repoRoot$separator", [StringComparison]::OrdinalIgnoreCase)) {{
  throw "GIT_EXE_PATH must resolve outside the repository workspace"
}}
function Resolve-TrustedExecutablePath([string]$environmentName) {{
  $raw = [Environment]::GetEnvironmentVariable($environmentName)
  if ([string]::IsNullOrWhiteSpace($raw)) {{
    throw "Set $environmentName to an absolute trusted executable outside the workspace"
  }}
  if (-not (Test-FullyQualifiedWindowsPath $raw)) {{
    throw "$environmentName must be an absolute trusted executable path"
  }}
  $item = Get-Item -LiteralPath $raw -Force -ErrorAction Stop
  Assert-NoReparsePathComponent $item $environmentName
  $resolved = $item.FullName
  if (-not (Test-FullyQualifiedWindowsPath $resolved) -or
      $item.PSIsContainer -or
      (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or
      [IO.Path]::GetExtension($resolved).ToLowerInvariant() -ne ".exe" -or
      $resolved.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase) -or
      $resolved.StartsWith("$repoRoot$separator", [StringComparison]::OrdinalIgnoreCase)) {{
    throw "$environmentName must resolve to a trusted absolute non-reparse .exe outside the workspace"
  }}
  return $resolved
}}
$ghPath = Resolve-TrustedExecutablePath "GH_CLI_PATH"
$pythonPath = Resolve-TrustedExecutablePath "PYTHON_EXE_PATH"
$evidenceTool = Join-Path $repoRoot "tools/release_artifact_evidence.py"
function Resolve-ExactReleaseTagCommit() {{
  $records = & $ghPath api -X GET `
    --hostname github.com `
    "repos/selinyi123/clipvault-personal/git/matching-refs/tags/{version}" | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0) {{ throw "release tag ref lookup failed" }}
  $matches = @($records | Where-Object {{ $_.ref -ceq "refs/tags/{version}" }})
  if ($matches.Count -eq 0) {{ return $null }}
  if ($matches.Count -ne 1) {{ throw "release tag lookup returned duplicate exact refs" }}
  $tagObject = $matches[0].object
  $seen = @{{}}
  for ($depth = 0; $depth -lt 8; $depth++) {{
    $objectType = [string]$tagObject.type
    $objectSha = [string]$tagObject.sha
    if ($objectSha -cnotmatch '^[0-9a-f]{{40}}$') {{
      throw "release tag object has an invalid Git SHA"
    }}
    if ($objectType -ceq "commit") {{ return $objectSha }}
    if ($objectType -cne "tag") {{ throw "release tag does not resolve to a commit" }}
    if ($seen.ContainsKey($objectSha)) {{ throw "release tag contains an object cycle" }}
    $seen[$objectSha] = $true
    $tagRecord = & $ghPath api -X GET `
      --hostname github.com `
      "repos/selinyi123/clipvault-personal/git/tags/$objectSha" | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {{ throw "annotated release tag lookup failed" }}
    $tagObject = $tagRecord.object
  }}
  throw "release tag annotation chain is too deep"
}}
$localCommit = (& $gitPath -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {{ throw "local HEAD lookup failed" }}
$status = @(& $gitPath -C $repoRoot -c core.fsmonitor=false status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) {{ throw "git status failed" }}
if ($localCommit -ne $targetCommit -or $status.Count -ne 0) {{
  throw "Publication tools must run from the exact clean frozen target"
}}
function Assert-TrackedSourceMatchesCommit([string]$relativePath) {{
  $sourceItem = Get-Item -LiteralPath (Join-Path $repoRoot $relativePath) -Force -ErrorAction Stop
  if ($sourceItem.PSIsContainer -or
      (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {{
    throw "Tracked release source must be a regular non-reparse file: $relativePath"
  }}
  $objectSpec = $targetCommit + ":" + $relativePath
  $expectedBlob = (& $gitPath -C $repoRoot rev-parse $objectSpec).Trim()
  if ($LASTEXITCODE -ne 0) {{ throw "target blob lookup failed for $relativePath" }}
  $actualBlob = (& $gitPath -C $repoRoot hash-object --no-filters -- $sourceItem.FullName).Trim()
  if ($LASTEXITCODE -ne 0) {{ throw "worktree blob lookup failed for $relativePath" }}
  if ($expectedBlob -cnotmatch '^[0-9a-f]{{40}}$' -or $actualBlob -cne $expectedBlob) {{
    throw "Tracked release source differs from the frozen target: $relativePath"
  }}
}}
Assert-TrackedSourceMatchesCommit "tools/release_artifact_evidence.py"
Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"
$artifactRoot = ".field-test-artifacts/v1.6.0-draft-run-$runId"
$actionsRoot = "$artifactRoot/actions"
$digestReport = "$artifactRoot/draft-release-SHA256SUMS.txt"
$prepublishRoot = ".field-test-artifacts/v1.6.0-prepublish-$runId"
if (Test-Path $prepublishRoot) {{ throw "Refusing stale prepublish directory" }}
New-Item -ItemType Directory -Path $prepublishRoot | Out-Null
$release = & $ghPath release view {version} `
  --repo selinyi123/clipvault-personal `
  --json tagName,name,isDraft,isPrerelease,targetCommitish,url,assets | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {{ throw "gh release view failed" }}
if ($release.tagName -ne "{version}" -or
    $release.name -ne "ClipVault Personal {version}" -or
    -not $release.isDraft -or $release.isPrerelease -or
    $release.targetCommitish -ne $targetCommit) {{
  throw "Draft Release metadata changed after QA"
}}
& $ghPath release download {version} `
  --repo selinyi123/clipvault-personal `
  --dir $prepublishRoot
if ($LASTEXITCODE -ne 0) {{ throw "gh release download failed" }}
$prepublishDigests = @(Get-ChildItem $prepublishRoot -File | Sort-Object Name | ForEach-Object {{
  "{{0}}  {{1}}" -f (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLowerInvariant(), $_.Name
}})
if (@(Compare-Object @(Get-Content $digestReport) $prepublishDigests).Count -ne 0) {{
  throw "Draft assets changed after QA; discard approval and repeat verification"
}}

# The earlier evidence JSON is only a snapshot. Re-run the strict verifier against
# the freshly downloaded draft immediately before the irreversible publication
# action so current main, run identity, draft metadata/API digests, attestations,
# bytes, and the Owner signer trust anchor are all checked again.
if ($env:ANDROID_RELEASE_CERT_SHA256 -cnotmatch '^[0-9a-f]{{64}}$') {{
  throw "Set the independently confirmed 64-lowercase-hex ANDROID_RELEASE_CERT_SHA256 locally"
}}
if ([string]::IsNullOrWhiteSpace($env:APKSIGNER_JAR_PATH) -or
    -not (Test-FullyQualifiedWindowsPath $env:APKSIGNER_JAR_PATH)) {{
  throw "Set APKSIGNER_JAR_PATH to an absolute Android SDK lib/apksigner.jar path"
}}
$apksignerItem = Get-Item -LiteralPath $env:APKSIGNER_JAR_PATH -Force -ErrorAction Stop
Assert-NoReparsePathComponent $apksignerItem "APKSIGNER_JAR_PATH"
$apksignerPath = $apksignerItem.FullName
if ($apksignerItem.PSIsContainer -or
    [IO.Path]::GetExtension($apksignerPath).ToLowerInvariant() -ne ".jar" -or
    $apksignerPath.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $apksignerPath.StartsWith("$repoRoot$separator", [StringComparison]::OrdinalIgnoreCase)) {{
  throw "APKSIGNER_JAR_PATH must be a trusted non-reparse .jar outside the workspace"
}}
if ([string]::IsNullOrWhiteSpace($env:JAVA_EXE_PATH) -or
    -not (Test-FullyQualifiedWindowsPath $env:JAVA_EXE_PATH)) {{
  throw "Set JAVA_EXE_PATH to an absolute trusted java.exe"
}}
$javaItem = Get-Item -LiteralPath $env:JAVA_EXE_PATH -Force -ErrorAction Stop
Assert-NoReparsePathComponent $javaItem "JAVA_EXE_PATH"
$javaPath = $javaItem.FullName
if ($javaItem.PSIsContainer -or
    [IO.Path]::GetExtension($javaPath).ToLowerInvariant() -ne ".exe" -or
    $javaPath.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $javaPath.StartsWith("$repoRoot$separator", [StringComparison]::OrdinalIgnoreCase)) {{
  throw "JAVA_EXE_PATH must be a trusted non-reparse .exe outside the workspace"
}}
$run = & $ghPath run view $runId `
  --repo selinyi123/clipvault-personal `
  --json displayTitle,event,headBranch,headSha,conclusion,url | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {{ throw "pre-publication gh run view failed" }}
if ($run.displayTitle -ne "Release artifacts {version} from main draft=true" -or
    $run.event -ne "workflow_dispatch" -or $run.headBranch -ne "main" -or
    $run.headSha -ne $targetCommit -or $run.conclusion -ne "success") {{
  throw "Run identity changed after QA"
}}
$prepublishEvidence = "$artifactRoot/prepublish-live-artifact-evidence.json"
$prepublishComment = "$artifactRoot/prepublish-live-artifact-comment.md"
if ((Test-Path $prepublishEvidence) -or (Test-Path $prepublishComment)) {{
  throw "Refusing stale pre-publication evidence outputs"
}}
$freshProjectionJson = & $pythonPath -I -S $evidenceTool `
  --windows-dir "$actionsRoot/clipvault-windows-release-artifacts" `
  --android-dir "$actionsRoot/clipvault-android-signed-release-artifacts" `
  --draft-release-dir "$prepublishRoot" `
  --gh "$ghPath" `
  --apksigner "$apksignerPath" `
  --java "$javaPath" `
  --version {version} `
  --commit $targetCommit `
  --run-url $run.url `
  --expected-android-cert-sha256 $env:ANDROID_RELEASE_CERT_SHA256 `
  --require-live-final-draft `
  --owner-approved-binding $ownerApprovedBinding `
  --publication-projection-stdout `
  --evidence-output $prepublishEvidence `
  --comment-output $prepublishComment | Out-String
if ($LASTEXITCODE -ne 0) {{ throw "fresh pre-publication evidence verification failed" }}
if (-not (Test-Path -LiteralPath $prepublishEvidence -PathType Leaf) -or
    -not (Test-Path -LiteralPath $prepublishComment -PathType Leaf) -or
    (Get-Item -LiteralPath $prepublishEvidence).Length -le 0 -or
    (Get-Item -LiteralPath $prepublishComment).Length -le 0) {{
  throw "fresh pre-publication evidence outputs are missing or empty"
}}
$freshEvidence = $freshProjectionJson | ConvertFrom-Json
if ($freshEvidence.projection_status -ne "owner_approved_live_snapshot" -or
    $freshEvidence.target_commit -ne $targetCommit -or
    $freshEvidence.workflow_run.id -ne [long]$runId -or
    $freshEvidence.workflow_run.url -ne $run.url -or
    $freshEvidence.draft_release.is_draft -ne $true -or
    $freshEvidence.release_tag.ref -cne "refs/tags/{version}" -or
    @("absent", "present") -cnotcontains [string]$freshEvidence.release_tag.state -or
    ($freshEvidence.release_tag.state -ceq "present" -and
      $freshEvidence.release_tag.commit_sha -cne $targetCommit) -or
    $freshEvidence.artifact_binding_sha256 -cne $ownerApprovedBinding -or
    $freshEvidence.android_signer.expected_cert_sha256 -cne $env:ANDROID_RELEASE_CERT_SHA256) {{
  throw "fresh evidence is not bound to the Owner-approved draft, run, bytes, and signer"
}}

$currentMain = & $ghPath api -X GET `
  --hostname github.com `
  "repos/selinyi123/clipvault-personal/branches/main" `
  --jq .commit.sha
if ($LASTEXITCODE -ne 0) {{ throw "current main lookup failed" }}
if ($currentMain.Trim() -ne $targetCommit) {{
  throw "Current main moved after QA; do not publish"
}}
$finalLocalCommit = (& $gitPath -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {{ throw "final local HEAD lookup failed" }}
$finalStatus = @(& $gitPath -C $repoRoot -c core.fsmonitor=false status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) {{ throw "final git status failed" }}
if ($finalLocalCommit -ne $targetCommit -or $finalStatus.Count -ne 0) {{
  throw "Release verifier checkout changed after QA; do not publish"
}}
Assert-TrackedSourceMatchesCommit "tools/release_artifact_evidence.py"
Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"

$releaseId = [long]$freshEvidence.draft_release.id
$releaseEndpoint = "repos/selinyi123/clipvault-personal/releases/$releaseId"
$approvedAssetNames = @(Get-Content $digestReport | ForEach-Object {{
  ($_ -split '\\s{{2,}}', 2)[1]
}} | Sort-Object)
function Assert-ReleaseAssetsMatchEvidence($releaseRecord, $evidenceRows, [string]$label) {{
  if (@($releaseRecord.assets).Count -ne @($evidenceRows).Count) {{
    throw "$label asset count mismatch"
  }}
  foreach ($row in @($evidenceRows)) {{
    $matches = @($releaseRecord.assets | Where-Object name -CEQ $row.release_name)
    $expectedDigest = "sha256:$($row.sha256)"
    if ($matches.Count -ne 1 -or
        [long]$matches[0].id -ne [long]$row.release_asset_id -or
        [long]$matches[0].size -ne [long]$row.size_bytes -or
        $matches[0].state -ne "uploaded" -or
        $matches[0].digest -cne $expectedDigest) {{
      throw "$label asset API identity/size/digest mismatch: $($row.release_name)"
    }}
  }}
}}
$liveDraft = & $ghPath api -X GET --hostname github.com $releaseEndpoint | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {{ throw "exact draft Release lookup failed" }}
$liveDraftAssetNames = @($liveDraft.assets | ForEach-Object name | Sort-Object)
if ($liveDraft.id -ne $releaseId -or
    $liveDraft.tag_name -ne "{version}" -or
    $liveDraft.name -ne "ClipVault Personal {version}" -or
    -not $liveDraft.draft -or $liveDraft.prerelease -or
    $liveDraft.target_commitish -ne $targetCommit -or
    @(Compare-Object $approvedAssetNames $liveDraftAssetNames).Count -ne 0 -or
    @($liveDraft.assets | Where-Object size -LE 0).Count -ne 0) {{
  throw "exact draft Release changed after fresh verification"
}}
Assert-ReleaseAssetsMatchEvidence $liveDraft $freshEvidence.artifacts "pre-publication"
$immediateMain = & $ghPath api -X GET `
  --hostname github.com `
  "repos/selinyi123/clipvault-personal/branches/main" `
  --jq .commit.sha
if ($LASTEXITCODE -ne 0) {{ throw "immediate pre-publication main lookup failed" }}
if ($immediateMain.Trim() -ne $targetCommit) {{
  throw "Current main moved immediately before publication; do not publish"
}}
$immediateTagCommit = Resolve-ExactReleaseTagCommit
if (($freshEvidence.release_tag.state -ceq "absent" -and $null -ne $immediateTagCommit) -or
    ($freshEvidence.release_tag.state -ceq "present" -and
      $immediateTagCommit -cne $targetCommit)) {{
  throw "Release tag changed after Owner approval; do not publish"
}}

& $ghPath api -X PATCH --hostname github.com $releaseEndpoint -F draft=false | Out-Null
if ($LASTEXITCODE -ne 0) {{ throw "GitHub Release publication failed" }}

$published = & $ghPath api -X GET --hostname github.com $releaseEndpoint | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {{ throw "post-publication exact Release lookup failed" }}
if ($published.id -ne $releaseId -or
    $published.tag_name -ne "{version}" -or
    $published.name -ne "ClipVault Personal {version}" -or
    $published.draft -or $published.prerelease -or
    $published.target_commitish -ne $targetCommit) {{
  throw "Published Release metadata mismatch"
}}
$publishedTagCommit = Resolve-ExactReleaseTagCommit
if ($publishedTagCommit -cne $targetCommit) {{
  throw "Published release tag does not resolve to the frozen target"
}}
$publishedAssetNames = @($published.assets | ForEach-Object name | Sort-Object)
if (@(Compare-Object $approvedAssetNames $publishedAssetNames).Count -ne 0 -or
    @($published.assets | Where-Object size -LE 0).Count -ne 0) {{
  throw "Published Release asset inventory mismatch"
}}
Assert-ReleaseAssetsMatchEvidence $published $freshEvidence.artifacts "post-publication"

$postpublishRoot = ".field-test-artifacts/v1.6.0-postpublish-$runId"
if (Test-Path $postpublishRoot) {{ throw "Refusing stale post-publish directory" }}
New-Item -ItemType Directory -Path $postpublishRoot | Out-Null
& $ghPath release download {version} `
  --repo selinyi123/clipvault-personal `
  --dir $postpublishRoot
if ($LASTEXITCODE -ne 0) {{ throw "post-publication gh release download failed" }}
$postFiles = @(Get-ChildItem $postpublishRoot -File)
if ($postFiles.Count -ne @($freshEvidence.artifacts).Count) {{
  throw "Published download asset count mismatch"
}}
foreach ($row in @($freshEvidence.artifacts)) {{
  $path = Join-Path $postpublishRoot $row.release_name
  if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or
      (Get-Item -LiteralPath $path).Length -ne [long]$row.size_bytes -or
      (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -cne $row.sha256) {{
    throw "Published asset bytes differ from the Owner-approved binding: $($row.release_name)"
  }}
}}

# Re-run the repository verifier against the published Release. This reconstructs
# the Owner-approved pre-publication binding from live state, verifies the exact
# published Release/tag/asset identities and attestations, then emits a separate
# publication-closure binding. Outputs stay outside all verified byte directories.
$postpublishEvidence = "$artifactRoot/postpublish-live-artifact-evidence.json"
$postpublishComment = "$artifactRoot/postpublish-live-artifact-comment.md"
if ((Test-Path $postpublishEvidence) -or (Test-Path $postpublishComment)) {{
  throw "Refusing stale post-publication evidence outputs"
}}
Assert-TrackedSourceMatchesCommit "tools/release_artifact_evidence.py"
Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"
& $pythonPath -I -S $evidenceTool `
  --windows-dir "$actionsRoot/clipvault-windows-release-artifacts" `
  --android-dir "$actionsRoot/clipvault-android-signed-release-artifacts" `
  --published-release-dir "$postpublishRoot" `
  --gh "$ghPath" `
  --apksigner "$apksignerPath" `
  --java "$javaPath" `
  --version {version} `
  --commit $targetCommit `
  --run-url $run.url `
  --expected-android-cert-sha256 $env:ANDROID_RELEASE_CERT_SHA256 `
  --owner-approved-binding $ownerApprovedBinding `
  --require-live-published-release `
  --evidence-output $postpublishEvidence `
  --comment-output $postpublishComment
if ($LASTEXITCODE -ne 0) {{ throw "post-publication live evidence verification failed" }}
if (-not (Test-Path -LiteralPath $postpublishEvidence -PathType Leaf) -or
    -not (Test-Path -LiteralPath $postpublishComment -PathType Leaf) -or
    (Get-Item -LiteralPath $postpublishEvidence).Length -le 0 -or
    (Get-Item -LiteralPath $postpublishComment).Length -le 0) {{
  throw "post-publication evidence outputs are missing or empty"
}}
# This is an output-integrity sanity check only. The JSON is not a new approval
# source and does not authorize Issue closure or any further Release mutation.
$postpublishReport = Get-Content -LiteralPath $postpublishEvidence -Raw | ConvertFrom-Json
if ($postpublishReport.evidence_type -cne "clipvault.issue36.published_release" -or
    $postpublishReport.target_commit -cne $targetCommit -or
    $postpublishReport.workflow_run.id -ne [long]$runId -or
    $postpublishReport.workflow_run.url -cne $run.url -or
    $postpublishReport.owner_approved_artifact_binding_sha256 -cne $ownerApprovedBinding -or
    $postpublishReport.published_release.id -ne $releaseId -or
    $postpublishReport.published_release.is_draft -ne $false -or
    $postpublishReport.published_release.is_prerelease -ne $false -or
    $postpublishReport.release_tag.ref -cne "refs/tags/{version}" -or
    $postpublishReport.release_tag.commit_sha -cne $targetCommit -or
    $postpublishReport.android_signer.expected_cert_sha256 -cne $env:ANDROID_RELEASE_CERT_SHA256 -or
    $postpublishReport.publication_closure_binding_sha256 -cnotmatch '^[0-9a-f]{{64}}$') {{
  throw "post-publication evidence is not bound to the approved release state"
}}
$finalPublishedLocalCommit = (& $gitPath -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {{ throw "final published-verifier HEAD lookup failed" }}
$finalPublishedStatus = @(& $gitPath -C $repoRoot -c core.fsmonitor=false status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) {{ throw "final published-verifier git status failed" }}
if ($finalPublishedLocalCommit -ne $targetCommit -or $finalPublishedStatus.Count -ne 0) {{
  throw "Published release verifier checkout changed during validation"
}}
Assert-TrackedSourceMatchesCommit "tools/release_artifact_evidence.py"
Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"
```

The exact-ID REST `PATCH` is an Owner publication action and must run only after
the bound approval described above, while `main` and `refs/tags/{version}` are
frozen and in an Owner-exclusive Release mutation window. GitHub does not make
branch/tag movement, asset mutation, and draft publication one atomic operation,
so the commands minimize that residual race and then recheck the same Release ID,
resolved tag commit, metadata, inventory, sizes, and bytes after publication.
The final verifier output supplies a path-free comment draft and a distinct
publication-closure binding; both remain evidence inputs rather than closure
authorization. Issue #36 remains open until readiness independently reports no
blocker and the Owner confirms every remaining manual gate.

If the exact-ID `PATCH` returned success but any later command failed, treat the
Release as already published. Do not rebuild, mutate the Release, or rerun the
full Step H draft path. Keep Issue #36 open, preserve the Owner-approved binding,
use a new post-publish download directory and new evidence/comment filenames,
then rerun only the read-only `--require-live-published-release` invocation from
the same frozen checkout. If the PowerShell session or trusted-path variables
were lost, follow the post-publication recovery procedure in
`docs/RELEASE_RUNBOOK_V1_6_0.md` before recording any closure evidence.

### Step H recovery - read-only after a successful PATCH

Use this block only in the same trusted PowerShell session after Step H reached
the publication `PATCH`. It never mutates the Release. If that session was lost,
first re-establish the exact frozen placeholders, trusted tool paths, environment
certificate input, helper functions, and clean checkout checks from a fresh pack;
do not execute the draft checks or `PATCH` again.

```powershell
$recoveryWorkingDirectory = [IO.Path]::GetFullPath((Get-Location).Path).TrimEnd('\', '/')
if (-not $recoveryWorkingDirectory.Equals($repoRoot, [StringComparison]::OrdinalIgnoreCase)) {{
  throw "Run post-publication recovery from the repository root"
}}
$recoveryNonce = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ") + "-" + [Guid]::NewGuid().ToString("N")
$recoveryRoot = ".field-test-artifacts/v1.6.0-postpublish-recovery-$runId-$recoveryNonce"
$recoveryEvidence = "$artifactRoot/postpublish-live-artifact-evidence-$recoveryNonce.json"
$recoveryComment = "$artifactRoot/postpublish-live-artifact-comment-$recoveryNonce.md"
if ((Test-Path $recoveryRoot) -or (Test-Path $recoveryEvidence) -or
    (Test-Path $recoveryComment)) {{
  throw "Refusing stale post-publication recovery paths"
}}
$recoveryLocalCommit = (& $gitPath -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {{ throw "recovery local HEAD lookup failed" }}
$recoveryStatus = @(& $gitPath -C $repoRoot -c core.fsmonitor=false status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) {{ throw "recovery git status failed" }}
if ($recoveryLocalCommit -ne $targetCommit -or $recoveryStatus.Count -ne 0) {{
  throw "Post-publication recovery requires the exact clean frozen target"
}}
Assert-TrackedSourceMatchesCommit "tools/release_artifact_evidence.py"
Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"
New-Item -ItemType Directory -Path $recoveryRoot | Out-Null
& $ghPath release download {version} `
  --repo selinyi123/clipvault-personal `
  --dir $recoveryRoot
if ($LASTEXITCODE -ne 0) {{ throw "post-publication recovery download failed" }}
& $pythonPath -I -S $evidenceTool `
  --windows-dir "$actionsRoot/clipvault-windows-release-artifacts" `
  --android-dir "$actionsRoot/clipvault-android-signed-release-artifacts" `
  --published-release-dir "$recoveryRoot" `
  --gh "$ghPath" `
  --apksigner "$apksignerPath" `
  --java "$javaPath" `
  --version {version} `
  --commit $targetCommit `
  --run-url $run.url `
  --expected-android-cert-sha256 $env:ANDROID_RELEASE_CERT_SHA256 `
  --owner-approved-binding $ownerApprovedBinding `
  --require-live-published-release `
  --evidence-output $recoveryEvidence `
  --comment-output $recoveryComment
if ($LASTEXITCODE -ne 0) {{ throw "post-publication recovery verification failed" }}
if (-not (Test-Path -LiteralPath $recoveryEvidence -PathType Leaf) -or
    -not (Test-Path -LiteralPath $recoveryComment -PathType Leaf) -or
    (Get-Item -LiteralPath $recoveryEvidence).Length -le 0 -or
    (Get-Item -LiteralPath $recoveryComment).Length -le 0) {{
  throw "post-publication recovery outputs are missing or empty"
}}
$finalRecoveryCommit = (& $gitPath -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {{ throw "final recovery HEAD lookup failed" }}
$finalRecoveryStatus = @(& $gitPath -C $repoRoot -c core.fsmonitor=false status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) {{ throw "final recovery git status failed" }}
if ($finalRecoveryCommit -ne $targetCommit -or $finalRecoveryStatus.Count -ne 0) {{
  throw "Post-publication recovery checkout changed during validation"
}}
Assert-TrackedSourceMatchesCommit "tools/release_artifact_evidence.py"
Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"
```

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
