# v1.5.16 Release Audit

Date: 2026-06-25

This file records the repository evidence for v1.5.16.

## Version files

- `desktop/clipvault/__init__.py`: 1.5.16
- `desktop/pyproject.toml`: 1.5.16
- `android/app/build.gradle.kts`: versionName 1.5.16, versionCode 12
- `installer/clipvault.iss`: 1.5.16
- `docs/VERSION_SYNC.md`: aligned at 1.5.16
- `docs/HANDOFF.md`: current slice is v1.5.16

## Functional files

- `android/app/src/main/kotlin/com/clipvault/app/runtime/ClipVaultFacade.kt`
- `android/app/src/main/kotlin/com/clipvault/app/ime/PanelCandidateTabs.kt`
- `android/app/src/main/kotlin/com/clipvault/app/ime/ClipVaultPanelImeService.kt`
- `android/app/src/main/kotlin/com/clipvault/app/ime/ClipVaultFullKeyboardService.kt`
- `android/app/src/main/kotlin/com/clipvault/app/data/Db.kt`
- `android/app/src/main/kotlin/com/clipvault/app/sync/Sync.kt`

## Test files

- `android/app/src/test/kotlin/com/clipvault/app/runtime/CandidateMixerTest.kt`
- `android/app/src/test/kotlin/com/clipvault/app/ime/PrivacyAwareFilterTest.kt`
- `android/app/src/test/kotlin/com/clipvault/app/ime/PanelCandidateTabsTest.kt`
- `desktop/tests/test_api.py`
- `desktop/tests/test_config.py`
- `desktop/tests/test_sync.py`

## Validation files

- `.github/workflows/ci.yml`
- `docs/MANUAL_QA_V1_5_16.md`
- `docs/AGENT_WORKFLOWS.md`

## Review fixes after diff audit

- Android dependency declarations are present in `android/app/build.gradle.kts`.
- HANDOFF keeps current v1.5.16 state and restores a compact project-memory snapshot.
- The old manual QA file was replaced by `docs/MANUAL_QA_V1_5_16.md`.

## Risk fixes after broader review

- Panel tabs now request source/kind-specific candidates from Runtime.
- Android pull now mirrors clip pinned/favorite metadata.
- Android sync worker logs only exception classes.
- Android pull ignores unknown or malformed individual events instead of failing the entire batch.
- Desktop API query parameter validation rejects malformed or negative values while preserving high-value clamping.
- Desktop server request handling has explicit size guards.
- New desktop config templates bind to loopback by default.
- Desktop HTTP server version follows package metadata.
- Release endpoint remains bodyless for compatibility.

## Verification workflow status

- CI workflow supports `workflow_dispatch` for manual verification on main.
- Manual QA checklist includes CI trigger instructions.
- Agent workflow treats manual CI dispatch evidence as acceptable CI evidence.

## Remaining review risks

- CI and manual QA evidence are not recorded in this repository yet.
- Android changes still need Gradle unit/build validation.

## Result

v1.5.16 is not only a version-number bump. The repository contains version metadata, runtime candidate logic, panel tab filtering, input frontend wiring, tests, CI definition, and manual QA gate for this release state.

## Remaining gate

Issue 3 should remain open until CI status or manual QA evidence is recorded.
