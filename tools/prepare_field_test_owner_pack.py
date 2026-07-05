#!/usr/bin/env python3
"""Prepare a local Owner action pack for the Issue #82 v1.7 field test.

The pack turns the live, read-only readiness state into concrete Owner-facing
files: a command checklist, a prefilled evidence JSON template, and a rendered
Issue #82 comment draft. It intentionally does not download artifacts, install
apps, run device QA, post comments, edit issues, sign or publish releases,
close Issue #82/#36, or claim v1.7 stable.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = Path("field-test-owner-pack")
DEFAULT_TESTER = "Owner"
DEFAULT_ANDROID_APK = "ClipVault-Android-v1.6.0-debug.apk"


def _load_local_module(name: str, rel_path: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


field_test_readiness = _load_local_module(
    "field_test_readiness_for_owner_pack",
    "tools/field_test_readiness.py",
)
field_test_evidence = _load_local_module(
    "field_test_evidence_for_owner_pack",
    "tools/field_test_evidence.py",
)


@dataclass(frozen=True)
class PackPaths:
    output_dir: Path
    guide: Path
    evidence_json: Path
    issue_comment: Path
    summary_json: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "output_dir": str(self.output_dir),
            "guide": str(self.guide),
            "evidence_json": str(self.evidence_json),
            "issue_comment": str(self.issue_comment),
            "summary_json": str(self.summary_json),
        }


def scope_note() -> str:
    return (
        "Owner action pack only. It does not download artifacts, install apps, "
        "run device QA, post comments, edit issues, sign or publish releases, "
        "close Issue #82/#36, or claim v1.7 stable."
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _gate_by_name(report: dict[str, Any], name: str) -> dict[str, Any]:
    for gate in report.get("gates", []):
        if isinstance(gate, dict) and gate.get("name") == name:
            return gate
    return {}


def _artifact_metadata(report: dict[str, Any]) -> list[dict[str, Any]]:
    gate = _gate_by_name(report, "candidate artifact inventory")
    metadata = gate.get("metadata") if isinstance(gate, dict) else None
    artifacts = metadata.get("artifacts") if isinstance(metadata, dict) else None
    return [row for row in artifacts if isinstance(row, dict)] if isinstance(artifacts, list) else []


def _issue_unchecked(report: dict[str, Any]) -> list[str]:
    gate = _gate_by_name(report, "Issue #82")
    metadata = gate.get("metadata") if isinstance(gate, dict) else None
    unchecked = metadata.get("unchecked_items") if isinstance(metadata, dict) else None
    return [str(row) for row in unchecked] if isinstance(unchecked, list) else []


def _status(report: dict[str, Any], name: str) -> str:
    gate = _gate_by_name(report, name)
    return str(gate.get("status", "unknown")) if gate else "missing"


def _prefilled_template(
    *,
    report: dict[str, Any],
    tester: str,
    tested_at: str,
    windows_dir: Path | None,
    android_dir: Path | None,
) -> dict[str, Any]:
    main_sha = str(report.get("main_sha") or "")
    ci_run_url = str(report.get("ci_run_url") or "")
    candidate_run_url = str(report.get("candidate_run_url") or "")
    source_version = str(report.get("source_version") or field_test_evidence.DEFAULT_SOURCE_VERSION)
    repo = str(report.get("repo") or field_test_evidence.DEFAULT_REPO)

    if windows_dir and android_dir:
        data = field_test_evidence.build_artifact_verified_template(
            windows_dir=windows_dir,
            android_dir=android_dir,
            target_commit=main_sha,
            ci_run_url=ci_run_url,
            candidate_run_url=candidate_run_url,
            tester=tester,
            tested_at=tested_at,
            source_version=source_version,
            repo=repo,
        )
    else:
        data = field_test_evidence.build_template(source_version=source_version, repo=repo)
        data.update({
            "target_commit": main_sha,
            "ci_run_url": ci_run_url,
            "candidate_run_url": candidate_run_url,
            "tester": tester,
            "tested_at": tested_at,
            "windows_environment": {
                "os": platform.platform(),
                "artifact_name": field_test_evidence.EXPECTED_WINDOWS_ARTIFACT_NAME,
                "portable_or_installer": "pending Owner Windows smoke",
            },
            "android_device": {
                "model": "pending Owner Android smoke",
                "android_version": "pending Owner Android smoke",
                "artifact_name": field_test_evidence.EXPECTED_ANDROID_ARTIFACT_NAME,
                "install_apk": f"ClipVault-Android-v{source_version}-debug.apk",
            },
        })
    data["scope_note"] = field_test_evidence.scope_note()
    return data


def _render_artifacts_table(artifacts: list[dict[str, Any]]) -> str:
    if not artifacts:
        return "_No artifact metadata available in the readiness report._"
    lines = [
        "| Artifact | ID | Size | Expires | Digest |",
        "|---|---:|---:|---|---|",
    ]
    for row in artifacts:
        lines.append(
            "| "
            f"{row.get('name', '-')} | "
            f"{row.get('id', '-')} | "
            f"{row.get('size_in_bytes', '-')} | "
            f"{row.get('expires_at', '-')} | "
            f"{row.get('digest', '-')} |"
        )
    return "\n".join(lines)


def _render_owner_guide(
    *,
    report: dict[str, Any],
    evidence_json: Path,
    issue_comment: Path,
    artifacts: list[dict[str, Any]],
) -> str:
    repo = str(report.get("repo") or field_test_readiness.DEFAULT_REPO)
    main_sha = str(report.get("main_sha") or "UNKNOWN_MAIN_SHA")
    ci_run_url = str(report.get("ci_run_url") or "UNKNOWN_CI_RUN_URL")
    candidate_run_url = str(report.get("candidate_run_url") or "UNKNOWN_RELEASE_CANDIDATE_RUN_URL")
    candidate_run_id = candidate_run_url.rstrip("/").split("/")[-1]
    source_version = str(report.get("source_version") or field_test_evidence.DEFAULT_SOURCE_VERSION)
    unchecked = _issue_unchecked(report)

    unchecked_lines = "\n".join(f"- {item}" for item in unchecked) or "- None in issue body."
    return f"""# ClipVault v1.7 Owner field-test action pack

