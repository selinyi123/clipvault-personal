# ClipVault Personal v1.5.4 — Validation blockers and plan

Date: 2026-06-21

Scope:

> record the current validation blockers after adding CI, and keep the next node focused on build evidence rather than feature expansion.

## 1. Research inputs

This round avoided repeating keyboard privacy, clipboard managers, ULID, Keystore, CandidateMixer ranking, and InputType tests.

### 1.1 CI workflows should stay small

A recent empirical study of GitHub Actions workflows reports that larger and more complex workflow configurations correlate with higher failure and maintenance risk.

Decision:

- Keep the existing CI small.
- Avoid release build, signing, deployment, and matrix expansion until base tests are green.

### 1.2 Use explicit tool versions

The Python setup action recommends explicitly setting the Python version instead of relying on runner defaults.

Decision:

- Keep Python 3.11 in CI because the desktop package requires Python 3.11 or newer.

### 1.3 Gradle should run through the wrapper

Gradle Actions documentation recommends executing builds through the Gradle Wrapper when a project has one.

Decision:

- Keep Android CI commands under the `android` directory and use `./gradlew`.

## 2. Implementation status

Done:

- Existing CI was reviewed.
- Desktop version bumped to `1.5.4`.

Blocked in this run:

- Updating the existing CI workflow to add a manual trigger was blocked by repository write safety checks.
- Adding a second manual workflow was also blocked.
- Adding a shell validation script was also blocked.

No source behavior was changed in this version.

## 3. Current validation commands

Until the workflow result is available, run the following manually:

```bash
cd desktop
python -m pytest -q

cd ../android
chmod +x ./gradlew
./gradlew :core:test :app:testDebugUnitTest --no-daemon
./gradlew :app:assembleDebug --no-daemon
```

## 4. Remaining v1.5 blockers

- Android app version remains behind desktop.
- Panel IME still needs migration to `runtime.listCandidates()`.
- CI run result is not yet available through the current GitHub connector response.

## 5. Next node

### v1.5.5

- Read GitHub Actions run status through the Actions UI or a connector endpoint that lists push-triggered runs.
- Fix the first concrete CI failure.
- Apply Android version-only patch.
- Migrate Panel IME with a narrow patch that avoids changing save-clipboard behavior.

### v1.6

Only start after v1.5 blockers are closed:

- candidate source toggles
- source caps
- query-aware filtering without storing typed text
