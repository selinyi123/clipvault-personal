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

## Test files

- `android/app/src/test/kotlin/com/clipvault/app/runtime/CandidateMixerTest.kt`
- `android/app/src/test/kotlin/com/clipvault/app/ime/PrivacyAwareFilterTest.kt`
- `android/app/src/test/kotlin/com/clipvault/app/ime/PanelCandidateTabsTest.kt`

## Validation files

- `.github/workflows/ci.yml`
- `docs/MANUAL_QA_V1_5_16.md`
- `docs/AGENT_WORKFLOWS.md`

## Result

v1.5.16 is not only a version-number bump. The repository contains version metadata, runtime candidate logic, panel tab filtering, input frontend wiring, tests, CI definition, and manual QA gate for this release state.

## Remaining gate

Issue 3 should remain open until CI status or manual QA evidence is recorded.