Status: **not stable yet**

This pack is generated from the current live readiness state.

- Repository: `{repo}`
- Target main SHA: `{main_sha}`
- CI run: {ci_run_url}
- Release-candidate run: {candidate_run_url}
- Source version under test: `{source_version}`
- Evidence JSON: `{evidence_json.name}`
- Rendered Issue #82 comment draft: `{issue_comment.name}`

Scope: {scope_note()}

## Candidate artifact inventory

{_render_artifacts_table(artifacts)}

## Fast path commands

Run from the repository root.

```powershell
$runId = "{candidate_run_id}"
$targetSha = "{main_sha}"
$version = "{source_version}"
$out = "field-test-v1.7"

Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$out/windows" | Out-Null
New-Item -ItemType Directory -Force -Path "$out/android" | Out-Null

gh run download $runId --repo {repo} --name clipvault-windows-release-candidate --dir "$out/windows"
gh run download $runId --repo {repo} --name clipvault-android-release-candidate --dir "$out/android"

python scripts/verify_release_manifest.py --artifact-dir "$out/windows" --platform windows --version $version --commit $targetSha --expect-dry-run
python scripts/verify_release_manifest.py --artifact-dir "$out/android" --platform android --version $version --commit $targetSha --expect-dry-run
```

If the stdlib downloader is preferred:

```powershell
python tools/download_field_test_artifacts.py --run-id {candidate_run_id} --output-dir field-test-v1.7 --target-commit {main_sha} --verify-manifests --clean
```

## Windows Owner smoke

Do not run the installer automatically from an agent. Owner should run it on the target Windows environment and record observations.

