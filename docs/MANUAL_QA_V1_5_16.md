# ClipVault Personal v1.5.16 — Manual QA checklist

Date: 2026-06-25

Scope: define the manual validation gate required before closing the v1.5 CandidateMixer node.

## Automated coverage (no device required)

As much of this gate as possible is now enforced by deterministic tests that run
in CI, so it does not depend on a human with a device. What each automated test
covers:

| Manual section | Covered by | Notes |
|---|---|---|
| CI validation | GitHub Actions `CI` (Desktop + Android jobs) | runs on every push/PR |
| Desktop validation | `desktop/tests/` via `pytest` | full suite green |
| Android validation | `:core:test`, `:app:testDebugUnitTest`, `:app:assembleDebug` (CI) | unit tests + debug build |
| Release-state / version alignment | `desktop/tests/test_release_alignment.py` | fails CI on any version drift |
| Sensitive-field suppression decision (FK #3–6, Panel #6–7) | `PrivacyAwareFilterTest` | password / no-suggestions / numeric-password |
| Suppression message present (Panel #7) | `PrivacyAwareFilterTest.suppressionMessageIsNonBlank` | non-empty message |
| Recent tab = clips only (Panel #3) | `PanelCandidateTabsTest.recentTabKeepsClipsAndDropsMemory` | |
| Memory tabs filter by kind (Panel #4) | `PanelCandidateTabsTest.memoryTabsFilterToTheirOwnKind` | term/phrase/prompt/command |
| Candidate ordering / mix | `CandidateMixerTest` | |
| clip_meta pin/favorite sync | `desktop/tests/test_sync.py::test_h7_clip_meta_*` | both directions + wire contract |

### Residual checks that still require a physical device

These verify on-screen rendering and live IME interaction, which cannot be
exercised on the host JVM and would need an instrumented/emulator test (out of
scope per docs/AGENT_WORKFLOWS.md — "does not add emulator CI unless explicitly
planned"):

- FK #1–2: candidate strip is visible; tapping a candidate commits text.
- Panel #1–2, #5: switching to the Panel IME; tapping a candidate commits text.
- Panel #8: the explicit save action requires a user tap (no implicit save).
- Sensitive transition: already-rendered/in-flight candidates are cleared when
  the same IME moves from a normal field to password/incognito; Panel save is disabled.

The decision logic behind every residual item is unit-tested above; only the
view rendering and input-connection wiring are unverified by automation.

These five are now filed as a planned instrumented (`androidTest`) task:
`docs/INSTRUMENTED_QA_BACKLOG.md` holds the wiring plan, and
`android/app/src/androidTest/kotlin/com/clipvault/app/ime/ResidualImeChecksTest.kt`
encodes them as `@Ignore`-d scaffolds ready to implement on a device/emulator.

## Preconditions

- Desktop node can run local tests.
- Android app can build a debug APK.
- Android device has ClipVault Panel IME and Full Keyboard enabled.
- Device has at least one recent clip and several memory candidates.

## CI validation

GitHub Actions workflow `.github/workflows/ci.yml` supports manual `workflow_dispatch`.

Use this before closing Issue 3:

1. Open the repository Actions tab.
2. Select the `CI` workflow.
3. Run the workflow on `main`.
4. Record the desktop and Android job results in Issue 3.

Expected result:

- Desktop tests pass.
- Android unit tests pass.
- Android debug APK builds.

## Desktop validation

Run from repository root:

```bash
cd desktop
python -m pytest -q
```

Expected result:

- all desktop tests pass.

## Android validation

Run from repository root:

```bash
cd android
./gradlew :core:test :app:testDebugUnitTest --no-daemon
./gradlew :app:assembleDebug --no-daemon
```

Expected result:

- core tests pass.
- app unit tests pass.
- debug APK builds.

## Full Keyboard checks

1. Open a normal text field.
2. Confirm candidate strip is visible.
3. Tap a candidate and confirm it commits text.
4. Open a sensitive field.
5. Confirm candidates are hidden.
6. Return to a normal field and confirm candidates reappear.

## Panel IME checks

1. Open a normal text field.
2. Switch to ClipVault Panel IME.
3. Confirm Recent tab shows clip candidates.
4. Confirm term, phrase, prompt, and command tabs show matching memory candidates when data exists.
5. Tap a candidate and confirm it commits text.
6. With candidates already visible, move focus to a sensitive field without switching IMEs.
7. Confirm the previous candidate list is immediately replaced by the suppression message and does not refill.
8. Confirm “保存剪贴板” is disabled and Room/outbox do not change.
9. Return to a normal field and confirm explicit save still requires a user tap.

## Release-state checks

- Desktop runtime version is 1.5.16.
- Desktop package metadata is 1.5.16.
- Android versionName is 1.5.16.
- Android versionCode is 12 or higher.
- Windows installer AppVersion is 1.5.16.
- PanelCandidateTabs helper exists.
- PanelCandidateTabsTest exists and passes.
- GitHub Actions status is recorded before closing Issue 3.

## Close criteria

Automated (enforced by CI — currently green):

- desktop tests pass.
- Android unit tests pass.
- Android debug build passes.
- visible version metadata is aligned (`test_release_alignment.py`).
- candidate suppression, panel tab filtering, and ordering logic pass.

Residual (physical device only — see "Residual checks" above):

- Full Keyboard strip render + tap-commit.
- Panel IME switch + tap-commit + explicit-save-requires-tap.
- Normal→sensitive transition clears rendered/in-flight candidates and blocks save.

If no device is available, the automated gate above is fully green and the
residual items are the only thing left. They should be signed off by the
maintainer or deferred to a planned instrumented test — not silently marked
passed.
