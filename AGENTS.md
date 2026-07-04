# AGENTS.md

Minimal instructions for coding agents working on ClipVault Personal.

For task flow and role split, read docs/AGENT_WORKFLOWS.md.

## Product boundary

- Keep the Android IME local-first.
- Do not add typed-text logging.
- Do not add analytics or tracking SDKs.
- Keep network work outside the IME service.
- Keep explicit user action for saving content.

## Current release blockers

Issue #3 / the v1.5 gate is closed. Issue #36 is the current v1.6.0 release
gate.

Do not claim v1.6 stable, publish final release artifacts, or close Issue #36
until these are recorded:

- Current main CI result is known.
- Current main release-candidate dry run result is known.
- Owner-controlled signed Windows/Android artifacts exist.
- Manual QA checklist passes with evidence.
- Final `v1.6.0` GitHub Release publication is Owner-approved.

Do not claim v1.7 stable until docs/STABILITY_PLAN_V1_6_V1_7.md exit criteria
are satisfied and a dedicated release issue has Owner approval.

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
- Do not close Issue #36 without CI, signed artifact, final release, and manual
  QA evidence.
