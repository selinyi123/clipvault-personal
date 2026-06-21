# ClipVault Personal v1.5.5 — Version consistency review

Date: 2026-06-21

Scope:

> review CI/testability risk after v1.5.4, fix low-risk version consistency where possible, and keep blocked edits explicit.

## 1. Research inputs

This round avoided repeating clipboard managers, keyboard privacy, Keystore, ULID, CandidateMixer ranking, InputType suppression, and basic CI setup.

### 1.1 Android local unit tests

Android local unit tests run on the host JVM, not on a device or emulator. This makes them fast, but code depending on Android framework behavior must be isolated or mocked.

Decision:

- CandidateMixer tests are acceptable because they use Room data classes as plain Kotlin data objects.
- PrivacyAwareFilter tests are acceptable because they use `InputType` integer constants and do not invoke Android framework methods.

### 1.2 Android mockable library risk

The Android Gradle Plugin supplies a mockable Android library for local tests. It allows tests to compile against Android classes, but method bodies are removed and method calls can throw if accessed.

Decision:

- Keep PrivacyAwareFilter logic as an integer bitmask pure function.
- Avoid tests that instantiate `EditorInfo` or call Android framework methods unless Robolectric or instrumentation tests are introduced.

### 1.3 Manual workflow trigger

GitHub Actions supports `workflow_dispatch` for manual runs when the workflow exists on the default branch.

Decision:

- `workflow_dispatch` remains desirable for validation, but editing workflow files was blocked in the current tool session.
- Do not repeatedly rewrite workflow files until a narrow patch path is available.

## 2. Implementation status

Done:

- Reviewed Android data classes used by `CandidateMixerTest`; constructor arguments match current `ClipEntity` and `MemoryEntity`.
- Reviewed `PrivacyAwareFilterTest`; it avoids Android framework method calls.
- Desktop runtime version bumped to `1.5.5`.

Blocked:

- `desktop/pyproject.toml` still has package metadata version `0.1.0`; updating it was blocked by repository write safety checks.
- Android app version still remains `1.5.0` / versionCode 8.
- Panel IME migration still remains open.
- CI run status is still unavailable through the current connector response.

## 3. Current validation commands

```bash
cd desktop
python -m pytest -q

cd ../android
chmod +x ./gradlew
./gradlew :core:test :app:testDebugUnitTest --no-daemon
./gradlew :app:assembleDebug --no-daemon
```

## 4. Next node

### v1.5.6

- Fix `desktop/pyproject.toml` version metadata with a narrower edit path.
- Read GitHub Actions run logs through the Actions UI if connector runs remain unavailable.
- Fix first concrete CI failure.
- Apply Android version-only patch.
- Migrate Panel IME to `runtime.listCandidates()` without changing save-clipboard behavior.

### v1.6

Only after v1.5 blockers are closed:

- source toggles
- candidate source caps
- transient query-aware filtering without storing typed text
