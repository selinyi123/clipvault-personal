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
    assert "umask 077" in workflow
    assert "actions/attest-build-provenance@v4" in workflow
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
    assert "Issue #36 remains open" in template
    assert not re.search(
        r"(?i)\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#\d+\b",
        template,
    )


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