Suggested non-destructive portable launch smoke:

```powershell
$dir = Resolve-Path "field-test-v1.7/windows"
$smokeRoot = Join-Path $env:TEMP ("ClipVaultSmoke-" + [guid]::NewGuid().ToString("N"))
$configPath = Join-Path $smokeRoot "config.toml"
$vaultPath = Join-Path $smokeRoot "vault"
$dbPath = Join-Path $smokeRoot "data/clipvault.db"
$logDir = Join-Path $smokeRoot "logs"
$port = 18787

New-Item -ItemType Directory -Force -Path $vaultPath | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $dbPath) | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$dbPathToml = $dbPath -replace "\\\\", "/"
$vaultPathToml = $vaultPath -replace "\\\\", "/"
$logDirToml = $logDir -replace "\\\\", "/"
@"
[device]
device_id = "01JZ7FIELDTEST000000000000"
device_name = "desktop-field-test"

[storage]
db_path = "$dbPathToml"
max_clip_bytes = 1048576

[watcher]
poll_fallback_ms = 500

[obsidian]
vault_path = "$vaultPathToml"

[backup]
repo_path = ""
interval_minutes = 15
enabled = false

[server]
host = "127.0.0.1"
port = $port

[log]
dir = "$logDirToml"
retention_days = 14
"@ | Set-Content -Encoding UTF8 -LiteralPath $configPath

$p = $null
try {{
  $p = Start-Process -FilePath (Join-Path $dir "ClipVault-Desktop-v{source_version}-portable.exe") -ArgumentList @("--config", $configPath, "--headless", "--no-open") -PassThru -WindowStyle Hidden
  Start-Sleep -Seconds 5
  Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health"
}} finally {{
  if ($p -and -not $p.HasExited) {{ Stop-Process -Id $p.Id -Force }}
}}
```

Owner-only rows to record:

- portable launch result;
- installer install result;
- normal clipboard capture result;
- LAN/Tailscale sync smoke with Android or another node;
- uninstall/cleanup result.

## Android Owner smoke

`adb` must be available and a real device or approved emulator must be attached.

```powershell
$apk = "field-test-v1.7/android/ClipVault-Android-v{source_version}-debug.apk"
$pkg = "com.clipvault.app"
$panelIme = "com.clipvault.app/.ime.ClipVaultPanelImeService"
$fullIme = "com.clipvault.app/.ime.ClipVaultFullKeyboardService"

adb devices -l
adb install -r $apk
adb shell pm list packages $pkg
adb shell am start -n com.clipvault.app/.ui.MainActivity

$previousIme = (adb shell settings get secure default_input_method).Trim()
try {{
  adb shell ime list -s
  adb shell ime enable $panelIme
  adb shell ime set $panelIme
  adb shell settings get secure default_input_method

  $marker = "ClipVault_SHARE_SMOKE_$(Get-Date -Format yyyyMMddHHmmss)"
  adb shell am start -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT "$marker" -n com.clipvault.app/.share.ShareReceiverActivity

  $canary = "CV_NO_TYPED_LOG_CANARY_$(Get-Date -Format yyyyMMddHHmmss)"
  adb logcat -c
  # Owner manually types the canary into a normal input, then verifies password/incognito fields hide candidates.
  if (adb logcat -d | Select-String -SimpleMatch $canary) {{
    throw "typed-text canary appeared in logcat; record this as a privacy failure"
  }}
}} finally {{
  if ($previousIme) {{ adb shell ime set $previousIme }}
}}
```

Do not type real passwords or secrets during logcat checks.

## Render and upload evidence after Owner fills `{evidence_json.name}`

```powershell
python tools/field_test_evidence.py --input "{evidence_json}" --no-fail
python tools/field_test_evidence.py --input "{evidence_json}" --output "{issue_comment}"
gh issue comment 82 --repo {repo} --body-file "{issue_comment}"
```

## Current Issue #82 unchecked rows

{unchecked_lines}

