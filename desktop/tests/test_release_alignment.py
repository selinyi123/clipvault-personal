"""Automated release-state gate.

Replaces the version-metadata checks in the Issue #36 release gate:
instead of a human eyeballing each version string, this fails CI whenever the
visible version metadata drifts from the desktop runtime version, and confirms
the Panel IME helper and its test are present. Version-agnostic on purpose — it
asserts *alignment* to `clipvault.__version__`, so it keeps protecting future
bumps without edits.
"""

import re
from pathlib import Path

from clipvault import __version__
from clipvault.instance_lock import INSTANCE_MUTEX_NAME, InstanceLock

_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_desktop_pyproject_matches_runtime_version():
    m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', _read("desktop/pyproject.toml"))
    assert m, "version not found in pyproject.toml"
    assert m.group(1) == __version__


def test_android_version_name_aligned_and_code_advanced():
    gradle = _read("android/app/build.gradle.kts")
    name = re.search(r'versionName\s*=\s*"([^"]+)"', gradle)
    code = re.search(r'versionCode\s*=\s*(\d+)', gradle)
    assert name, "versionName not found in build.gradle.kts"
    assert code, "versionCode not found in build.gradle.kts"
    assert name.group(1) == __version__
    assert int(code.group(1)) >= 13  # never regress below the v1.6.0 floor


def test_installer_app_version_aligned():
    m = re.search(r'#define\s+AppVersion\s+"([^"]+)"', _read("installer/clipvault.iss"))
    assert m, "AppVersion not found in clipvault.iss"
    assert m.group(1) == __version__


def test_installer_uses_runtime_mutex_without_forced_process_termination():
    installer = _read("installer/clipvault.iss")
    setup = installer.split("[Setup]", 1)[1].split("\n[", 1)[0]
    uninstall_run = installer.split("[UninstallRun]", 1)[1].split("\n[", 1)[0]

    def setup_value(name: str) -> str:
        matches = re.findall(
            rf"(?mi)^\s*{re.escape(name)}\s*=\s*(.*?)\s*$",
            setup,
        )
        assert len(matches) == 1, f"expected one [Setup] {name} directive"
        return matches[0]

    assert InstanceLock().name == INSTANCE_MUTEX_NAME
    assert setup_value("AppMutex") == INSTANCE_MUTEX_NAME
    assert setup_value("CloseApplications").casefold() == "no"
    assert setup_value("RestartApplications").casefold() == "no"
    assert re.search(r"(?i)\btaskkill(?:\.exe)?\b", installer) is None
    assert re.search(r"(?i)\bStop-Process\b", installer) is None
    assert [
        line.strip()
        for line in uninstall_run.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    ] == [
        'Filename: "{cmd}"; Parameters: "/C exit /B 0"; '
        'Flags: runhidden; RunOnceId: "killcv"'
    ]


def test_version_sync_doc_matches_source_tree():
    doc = _read("docs/VERSION_SYNC.md")
    gradle = _read("android/app/build.gradle.kts")
    code = re.search(r'versionCode\s*=\s*(\d+)', gradle)
    assert code, "versionCode not found in build.gradle.kts"

    assert f"runtime version: {__version__}" in doc
    assert f"pyproject.toml: {__version__}" in doc
    assert f"versionName: {__version__}" in doc
    assert f"versionCode: {code.group(1)}" in doc
    assert f"AppVersion: {__version__}" in doc
    assert "Issue #36" in doc
    assert "Final `v1.6.0` GitHub Release publication remains blocked" in doc
    assert re.search(r"Owner\s+approval", doc)


def test_readme_does_not_overstate_unreleased_v1_6_status():
    readme = _read("README.md")
    status = readme.split("---", 1)[0]

    assert "v1.6.0 二进制尚未发布" in status
    assert "最新**已发布**二进制仍为 [v1.5.10]" in status
    assert "Issue #36" in status
    assert "final Windows artifacts" in status
    assert "signed Android artifacts" in status
    assert "signed Windows/Android artifacts" not in status
    assert "manual device QA" in status
    assert "v1.7 仅作为稳定化/隐私/同步可靠性规划线推进" in status

    for stale_claim in (
        "桌面端 **166** 项测试",
        "共 170",
        "app 整体编译产出已签名 APK",
        "唯一长期剩余：Android 真机体验确认",
        "# 170 passed",
        "# 166 passed",
    ):
        assert stale_claim not in readme

    assert "pytest 回归套件（具体数量以当前命令输出为准）" in readme
    assert "不要把旧测试数量写成发布证据" in readme


def test_architecture_matches_current_http_sync_runtime():
    arch = _read("docs/ARCHITECTURE.md")

    assert "stdlib HTTPServer: REST + Web UI + HTTP sync" in arch
    assert "HTTP push/pull + 配对 token" in arch
    assert "sync/" in arch
    assert "engine.py      # HTTP push/pull 事件日志同步（合同 SYNC-2）" in arch
    assert "server.py      # stdlib HTTPServer：REST（合同 API-1）" in arch
    assert "handlers.py    # endpoint 逻辑，直接单测" in arch
    assert "HttpURLConnection 客户端" in arch

    for stale_claim in (
        "FastAPI",
        "WebSocket /sync",
        "syncserver/",
        "WS /sync",
        "uvicorn",
        "OkHttp WebSocket",
        "WS 长连",
        "WS 推送",
        "WS 推到桌面",
        "WS 半开连接",
    ):
        assert stale_claim not in arch


def test_product_spec_tracks_current_http_sync_runtime():
    spec = _read("docs/PRODUCT_SPEC.md")

    assert "局域网/Tailscale HTTP push-pull 同步服务端 + 设备配对" in spec
    assert "与桌面双向同步（HTTP push-pull + 离线 outbox）" in spec
    assert "双端同步：HTTP push-pull、配对、离线队列、去重" in spec

    for stale_claim in (
        "WebSocket 同步服务端",
        "WebSocket + 离线 outbox",
        "双端同步：WebSocket",
        "FastAPI",
    ):
        assert stale_claim not in spec


def test_threat_model_tracks_current_http_sync_boundary():
    threat_model = _read("docs/THREAT_MODEL.md")
    manifest = _read("android/app/src/main/AndroidManifest.xml")

    assert "HTTP push/pull over LAN/Tailscale" in threat_model
    assert "纯 LAN 模式 HTTP 明文" in threat_model
    assert "配对 token" in threat_model
    assert "P2 提供自签 TLS + 钉扎" in threat_model
    assert "SYNC-2 uses plain HTTP" in manifest
    assert 'android:usesCleartextTraffic="true"' in manifest

    for stale_claim in (
        "WS over LAN/Tailscale",
        "纯 LAN 模式 WS 明文",
        "FastAPI",
        "OkHttp WebSocket",
    ):
        assert stale_claim not in threat_model


def test_research_log_rounds_and_ids_are_unique():
    research = _read("docs/RESEARCH_AND_ROADMAP.md")

    rounds = re.findall(r"(?m)^## Research log - round (\d+) ", research)
    research_ids = re.findall(r"(?m)^\| (R\d+) \|", research)

    assert len(rounds) == len(set(rounds))
    assert len(research_ids) == len(set(research_ids))
    assert "R69 | Bidirectional sync JSON byte budgets and durable push blocking" in research


