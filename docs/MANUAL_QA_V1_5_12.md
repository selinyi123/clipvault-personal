# ClipVault Personal v1.5.12 — Manual QA checklist

Date: 2026-06-21

Scope: define the manual validation gate required before closing the v1.5 CandidateMixer node.

## Preconditions

- Desktop node can run local tests.
- Android app can build a debug APK.
- Android device has ClipVault Panel IME and Full Keyboard enabled.
- Device has at least one recent clip and several memory candidates.

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
6. Open a sensitive field.
7. Confirm Panel candidate list is replaced by the suppression message.
8. Confirm the explicit save action still requires a user tap.

## Release-state checks

- PanelCandidateTabs helper exists.
- PanelCandidateTabsTest exists and passes.
- Version metadata is aligned before final release.
- GitHub Actions status is recorded before closing Issue 3.

## Close criteria

Close Issue 3 only when:

- desktop tests pass.
- Android unit tests pass.
- Android debug build passes.
- Full Keyboard manual checks pass.
- Panel IME manual checks pass.
- visible version metadata is aligned.
