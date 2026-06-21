# ClipVault Personal v1.5.10 — Panel test foundation

Date: 2026-06-21

Scope: add a small host JVM test foundation for Panel tab filtering.

## Research input

Android device-level tests are valuable but costly in CI. For this project, pure JVM tests remain the first layer for deterministic candidate logic, and manual device checks remain necessary for IME behavior.

## Done

- Added PanelCandidateTabs as a pure helper.
- Added PANEL_CANDIDATE_POOL_LIMIT = 200.
- Added a unit test for filter-before-limit behavior.
- Updated desktop runtime version to 1.5.10.

## Still open

- Main Panel IME service still needs to call the helper.
- Package and Android version metadata still need final alignment.
- CI and manual IME validation are still required.

## Next node

v1.5.11 should wire the helper into the service, align versions, and validate CI/manual IME behavior.
