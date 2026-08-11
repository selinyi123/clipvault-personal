from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / ".github" / "workflows" / "v2-daily-candidate.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
ANDROID = ROOT / ".github" / "workflows" / "v2-ime-production.yml"
WINDOWS = ROOT / ".github" / "workflows" / "windows-ime-native-slice.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\\", "/")


def test_candidate_composes_only_existing_locked_production_entries():
    candidate = _text(CANDIDATE)
    android = _text(ANDROID)
    windows = _text(WINDOWS)
    windows_build = _text(ROOT / "windows" / "ime" / "scripts" / "Build-ProductionIme.ps1")

    assert "uses: ./.github/workflows/v2-ime-production.yml" in candidate
    assert "uses: ./.github/workflows/windows-ime-native-slice.yml" in candidate
    assert "workflow_call:" in android
    assert "workflow_call:" in windows
    assert "android/scripts/build-v2-ime.ps1" in android.casefold()
    assert "windows/ime/scripts/Prepare-RimeSdk.ps1" in windows
    assert "windows/ime/scripts/Build-ProductionIme.ps1" in windows
    assert "Build-NativeSlice.ps1" in windows_build
    assert "Package-ClipVaultIme.ps1" in windows_build
    assert "-SkipTests" not in windows_build


def test_candidate_can_bootstrap_from_the_registered_ci_workflow():
    candidate = _text(CANDIDATE)
    ci = _text(CI)

    assert "workflow_call:" in candidate
    assert "uses: ./.github/workflows/v2-daily-candidate.yml" in ci
    assert "github.event_name == 'workflow_dispatch'" in ci
    assert "github.ref == 'refs/heads/codex/v2-daily-integration'" in ci


def test_windows_dictionary_checkout_preserves_locked_lf_bytes():
    windows = _text(WINDOWS)

    init = windows.index("git init $source")
    byte_exact_checkout = windows.index(
        "git -C $source config core.autocrlf false"
    )
    checkout = windows.index(
        "git -C $source checkout FETCH_HEAD -- AUTHORS LICENSE pinyin_simp.dict.yaml"
    )

    assert init < byte_exact_checkout < checkout


def test_candidate_is_read_only_and_only_uploads_unsigned_internal_artifacts():
    candidate = _text(CANDIDATE).casefold()
    execution_surface = "\n".join(
        (candidate, _text(ANDROID).casefold(), _text(WINDOWS).casefold())
    )
    assert "permissions:\n  contents: read" in candidate
    assert "continue-on-error" not in execution_surface
    assert "actions/upload-artifact@v7" in execution_surface
    assert "actions/download-artifact@v8" in candidate
    assert "clipvault-v2-daily-unsigned-candidate" in candidate
    assert "candidate-not-a-release.txt" in candidate
    assert "sha256sums.txt" in execution_surface
    assert "release_manifest.json" in candidate
    assert "build_receipt.json" in candidate
    assert "scripts/release_candidate_manifest.py" in candidate
    assert "tools/v2_daily_candidate.py" in candidate
    assert "--platform v2-daily" in candidate
    assert "--commit '${{ github.sha }}'" in candidate
    assert "write-receipt" in candidate
    assert "--expected-run-id '${{ github.run_id }}'" in candidate
    assert "ISCC.exe".casefold() in candidate
    forbidden = (
        "secrets.",
        "contents: write",
        "gh release",
        "actions/create-release",
        "action-gh-release",
        "ncipollo/release-action",
        "release-drafter",
        "register-clipvaultime",
        "unregister-clipvaultime",
        "signtool",
        "apksigner sign",
        "msiexec",
    )
    assert not [token for token in forbidden if token in execution_surface]


def test_candidate_fails_closed_between_source_and_bundle_readiness():
    candidate = _text(CANDIDATE)
    android_build = _text(ROOT / "android" / "scripts" / "build-v2-ime.ps1")

    assert "if: ${{ always() }}" in candidate
    assert "needs.desktop_candidate.result" in candidate
    assert "needs.android_native.result" in candidate
    assert "needs.windows_native.result" in candidate
    assert "V2 daily candidate BLOCKED" in candidate
    assert candidate.count("tools/v2_daily_readiness.py") == 2
    assert "tools/v2_daily_readiness.py --source-only" in candidate
    assert "--automated-only `" in candidate
    assert "--candidate-dir $candidate" in candidate
    assert "--evidence" not in candidate
    assert "--no-fail" not in candidate
    assert "RIME_PRODUCTION_LOCK.json" in android_build
    assert "Required production input is missing" in android_build
    assert "Git lock mismatch" in android_build
    assert "SHA-256 mismatch" in android_build


def test_android_candidate_builds_dual_default_apks_and_never_packages_restricted_otp():
    android_workflow = _text(ANDROID)
    android_build = _text(ROOT / "android" / "scripts" / "build-v2-ime.ps1")
    runtime_verifier = _text(
        ROOT / "android" / "scripts" / "verify-v2-runtime-apk.ps1"
    )
    rime_verifier = _text(
        ROOT / "android" / "scripts" / "verify-v2-rime-assets.ps1"
    )

    assert ":core:test" in android_workflow
    assert ":app:testDebugUnitTest" in android_workflow
    assert ":app:compileOtpSmsRelayKotlin" in android_workflow
    assert ":app:testOtpSmsRelayUnitTest" in android_workflow
    assert ":app:lintOtpSmsRelay" in android_workflow
    assert ":app:verifySmsUserConsentDependency" in android_workflow
    assert "verify-otp-sms-negative-gate.ps1" in android_workflow
    assert "assembleOtpSmsRelay" not in android_workflow
    assert "bundleOtpSmsRelay" not in android_workflow
    assert ":ime-app:buildProductionIme :app:assembleRelease" in android_build
    assert "ClipVault-IME-v2-unsigned.apk" in android_build
    assert "ClipVault-Runtime-v2-unsigned.apk" in android_build
    assert "RIME_ASSET_LOCK.json" in android_build
    assert "allowed_staged_files" in rime_verifier
    assert "SHA-256 mismatch" in rime_verifier
    assert "release allowlist" in runtime_verifier
    assert "zipalign -c -P 16" in runtime_verifier