def test_android_sync_push_request_budget_stays_below_desktop_cap():
    worker = _read("android/app/src/main/kotlin/com/clipvault/app/sync/SyncWorker.kt")
    normalize = _read("android/core/src/main/kotlin/com/clipvault/core/Normalize.kt")
    server = _read("desktop/clipvault/api/server.py")

    clip_cap = re.search(
        r"DEFAULT_MAX_CLIP_BYTES\s*=\s*([\d_]+)",
        normalize,
    )
    escape_multiplier = re.search(
        r"MAX_JSON_ESCAPED_BYTES_PER_CLIP_BYTE\s*=\s*(\d+)",
        worker,
    )
    envelope_kib = re.search(
        r"MAX_SYNC_EVENT_ENVELOPE_BYTES\s*=\s*(\d+)\s*\*\s*1024",
        worker,
    )
    desktop_cap = re.search(
        r"_MAX_CONTENT_JSON_BODY\s*=\s*(\d+)\s*\*\s*1_048_576",
        server,
    )

    assert clip_cap, "Android max clip byte limit not found"
    assert escape_multiplier, "Android JSON escape multiplier not found"
    assert envelope_kib, "Android sync event envelope allowance not found"
    assert desktop_cap, "desktop sync push body cap not found"
    android_budget = (
        int(clip_cap.group(1).replace("_", "")) * int(escape_multiplier.group(1))
        + int(envelope_kib.group(1)) * 1024
    )
    assert android_budget < int(desktop_cap.group(1)) * 1_048_576
    assert 'if route == "/api/clips":\n                body = self._body(_MAX_CONTENT_JSON_BODY)' in server
    assert "_MAX_SYNC_PUSH_BODY = _MAX_CONTENT_JSON_BODY" in server
    assert "buildSyncPushBatch" in worker
    assert "maxSizedControlCharacterClipFitsTheProductionRequestBudget" in _read(
        "android/app/src/test/kotlin/com/clipvault/app/sync/SyncPushBatchTest.kt"
    )


def test_sync_pull_response_caps_cover_worst_case_clip_and_stay_aligned():
    android_sync = _read("android/app/src/main/kotlin/com/clipvault/app/sync/Sync.kt")
    android_test = _read("android/app/src/test/kotlin/com/clipvault/app/sync/SyncClientBoundsTest.kt")
    desktop_engine = _read("desktop/clipvault/sync/engine.py")
    desktop_test = _read("desktop/tests/test_sync.py")

    android_cap = re.search(
        r"MAX_SYNC_RESPONSE_BYTES\s*=\s*(\d+)\s*\*\s*1024\s*\*\s*1024",
        android_sync,
    )
    desktop_cap = re.search(
        r"SYNC_PULL_HTTP_RESPONSE_BYTES\s*=\s*(\d+)\s*\*\s*1024\s*\*\s*1024",
        desktop_engine,
    )
    envelope_kib = re.search(
        r"SYNC_PULL_RESPONSE_ENVELOPE_BYTES\s*=\s*(\d+)\s*\*\s*1024",
        desktop_engine,
    )

    assert android_cap, "Android sync response hard cap not found"
    assert desktop_cap, "desktop sync response hard cap not found"
    assert envelope_kib, "desktop sync response envelope reserve not found"
    assert int(android_cap.group(1)) == int(desktop_cap.group(1))
    assert int(envelope_kib.group(1)) >= 64
    assert "SYNC_PULL_FETCH_LIMIT = 8" in desktop_engine
    assert "fetch_limit = min(limit, SYNC_PULL_FETCH_LIMIT)" in desktop_engine
    assert "maxSizedControlCharacterClipFitsProductionPullResponseLimit" in android_test
    assert "test_h8_pull_accepts_max_clip_with_worst_case_json_escaping" in desktop_test


def test_panel_candidate_tabs_helper_and_test_exist():
    base = _ROOT / "android/app/src"
    assert (base / "main/kotlin/com/clipvault/app/ime/PanelCandidateTabs.kt").exists()
    assert (base / "test/kotlin/com/clipvault/app/ime/PanelCandidateTabsTest.kt").exists()


def test_signed_release_workflow_is_manual_secret_gated_and_verifies_apk():
    workflow = _read(".github/workflows/release.yml")

    assert (
        "run-name: Release artifacts ${{ inputs.version }} from "
        "${{ github.ref_name }} draft=${{ inputs.create_draft_release }}"
    ) in workflow
    assert "workflow_dispatch:" in workflow
    assert "\n  push:" not in workflow
    assert "\n  pull_request:" not in workflow
    assert "environment: release" in workflow
    assert "ANDROID_RELEASE_KEYSTORE_B64" in workflow
    assert "ANDROID_RELEASE_KEYSTORE_PASSWORD" in workflow
    assert "ANDROID_RELEASE_KEY_ALIAS" in workflow
    assert "ANDROID_RELEASE_KEY_PASSWORD" in workflow
    assert "vars.ANDROID_RELEASE_CERT_SHA256" in workflow
    assert "secrets.ANDROID_RELEASE_CERT_SHA256" not in workflow
    assert "ANDROID_RELEASE_CERT_SHA256 must be exactly 64 lowercase hex characters" in workflow
    assert "ANDROID_RELEASE_CERT_SHA256 does not match the approved v1.6.0 signing-reset certificate" in workflow
    assert "898f21c2b59a4a4729fd386d91a86711b81ea567d5d85bf391a2e0fff2f1f9f1" in workflow
    assert "86bdcbca45f0e9bce4c7cfbb3bc52f85f34a482acff8220af11dc659a2ec567c" in workflow
    assert "apksigner" in workflow
    assert "verify --verbose -Werr --print-certs" in workflow
    assert workflow.count("--expected-android-cert-sha256") == 2
    assert 'mapfile -t release_apks' in workflow
    assert '[[ "${#release_apks[@]}" -ne 1 ]]' in workflow
    assert 'aapt="$(find "${ANDROID_HOME}/build-tools" -name aapt' in workflow
    assert '"${aapt}" dump badging "${signed_apk}"' in workflow
    assert 'badging_output="$("${aapt}" dump badging "${signed_apk}")"' in workflow
    assert "package_line=\"${badging_output%%$'\\n'*}\"" in workflow
    assert "dump badging \"${signed_apk}\" |" not in workflow
    assert "Final APK application ID is not com.clipvault.app" in workflow
    assert "Final APK manifest versionName does not match" in workflow
    assert "trap 'rm -f -- \"${keystore:-}\"' EXIT" in workflow
    assert "umask 077" in workflow
    assert "actions/attest-build-provenance@v4" in workflow
    assert "create_draft_release" in workflow
    assert "upload-assets" in workflow
    assert "windows-${base}" in workflow
    assert "android-${base}" in workflow
    assert "--draft" in workflow
    assert "python -m pytest -q" in workflow
    assert ":app:lintRelease" in workflow
    assert "validate-release-input:" in workflow
    assert "version must be a release tag like v1.6.0" in workflow
    validate_input = _workflow_job_block(workflow, "validate-release-input")
    assert 'if [[ "${GITHUB_REF_NAME:-}" != "main" ]]' in validate_input
    assert "Release artifact build must run from main" in validate_input
    assert validate_input.index("Release artifact build must run from main") < validate_input.index(
        "version must be a release tag like v1.6.0"
    )
    assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in workflow
    assert "needs: validate-release-input" in workflow
    assert "needs.validate-release-input.outputs.version" in workflow


