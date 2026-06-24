# AGENTS.md

Minimal instructions for coding agents working on ClipVault Personal.

## Product boundary

- Keep the Android IME local-first.
- Do not add typed-text logging.
- Do not add analytics or tracking SDKs.
- Keep network work outside the IME service.
- Keep explicit user action for saving content.

## Current v1.5 blockers

Do not start v1.6 work until these are closed:

- Panel IME service uses the tested PanelCandidateTabs helper.
- Desktop runtime and package metadata versions match.
- Android version metadata matches the release node.
- CI result is known.
- Manual QA checklist passes.

## Test commands

Desktop:

```bash
cd desktop
python -m pytest -q
```

Android:

```bash
cd android
./gradlew :core:test :app:testDebugUnitTest --no-daemon
./gradlew :app:assembleDebug --no-daemon
```

## Patch discipline

- Prefer small patches.
- Cite the file or issue being changed in the final report.
- Do not claim tests passed unless they actually ran.
- Do not close Issue 3 without CI and manual QA evidence.
