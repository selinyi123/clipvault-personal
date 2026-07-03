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


def test_panel_candidate_tabs_helper_and_test_exist():
    base = _ROOT / "android/app/src"
    assert (base / "main/kotlin/com/clipvault/app/ime/PanelCandidateTabs.kt").exists()
    assert (base / "test/kotlin/com/clipvault/app/ime/PanelCandidateTabsTest.kt").exists()


def test_signed_release_workflow_is_manual_secret_gated_and_verifies_apk():
    workflow = _read(".github/workflows/release.yml")

    assert "workflow_dispatch:" in workflow
    assert "\n  push:" not in workflow
    assert "\n  pull_request:" not in workflow
    assert "environment: release" in workflow
    assert "ANDROID_RELEASE_KEYSTORE_B64" in workflow
    assert "ANDROID_RELEASE_KEYSTORE_PASSWORD" in workflow
    assert "ANDROID_RELEASE_KEY_ALIAS" in workflow
    assert "ANDROID_RELEASE_KEY_PASSWORD" in workflow
    assert "apksigner" in workflow
    assert "verify --print-certs" in workflow
    assert "trap 'rm -f \"${keystore:-}\"' EXIT" in workflow
    assert "actions/attest-build-provenance@v2" in workflow
    assert "create_draft_release" in workflow
    assert "upload-assets" in workflow
    assert "windows-${base}" in workflow
    assert "android-${base}" in workflow
    assert "--draft" in workflow
    assert "validate-release-input:" in workflow
    assert "version must be a release tag like v1.6.0" in workflow
    assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in workflow
    assert "needs: validate-release-input" in workflow
    assert "needs.validate-release-input.outputs.version" in workflow


def test_manual_qa_links_v1_6_release_runbook():
    runbook = _ROOT / "docs/RELEASE_RUNBOOK_V1_6_0.md"
    manual_qa = _read("docs/MANUAL_QA_V1_6_0.md")

    assert runbook.exists()
    assert "RELEASE_RUNBOOK_V1_6_0.md" in manual_qa
    assert "Release artifact build" in runbook.read_text(encoding="utf-8")


def test_release_runbook_uses_live_main_evidence_commands():
    runbook = _read("docs/RELEASE_RUNBOOK_V1_6_0.md")

    assert "gh run list" in runbook
    assert "gh workflow run \"Release candidate dry run\"" in runbook
    assert "CI_RUN_ID" in runbook
    assert "RELEASE_CANDIDATE_DRY_RUN_ID" in runbook
    assert not re.search(r"https://github\.com/[^)\s]+/actions/runs/\d+", runbook)
    assert not re.search(r"\b[0-9a-f]{40}\b", runbook)
