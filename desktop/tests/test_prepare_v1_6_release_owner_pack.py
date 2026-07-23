"""Tests for the fixed-scope Issue #36 Owner pack generator."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import py_compile
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "prepare_v1_6_release_owner_pack.py"
SPEC = importlib.util.spec_from_file_location("prepare_v1_6_release_owner_pack", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
owner_pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(owner_pack)


def _read_bytes(output_dir: Path) -> dict[str, bytes]:
    return {
        name: (output_dir / name).read_bytes()
        for name in owner_pack.EXPECTED_OUTPUT_FILES
    }


def test_local_module_loader_ignores_unchecked_hash_bytecode_cache(
    tmp_path: Path,
    monkeypatch,
):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    trusted_source = tools_dir / "trusted_helper.py"
    trusted_source.write_text("VALUE = 'trusted-source'\n", encoding="utf-8")
    cache_path = Path(importlib.util.cache_from_source(str(trusted_source)))
    cache_path.parent.mkdir()
    attacker_source = tmp_path / "attacker.py"
    attacker_source.write_text("VALUE = 'ignored-bytecode'\n", encoding="utf-8")
    py_compile.compile(
        str(attacker_source),
        cfile=str(cache_path),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    monkeypatch.setattr(owner_pack, "ROOT", tmp_path)

    module = owner_pack._load_local_module(
        "owner_pack_no_bytecode_test",
        "tools/trusted_helper.py",
    )

    assert module.VALUE == "trusted-source"


def test_manual_template_reuses_canonical_schema_v4_contract():
    generated = owner_pack.manual_qa_template(owner_pack.VERSION)
    canonical = owner_pack.manual_qa_evidence.build_template(owner_pack.VERSION)

    assert generated == canonical
    assert generated["schema_version"] == 4
    assert len(generated["android_runs"]) == 3
    assert [run["sdk_int"] for run in generated["android_runs"][:2]] == [26, 27]
    assert generated["android_runs"][2]["apk_name"] == (
        "ClipVault-Android-v1.6.0-release-signed.apk"
    )


def test_release_workflow_and_owner_pack_share_exact_canonical_release_body():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"(?ms)^\s*cat > release-notes\.md <<EOF\r?\n(?P<body>.*?)^\s*EOF\r?$",
        workflow,
    )
    assert match is not None
    workflow_body = textwrap.dedent(match.group("body")).rstrip("\r\n")
    workflow_body = (
        workflow_body
        .replace("${RELEASE_TAG}", owner_pack.VERSION)
        .replace(
            "${old_android_cert_sha256}",
            owner_pack.OLD_ANDROID_CERT_SHA256,
        )
        .replace(
            "${ANDROID_RELEASE_CERT_SHA256}",
            owner_pack.NEW_ANDROID_CERT_SHA256,
        )
    )

    assert workflow_body == owner_pack.SIGNING_RESET_RELEASE_BODY
    expected_items = 1 + sum(
        len(section.items) for section in owner_pack.manual_qa_evidence.REQUIRED_SECTIONS
    )
    assert expected_items == 26


def test_artifact_worksheet_is_scoped_and_never_claims_validation():
    worksheet = owner_pack.artifact_template(owner_pack.VERSION)

    assert worksheet["coordination_only"] is True
    assert worksheet["validator_input"] is False
    assert worksheet["android"]["expected_assets"][0] == (
        "ClipVault-Android-v1.6.0-release-signed.apk"
    )
    assert "BLOCKED_UNTIL_ANDROID_RELEASE_CERT_SHA256" in (
        worksheet["android"]["owner_certificate_identity"]
    )
    assert "OWNER_APPROVED_64_HEX_BINDING" in (
        worksheet["owner_approved_artifact_binding_sha256"]
    )
    assert "GH_EXE_PATH" in worksheet["gh_cli_path"]
    assert "GIT_EXE_PATH" in worksheet["git_exe_path"]
    assert "PYTHON_EXE_PATH" in worksheet["python_exe_path"]
    assert "APKSIGNER_JAR" in worksheet["apksigner_jar_path"]
    assert "JAVA_EXE_PATH" in worksheet["java_exe_path"]
    assert "does not prove" in worksheet["notes"]


def test_generated_guide_binds_same_draft_bytes_and_fail_closed_manual_qa():
    files = owner_pack.build_pack_files(owner_pack.VERSION, owner_pack.ISSUE_URL)
    guide = files["OWNER_RELEASE_ACTION_PACK.md"]
    draft = files["issue-36-comment-draft.md"]

    assert "create_draft_release=false" in guide
    assert "create_draft_release=true" in guide
    assert "same draft=true run" in guide
    assert "ANDROID_RELEASE_CERT_SHA256" in guide
    assert "environment secrets" in guide
    assert "API 26 and API 27" in guide
    assert "app-debug.apk" in guide
    assert "app-debug-androidTest.apk" in guide
    assert "ClipVault-Android-v1.6.0-release-signed.apk" in guide
    assert "& $pythonPath -I -S $evidenceTool" in guide
    assert "--expected-android-cert-sha256 $expectedAndroidCertSha256" in guide
    assert "--expected-android-cert-sha256 $env:ANDROID_RELEASE_CERT_SHA256" not in guide
    assert "--require-live-final-draft" in guide
    assert '--draft-release-dir "$releaseRoot"' in guide
    assert "GH_CLI_PATH" in guide
    assert "GIT_EXE_PATH" in guide
    assert "PYTHON_EXE_PATH" in guide
    assert "Resolve-TrustedExecutablePath" in guide
    assert guide.count("rev-parse --show-toplevel") == 4
    assert guide.count("Run every Owner release step from the repository root") == 4
    assert "Join-Path $repoRoot \"tools/release_artifact_evidence.py\"" in guide
    assert "& $ghPath run view" in guide
    assert "& $ghPath release edit" not in guide
    assert '--gh "$ghPath"' in guide
    assert '--apksigner "$apksignerPath"' in guide
    assert '--java "$javaPath"' in guide
    assert guide.count('Assert-NoReparsePathComponent $apksignerItem') == 2
    assert guide.count('Assert-NoReparsePathComponent $javaItem') == 2
    assert "apksigner.bat" not in guide
    assert "final-draft-artifact-evidence.json" in guide
    assert "final-draft-artifact-comment.md" in guide
    assert '--final-draft-artifact-evidence "$finalDraftEvidence"' in guide
    assert guide.count("--require-final-draft-binding") == 3
    assert guide.count("--require-release-ready") == 3
    assert "release_artifact_binding" in guide
    assert "artifact_evidence_type <- evidence_type" in guide
    assert "artifacts` row whose role is `android_signed_apk`" in guide
    assert "all eight per-file" in guide
    assert "-cnotmatch '^[0-9a-f]{64}$'" in guide
    assert "& $pythonPath -I -S $manualQaTool" in guide
    assert "manual QA validation failed or remains blocked" in guide
    assert guide.count('$ErrorActionPreference = "Stop"') >= 4
    assert guide.count("Set-StrictMode -Version Latest") == 4
    assert guide.count("function Test-FullyQualifiedWindowsPath") == 4
    assert "TrimEnd('', '/')" not in guide
    assert guide.count(".TrimEnd([char]92, [char]47)") == 9
    assert "IsPathFullyQualified" not in guide
    assert guide.count("function Reset-ReleaseGitEnvironment") == 4
    assert guide.count('$env:GIT_NO_REPLACE_OBJECTS = "1"') == 4
    assert guide.count("function Assert-NoReparsePathComponent") == 4
    assert guide.count("$cursor = $cursor.Directory") == 4
    assert guide.count("Assert-NoReparsePathComponent $gitItem") == 4
    assert guide.count("function Assert-TrackedSourceMatchesCommit") == 3
    assert guide.count("hash-object --no-filters --") == 3
    assert guide.count("Remove-Item Env:GH_FORCE_TTY") == 3
    assert guide.count('$env:GH_PROMPT_DISABLED = "1"') == 3
    assert "fetch origin main" not in guide
    assert '"repos/selinyi123/clipvault-personal/branches/main"' in guide
    assert guide.count(
        'Assert-TrackedSourceMatchesCommit "tools/release_artifact_evidence.py"'
    ) == 12
    assert guide.count(
        'Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"'
    ) == 12
    assert guide.count(
        'Assert-TrackedSourceMatchesCommit "tools/manual_qa_evidence.py"'
    ) == 5
    assert "Manual QA validator must run from the exact clean frozen target" in guide
    assert "Manual QA validator checkout changed during validation" in guide
    assert "Final-draft artifact evidence must be a regular non-reparse file" in guide
    assert "PASS (OWNER-ATTESTED)" in guide
    assert "final_draft_binding_assurance=verified_external_snapshot" in guide
    assert "Do not manually flip" in guide
    assert "v1.6.0-draft-run-$runId" in guide
    assert "Refusing stale evidence directory" in guide
    assert "Assert-NativeSuccess" in guide
    assert "Release artifacts v1.6.0 from main draft=true" in guide
    assert "& $ghPath release download v1.6.0" in guide
    assert "Assert-SameSha" in guide
    assert "draft-release-SHA256SUMS.txt" in guide
    assert "Windows observations are Owner-attested" in guide
    assert "OutboxBaseSeqTest" in guide
    assert "re_pair_outbox_high_water" in guide
    assert "separate filtered" in guide
    assert "never reuse one aggregate XML" in guide
    assert "hash both debug APK inputs after each filtered invocation" in guide
    assert "otherwise discard both results" in guide
    assert "move any connected-test result directory aside" in guide
    assert "abort on a nonzero Gradle exit" in guide

    step_f = guide.split("### Step F - execute manual QA against exact bytes", 1)[1]
    step_f = step_f.split("### Step G - consolidate Issue #36 evidence", 1)[0]
    assert 'v1.6.0-draft-run-$runId' in step_f
    assert step_f.count('--final-draft-artifact-evidence "$finalDraftEvidence"') == 2
    assert step_f.count("--require-final-draft-binding") == 2
    assert step_f.count("--require-release-ready") == 2
    assert "release-artifacts-v1.6.0.template.json" not in step_f
    assert "final-draft-artifact-comment.md" not in step_f
    for relative_path in (
        "tools/manual_qa_evidence.py",
        "tools/release_artifact_evidence.py",
        "scripts/verify_release_manifest.py",
    ):
        assert step_f.count(
            f'Assert-TrackedSourceMatchesCommit "{relative_path}"'
        ) == 3
    first_source_check = step_f.index(
        'Assert-TrackedSourceMatchesCommit "tools/manual_qa_evidence.py"'
    )
    preview = step_f.index("--no-fail")
    middle_source_check = step_f.index(
        'Assert-TrackedSourceMatchesCommit "tools/manual_qa_evidence.py"',
        first_source_check + 1,
    )
    final_render = step_f.index('--output "$pendingOutput"')
    last_source_check = step_f.rindex(
        'Assert-TrackedSourceMatchesCommit "scripts/verify_release_manifest.py"'
    )
    assert (
        first_source_check
        < preview
        < middle_source_check
        < final_render
        < last_source_check
    )
    assert step_f.count("Get-TrustedEvidenceSha256 $manualQaEvidence") == 3
    assert step_f.count("Get-TrustedEvidenceSha256 $finalDraftEvidence") == 3
    assert "Manual QA or final-draft artifact evidence changed after preview" in step_f
    assert "Manual QA or final-draft artifact evidence changed during final render" in step_f

    step_h = guide.split(
        "### Step H - publish the existing draft, then verify published state", 1
    )[1]
    step_h = step_h.split("### Step H recovery - read-only", 1)[0]
    manual_qa_recheck = step_h.index(
        "Manual QA recheck differs from the Owner-approved report"
    )
    final_environment = step_h.index(
        "final release certificate environment variable lookup failed"
    )
    final_main = step_h.index("final pre-publication main lookup failed")
    final_tag = step_h.index(
        "Release tag changed during manual QA revalidation; do not publish"
    )
    final_draft = step_h.index("final pre-publication draft lookup failed")
    final_asset_check = step_h.index(
        'Assert-ReleaseAssetsMatchEvidence $finalDraft $freshEvidence.artifacts "final pre-publication"'
    )
    publication_patch = step_h.index(
        "& $ghPath api -X PATCH --hostname github.com $releaseEndpoint -F draft=false"
    )
    assert (
        manual_qa_recheck
        < final_environment
        < final_main
        < final_tag
        < final_draft
        < final_asset_check
        < publication_patch
    )
    final_check_window = step_h[final_asset_check:publication_patch]
    assert "& $" not in final_check_window
    assert "Start-Process" not in final_check_window
    assert "manualQaTool" not in final_check_window
    assert 'Move-Item -LiteralPath $pendingOutput -Destination $finalOutput' in step_f
    assert "Final manual QA output already exists" in step_f
    assert "OutboxBaseSeqTest" in step_f
    assert "separate filtered" in step_f
    assert "never reuse one aggregate XML" in step_f
    assert "hash both debug APK inputs after each filtered invocation" in step_f
    assert "otherwise discard both results" in step_f
    assert "move any connected-test result directory aside" in step_f
    assert "abort on a nonzero Gradle exit" in step_f
    assert "re_pair_outbox_high_water" in step_f
    assert "Remove-Item -LiteralPath $pendingOutput" in step_f
    assert "Refusing stale prepublish directory" in guide
    assert "$prepublishNonce = [DateTime]::UtcNow.ToString(" in guide
    assert '[Guid]::NewGuid().ToString("N")' in guide
    assert (
        'v1.6.0-prepublish-$runId-$prepublishNonce' in guide
    )
    assert "Draft assets changed after QA" in guide
    assert "prepublish-live-artifact-evidence-$prepublishNonce.json" in guide
    assert "prepublish-live-artifact-comment-$prepublishNonce.md" in guide
    assert "fresh pre-publication evidence verification failed" in guide
    assert "REPLACE_WITH_OWNER_APPROVED_64_HEX_BINDING" in guide
    assert "REPLACE_WITH_OWNER_APPROVED_MANUAL_QA_64_HEX_SHA256" in guide
    assert "local evidence files are not the approval" in guide
    assert "manual-QA report SHA-256" in guide
    assert "Manual QA report does not match the Owner-approved digest" in guide
    assert "Manual QA pre-publication revalidation failed" in guide
    assert "Manual QA recheck differs from the Owner-approved report" in guide
    assert "$freshEvidence.artifact_binding_sha256 -cne $ownerApprovedBinding" in guide
    assert guide.count(
        '$expectedAndroidCertSha256 = '
        f'"{owner_pack.NEW_ANDROID_CERT_SHA256}"'
    ) == 2
    assert (
        "86bdcbca45f0e9bce4c7cfbb3bc52f85f34a482acff8220af11dc659a2ec567c"
        not in guide
    )
    assert guide.count(
        "$env:ANDROID_RELEASE_CERT_SHA256 -cne $expectedAndroidCertSha256"
    ) == 2
    assert "--owner-approved-binding $ownerApprovedBinding" in guide
    assert "--publication-projection-stdout" in guide
    assert "$freshProjectionJson = & $pythonPath" in guide
    assert "$freshEvidence = $freshProjectionJson | ConvertFrom-Json" in guide
    assert "Get-Content -LiteralPath $prepublishEvidence -Raw" not in guide
    assert "& $ghPath api -X GET" in guide
    assert "Current main moved after QA; do not publish" in guide
    assert "Publication tools must run from the exact clean frozen target" in guide
    assert "Release verifier checkout changed after QA; do not publish" in guide
    assert '$release.targetCommitish -ne $targetCommit' in guide
    assert 'api -X PATCH --hostname github.com $releaseEndpoint -F draft=false' in guide
    assert "$publicationPatchExitCode = $LASTEXITCODE" in guide
    assert "Publication outcome is unknown after PATCH" in guide
    assert "exact-ID GET proves the Release is public" in guide
    assert "PATCH may have reached GitHub" in guide
    assert '$releaseId = [long]$freshEvidence.draft_release.id' in guide
    assert "Current main moved immediately before publication; do not publish" in guide
    assert "function Resolve-ExactReleaseTagCommit" in guide
    assert "Release tag changed after Owner approval; do not publish" in guide
    assert "Published release tag does not resolve to the frozen target" in guide
    assert "exact draft Release changed after fresh verification" in guide
    assert "Assert-ReleaseAssetsMatchEvidence" in guide
    assert "asset API identity/size/digest mismatch" in guide
    assert "function Assert-SigningResetReleaseBody" in guide
    assert "function Normalize-ReleaseBody" in guide
    assert "$canonicalReleaseBody = @'" in guide
    assert "Draft Release body is not the canonical signing-reset disclosure" in guide
    assert "Android signing reset" in guide
    assert "not an in-place update" in guide
    assert "com\\.clipvault\\.app" in guide
    assert "v1\\.5\\.10.*uninstall" in guide
    assert "synchroni[sz]e.*public clips" in guide
    assert "public memory.*Desktop|Desktop.*public memory" in guide
    assert "one-time Desktop.*reseed preparation" in guide
    assert "no supported export path.*quarantined" in guide
    assert "Android-only secret/private" in guide
    assert "quarantine is empty" in guide
    assert "accept.*permanent loss" in guide
    assert "no cryptographic signing continuity" in guide
    assert "898f21c2b59a4a4729fd386d91a86711b81ea567d5d85bf391a2e0fff2f1f9f1" in guide
    assert "Release body contains an unresolved placeholder" in guide
    assert "each approved signing-reset fingerprint exactly once" in guide
    assert "environments/release/variables/ANDROID_RELEASE_CERT_SHA256" in guide
    assert "$releaseCertSha256 -cne $expectedAndroidCertSha256" in guide
    assert "Assert-SigningResetReleaseBody $approvedReleaseBody $expectedAndroidCertSha256" in guide
    assert "Draft Release body or metadata changed immediately before publication" in guide
    assert "[string]$published.body -cne $approvedReleaseBody" in guide
    assert (
        "Assert-SigningResetReleaseBody ([string]$published.body) "
        "$expectedAndroidCertSha256"
    ) in guide
    assert "v1.6.0-postpublish-$runId" in guide
    assert "$published.draft" in guide
    assert "Published Release metadata mismatch" in guide
    assert "Published asset bytes differ from the Owner-approved binding" in guide
    assert "Step H - publish the existing draft, then verify published state" in guide
    assert '--published-release-dir "$postpublishRoot"' in guide
    assert "--require-live-published-release" in guide
    assert "postpublish-live-artifact-evidence.json" in guide
    assert "postpublish-live-artifact-comment.md" in guide
    assert "post-publication live evidence verification failed" in guide
    assert (
        "$postpublishReport.owner_approved_artifact_binding_sha256 -cne "
        "$ownerApprovedBinding"
    ) in guide
    assert "$postpublishReport.published_release.id -ne $releaseId" in guide
    assert "$postpublishReport.release_tag.commit_sha -cne $targetCommit" in guide
    assert "$postpublishReport.publication_closure_binding_sha256" in guide
    assert "Published release verifier checkout changed during validation" in guide
    assert "publication-closure binding" in guide
    assert "or rerun the full Step H draft path" in " ".join(guide.split())
    patch_index = guide.index(
        "api -X PATCH --hostname github.com $releaseEndpoint -F draft=false"
    )
    post_download_index = guide.index(
        "& $ghPath release download v1.6.0",
        patch_index,
    )
    published_verify_index = guide.index(
        "--require-live-published-release",
        post_download_index,
    )
    assert patch_index < post_download_index < published_verify_index
    published_call_start = guide.rindex(
        "& $pythonPath -I -S $evidenceTool",
        patch_index,
        published_verify_index,
    )
    published_call_end = guide.index(
        "if ($LASTEXITCODE -ne 0)",
        published_verify_index,
    )
    published_call = guide[published_call_start:published_call_end]
    assert "--publication-projection-stdout" not in published_call
    assert "--draft-release-dir" not in published_call
    assert "--no-fail" not in published_call
    assert '$postpublishEvidence = "$artifactRoot/' in guide
    assert '$postpublishComment = "$artifactRoot/' in guide
    assert '$postpublishEvidence = "$postpublishRoot/' not in guide
    assert '$postpublishComment = "$postpublishRoot/' not in guide
    recovery_start = guide.index(
        "### Step H recovery - read-only after PATCH may have reached GitHub"
    )
    recovery_end = guide.index("## 3. Hard blockers", recovery_start)
    recovery = guide[recovery_start:recovery_end]
    assert "-X PATCH" not in recovery
    assert "v1.6.0-postpublish-recovery-$runId-$recoveryNonce" in recovery
    assert "[Guid]::NewGuid()" in recovery
    assert "Run post-publication recovery from the repository root" in recovery
    assert "--require-live-published-release" in recovery
    assert "Post-publication recovery checkout changed during validation" in recovery
    assert "Post-publication recovery Release body or identity mismatch" in recovery
    assert "Assert-SigningResetReleaseBody ([string]$recoveryRelease.body)" in recovery
    assert "Closure recommendation: `BLOCKED`" in draft
    assert "Android signing-reset migration" in draft
    assert "manual-QA report SHA-256" in draft
    assert guide.isascii()
    assert draft.isascii()


def test_generated_absolute_path_helper_runs_on_windows_powershell_5_1():
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    guide = owner_pack.owner_action_pack(
        owner_pack.VERSION,
        owner_pack.ISSUE_URL,
        generated_at="2026-07-13T00:00:00Z",
    )
    match = re.search(
        r"(?ms)^function Test-FullyQualifiedWindowsPath\(.*?^\}\r?$",
        guide,
    )
    assert match is not None
    script = "\n".join([
        '$ErrorActionPreference = "Stop"',
        "Set-StrictMode -Version Latest",
        match.group(0),
        "if (-not (Test-FullyQualifiedWindowsPath 'C:\\trusted\\tool.exe')) { throw 'drive path rejected' }",
        "if (Test-FullyQualifiedWindowsPath '\\\\server\\share\\tool.exe') { throw 'UNC path accepted' }",
        "if (Test-FullyQualifiedWindowsPath '\\\\?\\C:\\trusted\\tool.exe') { throw 'device path accepted' }",
        "if (Test-FullyQualifiedWindowsPath '\\\\.\\C:\\trusted\\tool.exe') { throw 'device alias accepted' }",
        "if (Test-FullyQualifiedWindowsPath 'C:relative.exe') { throw 'drive-relative path accepted' }",
        "if (Test-FullyQualifiedWindowsPath '\\root-relative.exe') { throw 'root-relative path accepted' }",
        "if (Test-FullyQualifiedWindowsPath '.\\relative.exe') { throw 'relative path accepted' }",
    ])
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")

    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_generated_signing_reset_body_validator_runs_fail_closed_on_windows_powershell_5_1():
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    guide = owner_pack.owner_action_pack(
        owner_pack.VERSION,
        owner_pack.ISSUE_URL,
        generated_at="2026-07-13T00:00:00Z",
    )
    start = guide.index("function Assert-SigningResetReleaseBody")
    end = guide.index("$liveDraft =", start)
    validator = guide[start:end].strip()
    new_cert = owner_pack.NEW_ANDROID_CERT_SHA256
    old_cert = "898f21c2b59a4a4729fd386d91a86711b81ea567d5d85bf391a2e0fff2f1f9f1"
    valid_body = "\n".join([
        "Android signing reset - this is not an in-place update from v1.5.10.",
        "The v1.6.0 APK retains application ID com.clipvault.app.",
        "The installed v1.5.10 package must be uninstalled.",
        "Before uninstalling v1.5.10, synchronize and verify public clips and public memory on Desktop.",
        "Complete the one-time Desktop public-data reseed preparation.",
        "There is no supported export path, so Android-only secret/private items must be quarantined.",
        "Confirm the quarantine is empty or explicitly accept permanent loss.",
        f"Old certificate SHA-256: {old_cert}",
        f"New certificate SHA-256: {new_cert}",
        "There is no cryptographic signing continuity between these certificates.",
    ])
    script = "\n".join([
        '$ErrorActionPreference = "Stop"',
        "Set-StrictMode -Version Latest",
        f'$expectedAndroidCertSha256 = "{new_cert}"',
        validator,
        f"$validBody = @'\n{valid_body}\n'@",
        "Assert-SigningResetReleaseBody $validBody $expectedAndroidCertSha256",
        "function Assert-Rejected([scriptblock]$Action) {",
        "  try { & $Action } catch { return }",
        '  throw "invalid Release body was accepted"',
        "}",
        "Assert-Rejected { Assert-SigningResetReleaseBody ($validBody + \"`nTODO\") $expectedAndroidCertSha256 }",
        "Assert-Rejected { Assert-SigningResetReleaseBody ($validBody -replace 'public memory', 'public records') $expectedAndroidCertSha256 }",
        "Assert-Rejected { Assert-SigningResetReleaseBody ($validBody + \"`nNew certificate SHA-256: $expectedAndroidCertSha256\") $expectedAndroidCertSha256 }",
        "Assert-Rejected { Assert-SigningResetReleaseBody $validBody ('a' * 64) }",
    ])
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")

    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_generated_trim_end_arguments_run_on_windows_powershell_5_1():
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    guide = owner_pack.owner_action_pack(
        owner_pack.VERSION,
        owner_pack.ISSUE_URL,
        generated_at="2026-07-13T00:00:00Z",
    )
    matches = re.findall(r"\.TrimEnd\(([^\r\n]+)\)", guide)
    assert matches == ["[char]92, [char]47"] * 9
    script = "\n".join([
        '$ErrorActionPreference = "Stop"',
        "Set-StrictMode -Version Latest",
        f"$trimmed = 'C:\\trusted\\/'.TrimEnd({matches[0]})",
        "if ($trimmed -cne 'C:\\trusted') { throw \"unexpected result: $trimmed\" }",
    ])
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")

    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_generated_step_h_parses_on_windows_powershell_5_1():
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    guide = owner_pack.owner_action_pack(
        owner_pack.VERSION,
        owner_pack.ISSUE_URL,
        generated_at="2026-07-13T00:00:00Z",
    )
    section = guide[
        guide.index("### Step H - publish the existing draft"):
        guide.index("## 3. Hard blockers")
    ]
    scripts = re.findall(
        r"(?ms)^```powershell\r?\n(?P<script>.*?)^```\r?$",
        section,
    )
    assert len(scripts) == 2
    for script in scripts:
        encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
        parser_script = "\n".join([
            "$ErrorActionPreference = 'Stop'",
            f"$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))",
            "$tokens = $null",
            "$errors = $null",
            "[void][Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)",
            "if ($errors.Count -ne 0) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }",
        ])

        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", "-"],
            input=parser_script,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert completed.returncode == 0, completed.stderr or completed.stdout


def test_generated_step_f_parses_on_windows_powershell_5_1():
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    guide = owner_pack.owner_action_pack(
        owner_pack.VERSION,
        owner_pack.ISSUE_URL,
        generated_at="2026-07-13T00:00:00Z",
    )
    section = guide[
        guide.index("### Step F - execute manual QA against exact bytes"):
        guide.index("### Step G - consolidate Issue #36 evidence")
    ]
    scripts = re.findall(
        r"(?ms)^```powershell\r?\n(?P<script>.*?)^```\r?$",
        section,
    )
    assert len(scripts) == 1
    encoded = base64.b64encode(scripts[0].encode("utf-8")).decode("ascii")
    parser_script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$source = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))",
        "$tokens = $null",
        "$errors = $null",
        "[void][Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)",
        "if ($errors.Count -ne 0) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }",
    ])

    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", "-"],
        input=parser_script,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_new_pack_contains_only_the_fixed_known_file_set(tmp_path):
    output_dir = tmp_path / "pack"

    assert owner_pack.main(["--out-dir", str(output_dir)]) == 0

    assert sorted(path.name for path in output_dir.iterdir()) == sorted(
        owner_pack.EXPECTED_OUTPUT_FILES
    )
    summary = json.loads((output_dir / "pack-summary.json").read_text(encoding="utf-8"))
    manual = json.loads(
        (output_dir / "manual-qa-v1.6.0.template.json").read_text(encoding="utf-8")
    )
    assert summary["schema"] == "clipvault.issue36.owner_pack.summary.v2"
    assert summary["generated_files"] == sorted(owner_pack.EXPECTED_OUTPUT_FILES)
    assert manual == owner_pack.manual_qa_evidence.build_template(owner_pack.VERSION)
    assert not list(tmp_path.glob(".pack.staging-*"))


def test_non_empty_output_is_refused_without_partial_writes(tmp_path):
    output_dir = tmp_path / "pack"
    output_dir.mkdir()
    sentinel = output_dir / "owner-notes.txt"
    sentinel.write_bytes(b"preserve me")

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir)])

    assert sentinel.read_bytes() == b"preserve me"
    assert not any((output_dir / name).exists() for name in owner_pack.EXPECTED_OUTPUT_FILES)


def test_existing_filled_pack_is_not_overwritten_without_force(tmp_path):
    output_dir = tmp_path / "pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    manual_path = output_dir / "manual-qa-v1.6.0.template.json"
    manual_path.write_bytes(b'\n{"owner_filled": true}\n')
    before = _read_bytes(output_dir)

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir)])

    assert _read_bytes(output_dir) == before


def test_force_replaces_only_known_regular_files_and_preserves_unknown_files(tmp_path):
    output_dir = tmp_path / "pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    sentinel = output_dir / "owner-notes.txt"
    sentinel.write_bytes(b"do not replace")
    known = output_dir / "OWNER_RELEASE_ACTION_PACK.md"
    known.write_bytes(b"stale")

    assert owner_pack.main(["--out-dir", str(output_dir), "--force"]) == 0

    assert sentinel.read_bytes() == b"do not replace"
    assert known.read_text(encoding="utf-8").startswith("# Owner Release Action Pack")
    assert sorted(path.name for path in output_dir.iterdir()) == sorted(
        (*owner_pack.EXPECTED_OUTPUT_FILES, "owner-notes.txt")
    )


def test_force_rejects_known_directory_before_replacing_other_files(tmp_path):
    output_dir = tmp_path / "pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    first = output_dir / "OWNER_RELEASE_ACTION_PACK.md"
    before = first.read_bytes()
    conflict = output_dir / "agent-cluster.md"
    conflict.unlink()
    conflict.mkdir()

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir), "--force"])

    assert first.read_bytes() == before
    assert conflict.is_dir()


def test_force_rejects_hard_linked_known_file(tmp_path):
    output_dir = tmp_path / "pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    target = tmp_path / "owner-copy.md"
    known = output_dir / "OWNER_RELEASE_ACTION_PACK.md"
    target.write_bytes(b"shared owner data")
    known.unlink()
    os.link(target, known)

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir), "--force"])

    assert target.read_bytes() == b"shared owner data"
    assert known.read_bytes() == b"shared owner data"


@pytest.mark.parametrize(
    "failed_name",
    [
        owner_pack.EXPECTED_OUTPUT_FILES[0],
        owner_pack.EXPECTED_OUTPUT_FILES[len(owner_pack.EXPECTED_OUTPUT_FILES) // 2],
        owner_pack.EXPECTED_OUTPUT_FILES[-1],
    ],
)
def test_force_rolls_back_every_known_file_when_installation_fails(
    tmp_path,
    monkeypatch,
    failed_name,
):
    output_dir = tmp_path / "pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    sentinel = output_dir / "owner-notes.txt"
    sentinel.write_bytes(b"preserve unknown")
    before = _read_bytes(output_dir)
    real_replace = owner_pack.os.replace

    def fail_one_stage_install(source, target):
        source_path = Path(source)
        target_path = Path(target)
        if (
            source_path.parent.name.startswith(".pack.staging-")
            and target_path.parent == output_dir
            and source_path.name == failed_name
        ):
            raise PermissionError("simulated locked Owner pack target")
        return real_replace(source, target)

    monkeypatch.setattr(owner_pack.os, "replace", fail_one_stage_install)

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir), "--force"])

    assert _read_bytes(output_dir) == before
    assert sentinel.read_bytes() == b"preserve unknown"
    assert not list(tmp_path.glob(".pack.staging-*"))
    assert not list(tmp_path.glob(".pack.backup-*"))


def test_force_preserves_backup_when_rollback_cannot_restore_owner_file(
    tmp_path,
    monkeypatch,
    capsys,
):
    output_dir = tmp_path / "pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    sentinel = output_dir / "owner-notes.txt"
    sentinel.write_bytes(b"preserve unknown")
    before = _read_bytes(output_dir)
    install_failure = owner_pack.EXPECTED_OUTPUT_FILES[2]
    restore_failure = owner_pack.EXPECTED_OUTPUT_FILES[0]
    real_replace = owner_pack.os.replace

    def fail_install_and_one_restore(source, target):
        source_path = Path(source)
        target_path = Path(target)
        if (
            source_path.parent.name.startswith(".pack.staging-")
            and target_path.parent == output_dir
            and source_path.name == install_failure
        ):
            raise PermissionError("simulated stage installation failure")
        if (
            source_path.parent.name.startswith(".pack.backup-")
            and target_path.parent == output_dir
            and source_path.name == restore_failure
        ):
            raise PermissionError("simulated rollback restore failure")
        return real_replace(source, target)

    monkeypatch.setattr(owner_pack.os, "replace", fail_install_and_one_restore)

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir), "--force"])

    backups = list(tmp_path.glob(".pack.backup-*"))
    assert len(backups) == 1
    backup = backups[0]
    assert str(backup.resolve()) in capsys.readouterr().err
    assert (backup / restore_failure).read_bytes() == before[restore_failure]
    assert not (output_dir / restore_failure).exists()
    for name in owner_pack.EXPECTED_OUTPUT_FILES:
        if name != restore_failure:
            assert (output_dir / name).read_bytes() == before[name]
    assert sentinel.read_bytes() == b"preserve unknown"
    assert not list(tmp_path.glob(".pack.staging-*"))


def test_symlink_output_directory_is_rejected(tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    output_dir = tmp_path / "pack-link"
    try:
        output_dir.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir), "--force"])

    assert list(target.iterdir()) == []


def test_symlinked_parent_and_known_file_are_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    parent_link = tmp_path / "linked-parent"
    try:
        parent_link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(parent_link / "pack")])
    assert list(outside.iterdir()) == []

    output_dir = tmp_path / "regular-pack"
    owner_pack.main(["--out-dir", str(output_dir)])
    external_file = tmp_path / "owner-data.md"
    external_file.write_bytes(b"preserve external bytes")
    known = output_dir / "OWNER_RELEASE_ACTION_PACK.md"
    known.unlink()
    known.symlink_to(external_file)

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir), "--force"])
    assert external_file.read_bytes() == b"preserve external bytes"


def test_windows_junction_output_directory_is_rejected(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction behavior is Windows-only")
    target = tmp_path / "outside"
    target.mkdir()
    output_dir = tmp_path / "pack-junction"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(output_dir), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"junction creation is unavailable: {completed.stderr}")

    try:
        with pytest.raises(SystemExit, match="2"):
            owner_pack.main(["--out-dir", str(output_dir), "--force"])
        assert list(target.iterdir()) == []
    finally:
        if output_dir.exists():
            output_dir.rmdir()


def test_staging_write_failure_leaves_no_output_or_partial_pack(tmp_path, monkeypatch):
    output_dir = tmp_path / "pack"
    real_write_text = owner_pack.Path.write_text
    writes = 0

    def fail_second_stage_write(path, *args, **kwargs):
        nonlocal writes
        if path.parent.name.startswith(".pack.staging-"):
            writes += 1
            if writes == 2:
                raise OSError("simulated staging disk failure")
        return real_write_text(path, *args, **kwargs)

    monkeypatch.setattr(owner_pack.Path, "write_text", fail_second_stage_write)

    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(["--out-dir", str(output_dir)])

    assert not output_dir.exists()
    assert not list(tmp_path.glob(".pack.staging-*"))


@pytest.mark.parametrize(
    "argv",
    [
        ["--version", "v1.7.0"],
        ["--issue-url", "https://github.com/selinyi123/clipvault-personal/issues/82"],
    ],
)
def test_cli_rejects_scope_drift(argv):
    with pytest.raises(SystemExit, match="2"):
        owner_pack.main(argv)


def test_default_output_stays_in_the_existing_ignored_workspace():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert owner_pack.DEFAULT_OUTPUT_DIR == Path(
        ".field-test-artifacts/v1.6.0-owner-pack"
    )
    assert ".field-test-artifacts/" in gitignore
