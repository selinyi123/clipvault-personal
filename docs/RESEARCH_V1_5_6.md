# ClipVault Personal v1.5.6 — Metadata alignment

Date: 2026-06-21

Scope:

> close the desktop version mismatch and reassess the remaining Android and CI blockers.

## 1. Research inputs

This round avoided repeating clipboard managers, keyboard privacy, Keystore, ULID, CandidateMixer ranking, InputType suppression, local Android test basics, and basic CI setup.

### 1.1 Python project version metadata

The Python Packaging User Guide states that `version` is a required project metadata key, either specified statically in `[project]` or listed as dynamic. It also states that the project version is the package distribution version and users should prefer normalized versions.

Decision:

- Keep a static `[project] version` for now.
- Align `desktop/pyproject.toml` and `desktop/clipvault/__init__.py` at `1.5.6`.

### 1.2 GitHub Actions manual runs

GitHub Actions supports `workflow_dispatch`, but manual workflow edits were blocked in the current tool session. The existing CI still runs on push and pull request.

Decision:

- Do not keep retrying workflow rewrites.
- Use the existing push / pull_request CI as the validation path until a narrow workflow patch is available.

### 1.3 Android app version remains isolated

The Android app version lives in a file that also contains release signing configuration. Prior attempts to rewrite that file were blocked.

Decision:

- Keep Android version-only patch as a separate narrow task.
- Do not modify release signing configuration.

## 2. Implementation status

Done:

- `desktop/pyproject.toml` is now `version = "1.5.6"`.
- `desktop/clipvault/__init__.py` is now `__version__ = "1.5.6"`.
- Android version mismatch has been re-confirmed and remains open.

Still open:

- Android app version remains `versionName = "1.5.0"` / `versionCode = 8`.
- Panel IME still needs migration to `runtime.listCandidates()`.
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

### v1.5.7

- Read GitHub Actions status from the Actions UI or a connector endpoint that lists push-triggered runs.
- Fix the first concrete CI failure.
- Apply Android version-only patch without changing release signing logic.
- Migrate Panel IME to `runtime.listCandidates()` without changing save-clipboard behavior.

### v1.6

Only after v1.5 blockers are closed:

- source toggles
- candidate source caps
- transient query-aware filtering without storing typed text
