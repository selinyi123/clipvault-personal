# AGENTS.md

Minimal instructions for coding agents working on ClipVault Personal.

For task flow and role split, read docs/AGENT_WORKFLOWS.md. For the active
multi-agent, multi-branch v2 daily-use program, also read
docs/V2_DAILY_EXECUTION_CHARTER.md before changing implementation code.

## Product boundary

- Keep the Android IME local-first.
- Do not add typed-text logging.
- Do not add analytics or tracking SDKs.
- Keep network work outside the IME service.
- Keep explicit user action for saving content.

## Current release baseline and blockers

Issue #3 / the v1.5 gate is closed. `v1.6.0` was published on 2026-07-30 and
Issue #36 was closed by explicit Owner risk exception. Its final manual
worksheet recorded 15 pass, 0 fail, and 10 blocked items. Preserve that result:
do not describe the blocked checks as passed or as complete manual QA.

Do not mutate the published release, tags, assets, Issue #36, or other remote
release state without fresh explicit Owner authorization.

Do not claim v1.7 stable until docs/STABILITY_PLAN_V1_6_V1_7.md exit criteria
are satisfied and a dedicated release issue has Owner approval.

Do not claim v2.0 stable until docs/STABILITY_PLAN_V2_0.md exit criteria are
satisfied and a dedicated v2.0 release-gate issue has Owner approval. v2.0 is
the dual-IME-entrypoint stability line; do not relabel v2.1 librime work or the
optional TLS hardening branch as v2.0 stable evidence.

The active next-stage design is documented in
docs/NEXT_PHASE_V2_INPUT_FOUNDATION.md. Android engine, Windows TSF/Host, and
OTP work remain isolated PoCs until their respective gates pass; a compiled or
synthetic scaffold is not production integration evidence.

`v2 Daily Candidate` is the cross-milestone internal integration target. It is
not automatically a stable semantic version. Branch ownership, anti-drift
checks, merge order, evidence language, and the current execution sequence are
defined in docs/V2_DAILY_EXECUTION_CHARTER.md.

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
- Preserve the recorded v1.6.0 risk-exception result; do not retroactively mark
  its blocked manual checks as passed.