## Current gate snapshot

- Current main commit: {_status(report, "current main commit")}
- CI: {_status(report, "CI")}
- Release candidate dry run: {_status(report, "Release candidate dry run")}
- Candidate artifact inventory: {_status(report, "candidate artifact inventory")}
- Issue #82 checklist: {_status(report, "Issue #82")}
- Issue #82 current-run evidence markers: {_status(report, "Issue #82 current-run evidence markers")}
"""


def build_pack(
    *,
    output_dir: Path,
    tester: str,
    tested_at: str,
    windows_dir: Path | None = None,
    android_dir: Path | None = None,
    repo: str = field_test_readiness.DEFAULT_REPO,
    branch: str = field_test_readiness.DEFAULT_BRANCH,
    source_version: str = field_test_readiness.DEFAULT_SOURCE_VERSION,
) -> dict[str, Any]:
    if (windows_dir is None) != (android_dir is None):
        raise ValueError("--windows-dir and --android-dir must be provided together")

    report = field_test_readiness.build_report(
        repo=repo,
        branch=branch,
        source_version=source_version,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = PackPaths(
        output_dir=output_dir,
        guide=output_dir / "OWNER_FIELD_TEST_ACTION_PACK.md",
        evidence_json=output_dir / "field-test-v1.7.json",
        issue_comment=output_dir / "field-test-v1.7-issue-comment.md",
        summary_json=output_dir / "pack-summary.json",
    )

    data = _prefilled_template(
        report=report,
        tester=tester,
        tested_at=tested_at,
        windows_dir=windows_dir,
        android_dir=android_dir,
    )
    result = field_test_evidence.validate_evidence(data)
    comment = field_test_evidence.render_markdown(data, result)
    artifacts = _artifact_metadata(report)
    guide = _render_owner_guide(
        report=report,
        evidence_json=paths.evidence_json,
        issue_comment=paths.issue_comment,
        artifacts=artifacts,
    )

    paths.evidence_json.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths.issue_comment.write_text(comment, encoding="utf-8")
    paths.guide.write_text(guide, encoding="utf-8")
    summary = {
        "status": report.get("status"),
        "blocked": report.get("blocked"),
        "warnings": report.get("warnings"),
        "main_sha": report.get("main_sha"),
        "ci_run_url": report.get("ci_run_url"),
        "candidate_run_url": report.get("candidate_run_url"),
        "field_test_ready": result.field_test_ready,
        "item_counts": result.item_counts,
        "paths": paths.as_dict(),
        "scope_note": scope_note(),
    }
    paths.summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare an Owner action pack for v1.7 field testing.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--tester", default=DEFAULT_TESTER)
    parser.add_argument("--tested-at", default=_now_iso())
    parser.add_argument("--repo", default=field_test_readiness.DEFAULT_REPO)
    parser.add_argument("--branch", default=field_test_readiness.DEFAULT_BRANCH)
    parser.add_argument("--source-version", default=field_test_readiness.DEFAULT_SOURCE_VERSION)
    parser.add_argument("--windows-dir", type=Path, help="optional downloaded Windows candidate artifact directory")
    parser.add_argument("--android-dir", type=Path, help="optional downloaded Android candidate artifact directory")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        summary = build_pack(
            output_dir=args.output_dir,
            tester=args.tester,
            tested_at=args.tested_at,
            windows_dir=args.windows_dir,
            android_dir=args.android_dir,
            repo=args.repo,
            branch=args.branch,
            source_version=args.source_version,
        )
    except Exception as exc:
        print(f"field-test owner pack failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        paths = summary["paths"]
        assert isinstance(paths, dict)
        print("ClipVault v1.7 Owner field-test action pack prepared")
        print(f"output_dir: {paths['output_dir']}")
        print(f"guide: {paths['guide']}")
        print(f"evidence_json: {paths['evidence_json']}")
        print(f"issue_comment: {paths['issue_comment']}")
        print(scope_note())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