def test_signed_release_keeps_gradle_passwords_out_of_process_arguments():
    workflow = _read(".github/workflows/release.yml")
    android = _workflow_job_block(workflow, "android-signed-release")

    assert "ORG_GRADLE_PROJECT_CV_KEYSTORE_PASS: ${{ secrets.ANDROID_RELEASE_KEYSTORE_PASSWORD }}" in android
    assert "ORG_GRADLE_PROJECT_CV_KEY_ALIAS: ${{ secrets.ANDROID_RELEASE_KEY_ALIAS }}" in android
    assert "ORG_GRADLE_PROJECT_CV_KEY_PASS: ${{ secrets.ANDROID_RELEASE_KEY_PASSWORD }}" in android
    assert 'export ORG_GRADLE_PROJECT_CV_KEYSTORE="${keystore}"' in android
    decode = android.index("base64 -d")
    gradle = android.index("./gradlew :core:test")
    cleanup = android.index('rm -f -- "${keystore}"', gradle)
    stage = android.index("rm -rf ../release-artifacts", cleanup)
    assert decode < android.index("unset ANDROID_RELEASE_KEYSTORE_B64") < gradle
    assert gradle < android.index("unset ORG_GRADLE_PROJECT_CV_KEYSTORE", gradle) < cleanup < stage
    assert 'keystore=""' in android[cleanup:stage]
    assert "-PCV_KEYSTORE" not in android
    assert "-PCV_KEYSTORE_PASS" not in android
    assert "-PCV_KEY_ALIAS" not in android
    assert "-PCV_KEY_PASS" not in android


def test_signed_release_verifies_owner_signer_before_attestation_and_upload():
    workflow = _read(".github/workflows/release.yml")
    android = _workflow_job_block(workflow, "android-signed-release")

    gradle = android.index("./gradlew :core:test")
    aapt = android.index('aapt="$(find "${ANDROID_HOME}/build-tools"')
    badging = android.index('badging_output="$("${aapt}" dump badging')
    package_check = android.index("Final APK application ID is not com.clipvault.app")
    version_check = android.index("Final APK manifest versionName does not match")
    apksigner = android.index("verify --verbose -Werr --print-certs")
    manifest = android.index("python scripts/release_candidate_manifest.py")
    verifier = android.index("python scripts/verify_release_manifest.py")
    attestation = android.index("Attest Android signed release artifacts")
    upload = android.index("Upload Android signed release artifacts")

    assert (
        gradle
        < aapt
        < badging
        < package_check
        < version_check
        < apksigner
        < manifest
        < verifier
        < attestation
        < upload
    )
    assert '--expected-android-cert-sha256 "${ANDROID_RELEASE_CERT_SHA256}"' in android
    assert "vars.ANDROID_RELEASE_CERT_SHA256" in android
    assert 'echo "${ANDROID_RELEASE_CERT_SHA256}"' not in android


def test_manual_qa_links_v1_6_release_runbook():
    runbook = _ROOT / "docs/RELEASE_RUNBOOK_V1_6_0.md"
    manual_qa = _read("docs/MANUAL_QA_V1_6_0.md")

    assert runbook.exists()
    assert "RELEASE_RUNBOOK_V1_6_0.md" in manual_qa
    assert "Release artifact build" in runbook.read_text(encoding="utf-8")


def test_release_runbook_uses_live_main_evidence_commands():
    runbook = _read("docs/RELEASE_RUNBOOK_V1_6_0.md")

    assert "python tools/release_readiness.py --no-fail" in runbook
    assert "python tools/release_readiness.py --json --no-fail" in runbook
    assert "The checker is read-only." in runbook
    assert "must not trigger workflows, set secrets, create or" in runbook
    assert "lists the unchecked release-gate checklist items" in runbook
    assert (_ROOT / "tools/release_readiness.py").exists()
    assert "gh run list" in runbook
    assert "gh workflow run \"Release candidate dry run\"" in runbook
    assert "Release artifact build` only with `--ref main`" in runbook
    assert "Release artifacts v1.6.0 from main draft=false" in runbook
    assert "The workflow must run from current `main`" in runbook
    assert "CI_RUN_ID" in runbook
    assert "RELEASE_CANDIDATE_DRY_RUN_ID" in runbook
    assert not re.search(r"https://github\.com/[^)\s]+/actions/runs/\d+", runbook)
    assert not re.search(r"\b[0-9a-f]{40}\b", runbook)


def test_manual_qa_evidence_helper_is_documented_without_release_overclaim():
    script = _ROOT / "tools/manual_qa_evidence.py"
    manual_qa = _read("docs/MANUAL_QA_V1_6_0.md")
    runbook = _read("docs/RELEASE_RUNBOOK_V1_6_0.md")
    research = _read("docs/RESEARCH_AND_ROADMAP.md")
    handoff = _read("docs/HANDOFF.md")
    evidence_readme = _read("docs/EVIDENCE/v1.6.0/README.md")

    assert script.exists()
    script_text = script.read_text(encoding="utf-8")
    assert "does not call GitHub" in script_text
    assert "does not replace signed artifact evidence" in script_text
    assert "android_device_qa" in script_text
    assert "ime_privacy_qa" in script_text
    assert "sync_qa" in script_text
    assert "windows_clipboard_privacy_qa" in script_text

    for doc in (manual_qa, runbook):
        assert r'.field-test-artifacts\v1.6.0-manual-qa' in doc
        assert r'python tools/manual_qa_evidence.py --write-template "$qaEvidenceDir\manual-qa-v1.6.0.json"' in doc
        assert r'--final-draft-artifact-evidence "$finalDraftEvidence"' in doc
        assert "--require-final-draft-binding" in doc
        assert r'--output "$qaEvidenceDir\manual-qa-issue-comment.md"' not in doc
        assert "--no-fail" in doc
        assert "Step F" in doc
        assert "bare helper" in doc
        assert ".field-test-artifacts/v1.6.0-owner-pack/OWNER_RELEASE_ACTION_PACK.md" in doc
        assert "release-owner-action-guide-v1.6.0.md" not in doc
        assert "final_draft_binding_assurance=verified_external_snapshot" in doc
        assert "app-debug-androidTest.apk" in doc
        assert "OutboxBaseSeqTest" in doc
        assert "separate" in doc
        assert "aggregate" in doc
        assert "changed between filtered test runs" in doc
        assert "discard both" in doc
        assert doc.count("Rename-Item -LiteralPath $connectedResults") == 2
        assert "CursorWindow filtered instrumentation failed" in doc
        assert "Outbox baseline filtered instrumentation failed" in doc
        assert doc.count("did not create fresh connected-test results") == 2
        assert doc.count("Fresh connected-test results must not be a reparse point") == 2
        assert "git status --short" in doc
        assert "does not replace signed artifact evidence" in doc
        assert "Issue #36" in doc

    assert "R83 | Structured manual QA evidence" in research
    assert "does not run device QA" in research
    assert "does not replace signed artifact evidence" in research
    assert "tools/manual_qa_evidence.py" in handoff
    assert "fail-closed schema v4" in handoff
    assert "Frozen schema v3 and v2" in handoff
    assert "does not run device QA" in handoff
    assert "does not replace signed-artifact/final-release evidence" in handoff
    assert "OutboxBaseSeqTest" in evidence_readme
    assert "API 26/27 outbox baseline QA" in evidence_readme


def test_release_runbook_uses_powershell_safe_secret_stdin_and_checks_failures():
    runbook = _read("docs/RELEASE_RUNBOOK_V1_6_0.md")

    assert "--env release <" not in runbook
    assert "[Convert]::ToBase64String(" in runbook
    assert '[IO.File]::ReadAllBytes("clipvault-release.jks")' in runbook
    assert "Do not create a plaintext `.b64` staging file" in runbook
    assert "Get-Content -LiteralPath keystore.b64" not in runbook
    assert "Failed to set the release keystore secret" in runbook
    assert "Failed to set the keystore password secret" in runbook
    assert "Failed to set the key alias secret" in runbook
    assert "Failed to set the key password secret" in runbook


def test_release_artifact_evidence_helper_is_documented_without_release_overclaim():
    script = _ROOT / "tools/release_artifact_evidence.py"
    manual_qa = _read("docs/MANUAL_QA_V1_6_0.md")
    runbook = _read("docs/RELEASE_RUNBOOK_V1_6_0.md")
    research = _read("docs/RESEARCH_AND_ROADMAP.md")
    handoff = _read("docs/HANDOFF.md")
    workflows = _read("docs/AGENT_WORKFLOWS_ISSUE_36.md")

    assert script.exists()
    script_text = script.read_text(encoding="utf-8")
    assert "does not download GitHub Actions artifacts" in script_text
    assert "does not replace manual QA evidence" in script_text
    assert "verify_release_manifest.py" in script_text
    assert "ANDROID_APKSIGNER_VERIFY.txt" in script_text
    assert "--expected-android-cert-sha256" in script_text
    assert '"status": "structural_precheck_pass"' in script_text
    assert "--require-live-published-release" in script_text

    assert "python tools/release_artifact_evidence.py" in manual_qa
    assert "tools/release_artifact_evidence.py --require-live-final-draft" in runbook
    assert "python tools/prepare_v1_6_release_owner_pack.py" in runbook
    assert "gh attestation verify" in runbook
    assert "a green workflow alone" in runbook.lower()
    assert "still does not prove artifact contents" in runbook.lower()
    assert "--require-live-final-draft" in runbook
    assert "--require-live-published-release" in runbook
    assert "Post-publication recovery" in runbook
    assert "Do not rebuild" in runbook
    assert "runInvocationURI" in runbook
    assert "release-by-tag REST endpoint returns published Releases only" in runbook
    assert "does not download artifacts, trigger workflows" in research
    assert "R85 | Downloaded release artifact evidence and provenance" in research
    assert "tools/release_artifact_evidence.py" in handoff
    assert "canonical binding digest" in handoff
    assert "--require-live-final-draft" in handoff
    assert "--require-live-published-release" in handoff
    assert "publication-closure binding" in handoff
    assert "--require-live-published-release" in workflows
    assert "without treating either JSON as closure" in workflows


def test_release_runbook_qa_uses_the_final_draft_bytes_without_rebuild():
    runbook = _read("docs/RELEASE_RUNBOOK_V1_6_0.md")

    preflight = runbook.index("create_draft_release=false")
    final_draft = runbook.index("create_draft_release=true", preflight)
    manual_qa = runbook.index("## 6. Record manual QA evidence", final_draft)
    publication = runbook.index("publishes by the verified numeric Release ID", manual_qa)

    assert preflight < final_draft < manual_qa < publication
    assert "Its bytes are not eligible" in runbook
    assert "from the draft Release directory" in runbook
    assert "execute its Step H as one intact, fail-closed" in runbook
    assert "every draft asset ID/size/digest" in runbook
    assert "not by a mutable tag lookup" in runbook
    assert "Owner-exclusive Release mutation window" in runbook
    assert "gh release edit v1.6.0" not in runbook
    assert "\ngh release " not in runbook
    assert "Optional draft GitHub Release" not in runbook


def test_release_runbook_uses_release_environment_secrets():
    runbook = _read("docs/RELEASE_RUNBOOK_V1_6_0.md")
    manual_qa = _read("docs/MANUAL_QA_V1_6_0.md")

    assert "Required `release` environment secrets" in runbook
    assert "--env release" in runbook
    assert "repository-level secrets" in runbook
    assert "Required repository secrets" not in runbook
    assert "`release`" in manual_qa
    assert "environment secrets" in manual_qa


def test_pull_request_template_warns_against_release_gate_auto_close_keywords():
    template = _read(".github/PULL_REQUEST_TEMPLATE.md")

    assert "Release-gate issue hygiene" in template
    assert "GitHub auto-close" in template
    assert "Negative wording with those keywords can still be interpreted by GitHub" in template
    assert "Issue #36 remains open" in template
    assert "does not change issue state for #36" in template
    assert "PR title/body" in template
    assert "tools/check_pr_issue_hygiene.py" not in template
    assert not re.search(
        r"(?i)\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#\d+\b",
        template,
    )


def test_ci_checks_pull_request_body_for_release_gate_issue_hygiene():
    workflow = _read(".github/workflows/ci.yml")

    assert "release-gate-issue-hygiene:" in workflow
    assert "Release-gate issue hygiene" in workflow
    assert "github.event_name == 'pull_request'" not in workflow
    assert "python tools/check_pr_issue_hygiene.py --event-path" in workflow
    assert (_ROOT / "tools/check_pr_issue_hygiene.py").exists()


def test_windows_pyinstaller_workflows_bundle_desktop_resources():
    expected = [
        '--add-data "$PWD/clipvault/store/migrations;clipvault/store/migrations"',
        '--add-data "$PWD/clipvault/api/webui;clipvault/api/webui"',
    ]

    for rel in (".github/workflows/release.yml", ".github/workflows/release-candidate.yml"):
        workflow = _read(rel)
        assert "../clipvault/" not in workflow
        for line in expected:
            assert line in workflow


def test_workflow_checkouts_do_not_persist_github_token_credentials():
    workflows = sorted((_ROOT / ".github/workflows").glob("*.yml"))
    assert workflows, "no GitHub Actions workflows found"

    for path in workflows:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        checkout_line_indexes = [
            index
            for index, line in enumerate(lines)
            if re.search(r"uses:\s*actions/checkout@", line)
        ]

        for index in checkout_line_indexes:
            checkout_block = "\n".join(lines[index : index + 8])
            assert re.search(
                r"(?m)^\s*persist-credentials:\s*false\s*$",
                checkout_block,
            ), f"{path.relative_to(_ROOT)} checkout step must set persist-credentials: false"


def test_release_artifact_uploads_fail_if_no_files_are_found():
    for rel in (".github/workflows/release-candidate.yml", ".github/workflows/release.yml"):
        text = _read(rel)
        lines = text.splitlines()
        upload_line_indexes = [
            index
            for index, line in enumerate(lines)
            if re.search(r"uses:\s*actions/upload-artifact@", line)
        ]
        assert upload_line_indexes, f"{rel} must upload release artifacts"

        for index in upload_line_indexes:
            upload_block = "\n".join(lines[index : index + 8])
            assert re.search(
                r"(?m)^\s*if-no-files-found:\s*error\s*$",
                upload_block,
            ), f"{rel} artifact upload must fail when the configured path matches no files"


def test_draft_release_reverifies_downloaded_artifacts_before_release_creation():
    workflow = _read(".github/workflows/release.yml")
    draft = _workflow_job_block(workflow, "draft-github-release")

    download_windows = draft.index("Download Windows release artifacts")
    download_android = draft.index("Download Android signed release artifacts")
    verify = draft.index("Verify downloaded release artifacts")
    create = draft.index("Create draft GitHub Release")
    assert download_windows < verify < create
    assert download_android < verify < create

    assert "name: clipvault-windows-release-artifacts" in draft
    assert "path: release-artifacts/clipvault-windows-release-artifacts" in draft
    assert "--artifact-dir release-artifacts/clipvault-windows-release-artifacts" in draft
    assert "--platform windows" in draft
    assert "name: clipvault-android-signed-release-artifacts" in draft
    assert "path: release-artifacts/clipvault-android-signed-release-artifacts" in draft
    assert "--artifact-dir release-artifacts/clipvault-android-signed-release-artifacts" in draft
    assert "--platform android" in draft
    assert "--require-signed" in draft
    assert "vars.ANDROID_RELEASE_CERT_SHA256" in draft
    assert '--expected-android-cert-sha256 "${ANDROID_RELEASE_CERT_SHA256}"' in draft


def test_draft_release_stages_only_verified_named_artifact_directories():
    workflow = _read(".github/workflows/release.yml")
    draft = _workflow_job_block(workflow, "draft-github-release")

    assert "artifact_dirs=(" in draft
    assert "release-artifacts/clipvault-windows-release-artifacts" in draft
    assert "release-artifacts/clipvault-android-signed-release-artifacts" in draft
    assert 'find "${dir}" -maxdepth 1 -type f -print0' in draft
    assert "find release-artifacts -type f" not in draft


def test_draft_release_staging_fails_on_duplicate_asset_names():
    workflow = _read(".github/workflows/release.yml")
    draft = _workflow_job_block(workflow, "draft-github-release")

    duplicate_check = draft.index("Duplicate release asset name after staging")
    copy = draft.index('cp "${file}" "upload-assets/${asset}"')

    assert duplicate_check < copy
    assert '[[ -e "upload-assets/${asset}" ]]' in draft
    assert "exit 1" in draft[duplicate_check:copy]


def test_draft_release_notes_disclose_signing_reset_and_dynamic_new_certificate():
    workflow = _read(".github/workflows/release.yml")
    draft = _workflow_job_block(workflow, "draft-github-release")

    notes = draft.index("cat > release-notes.md")
    create = draft.index("gh release create")

    assert notes < create
    assert "Android signing reset - this is not an in-place update from v1.5.10" in draft
    assert "retains application ID com.clipvault.app" in draft
    assert "package to be uninstalled before this APK can be installed" in draft
    assert "synchronize and verify public clips" in draft
    assert "Desktop-authoritative public memory" in draft
    assert "one-time Desktop" in draft
    assert "reseed preparation" in draft
    assert "no supported export path for quarantined" in draft
    assert "Android-only secret/private item" in draft
    assert "quarantine is empty" in draft
    assert "explicitly accept their permanent loss" in draft
    assert "pull the" in draft and "prepared reseed" in draft
    assert "pair again, pull the" in draft
    assert "re-enable" in draft
    assert "ClipVault Panel IME and Quick Settings Tile" in draft
    assert "898f21c2b59a4a4729fd386d91a86711b81ea567d5d85bf391a2e0fff2f1f9f1" in draft
    assert "New certificate SHA-256: ${ANDROID_RELEASE_CERT_SHA256}" in draft
    assert "There is no cryptographic signing continuity" in draft
    assert "Draft Release certificate does not match the approved v1.6.0 signing-reset certificate" in draft
    assert "86bdcbca45f0e9bce4c7cfbb3bc52f85f34a482acff8220af11dc659a2ec567c" in draft


def _pull_request_paths(workflow_text: str) -> set[str]:
    paths: set[str] = set()
    in_pull_request = False
    in_paths = False
    for line in workflow_text.splitlines():
        if line == "  pull_request:":
            in_pull_request = True
            in_paths = False
            continue
        if in_pull_request and line and not line.startswith(" "):
            break
        if in_pull_request and re.match(r"^    paths:\s*$", line):
            in_paths = True
            continue
        if not in_paths:
            continue
        match = re.match(r"^      -\s+(.+?)\s*$", line)
        if match:
            paths.add(match.group(1).strip("\"'"))
        elif line.strip() and not line.startswith("      "):
            break
    return paths


def test_release_candidate_pr_paths_cover_invoked_release_scripts():
    workflow = _read(".github/workflows/release-candidate.yml")
    invoked_scripts = set(
        re.findall(r"\bpython\s+(scripts/[A-Za-z0-9_./-]+\.py)\b", workflow)
    )
    assert invoked_scripts, "release-candidate workflow must invoke release scripts"

    paths = _pull_request_paths(workflow)
    missing = sorted(invoked_scripts - paths)
    assert not missing, (
        "release-candidate pull_request.paths must include every invoked release "
        f"script; missing: {missing}"
    )


def test_release_candidate_runs_on_main_push_without_release_side_effects():
    workflow = _read(".github/workflows/release-candidate.yml")

    assert "\n  push:\n    branches: [main]\n" in workflow
    push_block = workflow.split("\n  push:\n", 1)[1].split("\n  pull_request:\n", 1)[0]
    assert "branches: [main]" in push_block
    assert "paths:" not in push_block

    assert "environment: release" not in workflow
    assert "secrets." not in workflow
    assert "gh release" not in workflow
    assert "create_draft_release" not in workflow
    assert "contents: write" not in workflow


def _top_level_permissions(workflow_text: str) -> dict[str, str]:
    lines = workflow_text.splitlines()
    try:
        start = lines.index("permissions:")
    except ValueError:
        return {}

    permissions: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        match = re.match(r"^  ([a-z-]+):\s*([a-z-]+)\s*$", line)
        if match:
            permissions[match.group(1)] = match.group(2)
    return permissions


def _workflow_job_block(workflow_text: str, job_name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(job_name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        workflow_text,
    )
    assert match, f"job {job_name!r} not found"
    return match.group(1)


def test_workflow_github_token_permissions_are_least_privilege():
    workflows = sorted((_ROOT / ".github/workflows").glob("*.yml"))
    assert workflows, "no GitHub Actions workflows found"

    for path in workflows:
        text = path.read_text(encoding="utf-8")
        assert _top_level_permissions(text) == {"contents": "read"}, (
            f"{path.relative_to(_ROOT)} must default GITHUB_TOKEN to contents: read"
        )
        assert not re.search(r"(?m)^permissions:\s*(read-all|write-all)\s*$", text), (
            f"{path.relative_to(_ROOT)} must use explicit narrow permissions"
        )

    for rel in (".github/workflows/ci.yml", ".github/workflows/release-candidate.yml"):
        text = _read(rel)
        assert not re.search(r"(?m)^\s+[a-z-]+:\s*write\s*$", text), (
            f"{rel} must not request write-scoped GITHUB_TOKEN permissions"
        )

    release_workflow = _read(".github/workflows/release.yml")
    assert len(re.findall(r"(?m)^\s+contents:\s*write\s*$", release_workflow)) == 1
    assert len(re.findall(r"(?m)^\s+attestations:\s*write\s*$", release_workflow)) == 2
    assert len(re.findall(r"(?m)^\s+id-token:\s*write\s*$", release_workflow)) == 2

    for job in ("windows-release-artifacts", "android-signed-release"):
        block = _workflow_job_block(release_workflow, job)
        assert "permissions:" in block
        assert re.search(r"(?m)^      contents:\s*read\s*$", block)
        assert re.search(r"(?m)^      attestations:\s*write\s*$", block)
        assert re.search(r"(?m)^      id-token:\s*write\s*$", block)
        assert not re.search(r"(?m)^      contents:\s*write\s*$", block)

    draft_block = _workflow_job_block(release_workflow, "draft-github-release")
    assert "permissions:" in draft_block
    assert re.search(r"(?m)^      contents:\s*write\s*$", draft_block)
    assert "attestations: write" not in draft_block
    assert "id-token: write" not in draft_block


def test_workflows_do_not_use_privileged_untrusted_code_triggers():
    workflows = sorted((_ROOT / ".github/workflows").glob("*.yml"))
    assert workflows, "no GitHub Actions workflows found"

    dangerous_triggers = ("pull_request_target", "workflow_run")
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        for trigger in dangerous_triggers:
            assert not re.search(rf"(?m)^\s*{trigger}:\s*$", text), (
                f"{path.relative_to(_ROOT)} must not use the privileged "
                f"{trigger!r} trigger; keep PR code on ordinary pull_request "
                "or add a reviewed release-chain ADR before changing this gate"
            )


def test_android_workflows_validate_gradle_wrapper_before_gradle_runs():
    workflow_expectations = {
        ".github/workflows/ci.yml": "Run Android unit tests",
        ".github/workflows/release-candidate.yml": "Build Android candidates without release signing secrets",
        ".github/workflows/release.yml": "Build signed Android release APK",
    }

    for rel, first_gradle_step_name in workflow_expectations.items():
        text = _read(rel)
        validation = text.find("uses: gradle/actions/wrapper-validation@v6")
        first_gradle_step = text.find(first_gradle_step_name)

        assert validation != -1, f"{rel} must validate the Gradle wrapper"
        assert first_gradle_step != -1, f"{rel} missing expected Gradle step"
        assert validation < first_gradle_step, f"{rel} must validate the wrapper before running Gradle"


def test_ci_compiles_residual_android_instrumented_qa_sources():
    workflow = _read(".github/workflows/ci.yml")
    gradle = _read("android/app/build.gradle.kts")
    backlog = _read("docs/INSTRUMENTED_QA_BACKLOG.md")

    assert "./gradlew :app:compileDebugAndroidTestKotlin --no-daemon" in workflow
    assert "connectedDebugAndroidTest" not in workflow
    assert 'testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"' in gradle
    assert 'androidTestImplementation("androidx.test:runner:1.6.2")' in gradle
    assert "Owner/manual QA gate" in backlog
    assert "Issue #36" in backlog


def test_ci_runs_android_lint_for_minsdk_compatibility():
    workflow = _read(".github/workflows/ci.yml")

    assert "./gradlew :app:lintDebug --no-daemon" in workflow


def test_release_candidate_runs_regressions_before_packaging():
    workflow = _read(".github/workflows/release-candidate.yml")

    assert "python -m pytest -q" in workflow
    assert ":app:lintDebug" in workflow
    assert ":app:lintRelease" in workflow


def test_residual_ime_android_test_scaffolds_stay_ignored_until_device_qa_runs():
    backlog = _read("docs/INSTRUMENTED_QA_BACKLOG.md")
    residual = _read("android/app/src/androidTest/kotlin/com/clipvault/app/ime/ResidualImeChecksTest.kt")

    expected_methods = {
        "fullKeyboard_stripVisible_and_tapCommitsText": "Full Keyboard #1-2",
        "panelIme_switch_and_tapCommitsText": "Panel IME #1-3, #5",
        "panelIme_explicitSave_requiresUserTap": "Panel IME #8",
        "sensitiveEditor_clearsRenderedAndInFlightCandidates": "FK #3-4, Panel #6-7",
        "sensitiveEditor_blocksExplicitClipboardSave": "Panel sensitive-save gate",
    }
    ignore_reason = "residual: needs a device/emulator; see docs/INSTRUMENTED_QA_BACKLOG.md"

    assert residual.count("@Ignore(") == len(expected_methods)
    assert "does not run" in backlog
    assert "`connectedDebugAndroidTest`" in backlog
    assert re.search(r"does\s+not\s+satisfy\s+the\s+Owner/manual\s+QA\s+gate\s+for\s+Issue\s+#36", backlog)

    for method, manual_item in expected_methods.items():
        assert f"| `{method}` | {manual_item} |" in backlog
        assert re.search(
            rf"@Test\s+@Ignore\(\"{re.escape(ignore_reason)}\"\)\s+fun {method}\(\)",
            residual,
        ), f"{method} must remain an ignored device/emulator scaffold until executed QA is recorded"


def test_residual_ime_backlog_tracks_current_release_gate():
    backlog = _read("docs/INSTRUMENTED_QA_BACKLOG.md")
    version_sync = _read("docs/VERSION_SYNC.md")
    research = _read("docs/RESEARCH_AND_ROADMAP.md")

    assert re.search(r"current Issue #36 / v1\.6\.0 manual QA\s+gate", backlog)
    assert "`docs/MANUAL_QA_V1_6_0.md`" in backlog
    assert "Issue #36 evidence comment" in backlog
    assert "connectedDebugAndroidTest" in backlog
    assert "docs/MANUAL_QA_V1_5_16.md" not in backlog
    assert "v1.5.16 manual QA gate" not in backlog
    assert "Final `v1.6.0` GitHub Release publication remains blocked" in version_sync
    assert "R74 | Residual IME backlog gate routing" in research


def test_windows_clipboard_privacy_manual_qa_probe_is_documented():
    probe_path = _ROOT / "tools/clipboard_privacy_probe.py"
    assert probe_path.exists()
    probe = probe_path.read_text(encoding="utf-8")
    manual_qa = _read("docs/MANUAL_QA_V1_6_0.md")
    handoff = _read("docs/HANDOFF.md")

    assert "Manual QA helper" in probe
    assert "Issue #36" in probe
    assert "overwrites the current Windows clipboard" in manual_qa
    assert "does not by itself satisfy the" in manual_qa
    assert "tools/clipboard_privacy_probe.py" in manual_qa
    assert "tools/clipboard_privacy_probe.py" in handoff

    for probe_case in ("exclude-monitor", "viewer-ignore", "history-off", "cloud-off", "normal"):
        assert f"python tools/clipboard_privacy_probe.py {probe_case}" in manual_qa

    for format_name in (
        "ExcludeClipboardContentFromMonitorProcessing",
        "Clipboard Viewer Ignore",
        "CanIncludeInClipboardHistory",
        "CanUploadToCloudClipboard",
    ):
        assert format_name in probe
        assert format_name in manual_qa
        assert format_name in handoff

    for win32_api in (
        "OpenClipboard",
        "EmptyClipboard",
        "SetClipboardData",
        "GlobalAlloc",
        "GMEM_MOVEABLE",
    ):
        assert win32_api in probe


def test_workflows_use_node24_compatible_github_actions():
    minimum_major = {
        "actions/checkout": 5,
        "actions/setup-python": 6,
        "actions/setup-java": 5,
        "actions/upload-artifact": 7,
        "actions/download-artifact": 8,
        "actions/attest-build-provenance": 4,
    }
    workflows = sorted((_ROOT / ".github/workflows").glob("*.yml"))
    assert workflows, "no GitHub Actions workflows found"

    for path in workflows:
        text = path.read_text(encoding="utf-8")
        for action, minimum in minimum_major.items():
            for match in re.finditer(rf"uses:\s*{re.escape(action)}@v(\d+)", text):
                major = int(match.group(1))
                assert major >= minimum, (
                    f"{path.relative_to(_ROOT)} uses {action}@v{major}; "
                    f"use v{minimum}+ so workflows run on Node.js 24-compatible action runtimes"
                )


def test_stability_plan_defines_v1_7_exit_criteria_without_release_overclaim():
    plan = _read("docs/STABILITY_PLAN_V1_6_V1_7.md")

    assert "## v1.7 stable exit criteria" in plan
    assert "Do not call v1.7 stable until every row below has evidence recorded" in plan
    assert "dedicated release-gate issue" in plan

    for required_area in (
        "IME privacy boundary",
        "Manual QA automation",
        "Release supply-chain",
        "Capture-layer privacy",
        "Local-first sync reliability",
        "Documentation-as-release-evidence",
        "Current-main packaging evidence",
        "Field-test package evidence",
    ):
        assert required_area in plan

    for evidence_tier in (
        "Required automated evidence",
        "Required CI evidence",
        "Required Owner/manual evidence",
        "Stable exit decision",
    ):
        assert evidence_tier in plan

    for blocker_truth in (
        "Not stable if compile-only scaffolds are claimed as executed QA.",
        "Not stable if unsigned dry-run artifacts are described as signed release artifacts.",
        "Android production log source-shape privacy",
        "Not stable if any typed-text logging, implicit save, Android production log payload interpolation",
        "auth-failure response-body skipping",
        "auth-failure bodies can mask token clearing",
        "outbound push request-body budgeting",
        "oversized push bodies can wedge WorkManager retries",
        "do not publish `v1.7.0` from this plan alone.",
        "Treat blocked Owner/manual rows as incomplete",
        "release-artifact main-ref dispatch",
        "signed release artifacts can be built from a non-`main` ref",
        "field-test packages are not signed/final release evidence",
        "Android unsigned release APK is not a signed install package",
    ):
        assert blocker_truth in plan


def test_v1_7_field_test_packages_use_release_candidates_without_stable_overclaim():
    plan = _read("docs/STABILITY_PLAN_V1_6_V1_7.md")
    field_test = _read("docs/V1_7_FIELD_TEST_PACKAGES.md")
    research = _read("docs/RESEARCH_AND_ROADMAP.md")
    workflows = _read("docs/AGENT_WORKFLOWS.md")
    handoff = _read("docs/HANDOFF.md")
    workflow = _read(".github/workflows/release-candidate.yml")
    script = _ROOT / "tools/field_test_evidence.py"
    readiness_script = _ROOT / "tools/field_test_readiness.py"
    owner_pack_script = _ROOT / "tools/prepare_field_test_owner_pack.py"

    assert script.exists()
    assert readiness_script.exists()
    assert owner_pack_script.exists()
    script_text = script.read_text(encoding="utf-8")
    readiness_text = readiness_script.read_text(encoding="utf-8")
    owner_pack_text = owner_pack_script.read_text(encoding="utf-8")
    assert "does not download artifacts" in script_text
    assert "--verify-artifacts" in script_text
    assert "verify_release_manifest.py" in script_text
    assert "expect_dry_run=True" in script_text
    assert "field_test_ready" in script_text
    assert "Read-only readiness report" in readiness_text
    assert "does not trigger workflows" in readiness_text
    assert "download artifacts" in readiness_text
    assert "claim v1.7 stable" in readiness_text
    assert "gh api" in readiness_text
    assert "refusing write-capable gh api flag" in readiness_text
    assert "Owner action pack only" in owner_pack_text
    assert "does not download artifacts, install apps" in owner_pack_text
    assert "claim v1.7 stable" in owner_pack_text
    assert "--verify-artifacts" in field_test
    assert "python tools/prepare_field_test_owner_pack.py" in field_test
    assert "OWNER_FIELD_TEST_ACTION_PACK.md" in field_test
    assert "pack-summary.json" in field_test
    assert "python tools/field_test_readiness.py --no-fail" in field_test
    assert "python tools/field_test_readiness.py --json --no-fail" in field_test
    assert "stale issue-body baseline" in field_test
    assert "does not download artifacts, verify local artifact bytes" in field_test
    assert "artifact-only Issue #82" in field_test
    assert "expected to remain `BLOCKED`" in field_test
    assert "tools/field_test_evidence.py `" in field_test
    assert "--windows-dir field-test-v1.7/windows" in field_test
    assert "--android-dir field-test-v1.7/android" in field_test
    assert "device QA, post to GitHub" in field_test
    assert "python tools/field_test_evidence.py --write-template field-test-v1.7.json" in field_test
    assert "python tools/field_test_evidence.py --input field-test-v1.7.json --no-fail" in field_test
    assert "python tools/field_test_evidence.py --input field-test-v1.7.json --output field-test-v1.7-issue-comment.md" in field_test
    assert "docs/V1_7_FIELD_TEST_PACKAGES.md" in plan
    assert "tools/field_test_evidence.py --verify-artifacts" in plan
    assert "Release candidate dry run" in field_test
    assert "clipvault-windows-release-candidate" in field_test
    assert "clipvault-android-release-candidate" in field_test
    assert "scripts/verify_release_manifest.py" in field_test
    assert "--expect-dry-run" in field_test
    assert "The current source metadata remains `1.6.0`" in field_test
    assert "No version bump to `1.7.0`" in field_test
    assert "does not declare v1.7 stable" in field_test
    assert "does not publish `v1.7.0`" in field_test
    assert "does not close Issue #36" in field_test
    assert "not signed/final release evidence" in field_test
    assert "Android unsigned release APK is not a signed install package" in field_test
    assert "use `ClipVault-Android-v<version>-debug.apk` for real-device install" in field_test

    assert "name: clipvault-windows-release-candidate" in workflow
    assert "name: clipvault-android-release-candidate" in workflow
    assert "Release candidate dry run" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "environment:" not in workflow
    assert "ANDROID_RELEASE_KEYSTORE" not in workflow

    assert "v1.7 field-test packages" in workflows
    assert "不得把 unsigned candidate artifacts 冒充为 signed/final release evidence" in workflows
    assert "docs/V1_7_FIELD_TEST_PACKAGES.md" in handoff
    assert "does not claim v1.7 stable" in handoff
    assert "unsigned candidate artifacts as signed/final release evidence" in handoff
    assert "tools/field_test_evidence.py" in handoff
    assert "tools/field_test_readiness.py" in handoff
    assert "tools/prepare_field_test_owner_pack.py" in handoff
    assert "tools/v2_keyboard_readiness.py" in handoff
    assert "stale issue-body baselines" in handoff
    assert "does not trigger workflows" in handoff
    assert "can use `--verify-artifacts`" in handoff
    assert "run device QA, post to GitHub" in handoff
    assert "R86 | v1.7 candidate package upload lane" in research
    assert "R87 | Structured v1.7 field-test evidence" in research
    assert "R88 | Read-only v1.7 field-test readiness" in research
    assert "R89 | v2.0 dual-IME local readiness aggregator" in research
    assert "R96 | Owner field-test action pack" in research
    assert "workflow-run artifacts REST API" in research
    assert "does not replace `tools/field_test_evidence.py`" in research
    assert "can use `--verify-artifacts`" in research
    assert "dry-run manifest/checksum verifier" in research
    assert "candidate-only upload/download/manifest-verification path" in research
    assert "single current-main action pack" in research
    assert "does not download artifacts, install apps, run device QA" in research


def test_stability_plan_defines_v2_0_exit_criteria_without_release_overclaim():
    plan = _read("docs/STABILITY_PLAN_V2_0.md")
    roadmap = _read("docs/ROADMAP_V2_KEYBOARD.md")
    gates = _read("docs/GATES.md")
    research = _read("docs/RESEARCH_AND_ROADMAP.md")

    assert "## Scope lock" in plan
    assert "## v2.0 stable exit criteria" in plan
    assert "tools/v2_keyboard_readiness.py --no-fail" in plan
    assert "v2.0 means the same APK exposes two IME entrypoints" in plan
    assert "ClipVault Panel IME" in plan
    assert "ClipVault Keyboard Lab" in plan
    assert "Issue #36 / v1.6.0 is closed" in plan
    assert "docs/STABILITY_PLAN_V1_6_V1_7.md" in plan
    assert "dedicated v2.0 release-gate issue" in plan

    for required_area in (
        "Dual IME registration",
        "Keyboard Lab baseline controls",
        "Panel IME baseline controls",
        "IME privacy boundary",
        "Local-first runtime compatibility",
        "Documentation and release truth",
    ):
        assert required_area in plan

    for evidence_tier in (
        "Required automated evidence",
        "Required CI evidence",
        "Required Owner/manual evidence",
        "Stable exit decision",
    ):
        assert evidence_tier in plan

    for blocker_truth in (
        "v2.0 does not mean the v2.1 librime/fcitx5 production engine.",
        "v2.0 does not mean the optional LAN TLS transport-hardening branch.",
        "Not stable if L0/L1 typed text is persisted, learned, logged, synced, or saved without explicit user action.",
        "Do not wire librime/fcitx5 into the production IME.",
        "Do not start v2.2 CandidateMixer",
        "Do not add network work inside any IME service.",
    ):
        assert blocker_truth in plan
    assert re.search(r"A planning label or source-tree version is not\s+release\s+evidence\.", plan)

    assert "STABILITY_PLAN_V2_0.md" in roadmap
    assert "v2.0 门禁（双 IME 入口）" in gates
    assert "R84 | v2.0 stability evidence taxonomy" in research
    assert "do not relabel v2.1 librime/fcitx5 build-PoC work" in research


def test_top_level_agents_file_matches_current_release_gate():
    agents = _read("AGENTS.md")

    assert "Issue #3 / the v1.5 gate is closed." in agents
    assert "Issue #36 is the current v1.6.0 release" in agents
    assert "Do not claim v1.6 stable" in agents
    assert "Current main CI result is known." in agents
    assert "Current main release-candidate dry run result is known." in agents
    assert "Owner-controlled final Windows artifacts and signed Android artifacts exist." in agents
    assert "Manual QA checklist passes with evidence." in agents
    assert "Final `v1.6.0` GitHub Release publication is Owner-approved." in agents
    assert "Do not claim v1.7 stable until docs/STABILITY_PLAN_V1_6_V1_7.md" in agents
    assert "Do not claim v2.0 stable until docs/STABILITY_PLAN_V2_0.md" in agents
    assert re.search(r"v2\.0 is\s+the dual-IME-entrypoint stability line", agents)
    assert "Do not close Issue #36 without CI, signed artifact, final release, and manual" in agents

    for stale_claim in (
        "Current v1.5 blockers",
        "Do not start v1.6 work until these are closed",
        "Do not close Issue 3 without CI and manual QA evidence.",
        "Owner-controlled signed Windows/Android artifacts exist.",
    ):
        assert stale_claim not in agents


def test_agent_workflows_status_anchor_avoids_stale_test_counts_and_overclaims():
    workflows = _read("docs/AGENT_WORKFLOWS.md")

    assert "python -m pytest -q` 输出和 GitHub CI 为准" in workflows
    assert "不要把旧的固定测试数量写成发布证据" in workflows
    assert "v1.6 release gate（Issue #36）" in workflows
    assert "signed artifacts、Owner/manual QA、最终 GitHub Release 发布前不得关闭" in workflows
    assert "v1.7 stable" in workflows
    assert "不得声称 `v1.7.0` 已发布或稳定完成" in workflows
    assert "v2.0 stable" in workflows
    assert "v2.0 是双 IME 入口稳定线" in workflows
    assert "不得把 v2.1 librime 或 TLS 支线冒充为 v2.0 发布证据" in workflows

    assert "桌面测试：**179**" not in workflows
    assert "Linux/CI 跑通 + 4 项 Windows-only" not in workflows


def test_handoff_current_state_anchors_v1_6_gate_before_v1_7_or_v2_work():
    handoff = _read("docs/HANDOFF.md")

    current_state = handoff.split("## Current development note", 1)[0]
    assert "v1.6.0 release gate, v1.7 stability planning, and v2.0 dual-IME stability planning" in current_state
    assert "Issue #36 remains open" in current_state
    assert "Owner-controlled final Windows artifacts, signed Android artifacts" in current_state
    assert "Owner-approved GitHub Release publication" in current_state
    assert "v1.7 stays planning/stability-only" in current_state
    assert "v2.0 stays planning/stability-only" in current_state
    assert "docs/STABILITY_PLAN_V2_0.md" in current_state
    assert "dedicated Owner-approved v2.0 release-gate issue" in current_state
    assert "signed Windows/Android artifacts" not in current_state
    assert "v2.1 V2-S004" not in current_state

    current_development_note = handoff.split("## Current development note", 1)[1].split(
        "## Recent completed note", 1
    )[0]
    assert "tools/release_readiness.py" in current_development_note
    assert re.search(r"without\s+triggering\s+workflows,\s+setting\s+secrets,\s+creating\s+releases", current_development_note)
    assert re.search(r"prints\s+the\s+exact\s+unchecked\s+release-gate\s+checklist\s+items", current_development_note)
    assert "docs/STABILITY_PLAN_V2_0.md" in current_development_note
    assert "dual-IME entrypoint stability milestone" in current_development_note
    assert "does not claim" in current_development_note
    assert "v2.0 stable" in current_development_note
    assert "tools/v2_keyboard_readiness.py" in current_development_note
    assert "does not call GitHub, trigger workflows" in current_development_note

    current_version = handoff.split("## Current Version Status", 1)[1].split(
        "## Hardening Support Line Snapshot", 1
    )[0]
    assert "`v1.6.0` GitHub Release is not published" in current_version
    assert "Latest downloadable binaries remain **v1.5.10**" in current_version
    assert re.search(r"do not cite stale fixed test counts as\s+current release evidence", current_version)
    assert "Issue #36 remains the release gate" in current_version
    assert "final Windows artifacts, signed Android" in current_version
    for stale_release_evidence in (
        "桌面 134 测试",
        "166 项 Linux 跑通",
        "4 项 Windows-only",
        "signed Windows/Android artifacts",
    ):
        assert stale_release_evidence not in current_version

    assert "## v1.6 Release Gate — Issue #36 OPEN" in handoff
    release_gate = handoff.split("## v1.6 Release Gate — Issue #36 OPEN", 1)[1]
    assert re.search(r"v1\.6\s+stable/release is not complete", release_gate)
    assert "Owner-controlled final" in release_gate
    assert "signed Android artifacts" in release_gate
    assert "signed Windows/Android artifacts" not in release_gate
    assert re.search(r"must not claim\s+`v1\.7\.0` stable or published", release_gate)
    assert "v1.6 Entry Gate" not in handoff
