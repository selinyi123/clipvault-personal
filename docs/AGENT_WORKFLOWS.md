# ClipVault Personal Agent Workflows

This document defines the task flow for future coding-agent sessions.

## Release blocker closure

Use this workflow until Issue 3 is closed.

1. Read Issue 3.
2. Pick exactly one open blocker.
3. Inspect the smallest related file set.
4. Make the smallest patch possible.
5. Run or request the relevant validation command.
6. Update Issue 3 with the actual result.
7. Stop. Do not start unrelated work.

Active blockers:

- CI result discovery.
- Manual QA completion via docs/MANUAL_QA_V1_5_16.md.

Resolved closure items:

- Panel IME service is wired to PanelCandidateTabs.
- Desktop runtime and package metadata are aligned.
- Android versionName is aligned and versionCode has advanced.

## Recurring session loop

1. Read this file.
2. Read Issue 3.
3. Select the first unfinished blocker in this order:
   - CI result discovery.
   - manual QA evidence.
4. Attempt exactly one narrow improvement.
5. If the patch lands, fetch the changed file and cite it.
6. If the patch is blocked, record the blocker and stop.
7. Do not create v1.6 work until Issue 3 is closed.

## Agent roles

### Research agent

- Finds prior art and risk signals.
- Produces concise design implications.
- Does not modify runtime code.

### Patch agent

- Makes small source changes.
- Avoids broad refactors.
- Keeps privacy boundaries intact.

### Test agent

- Adds or improves deterministic tests.
- Prefers host JVM tests for pure candidate logic.
- Does not add emulator CI unless explicitly planned.

### Release agent

- Aligns visible version metadata.
- Reads CI or records why CI is unavailable.
- Updates Issue 3 with evidence.

### QA agent

- Executes the manual checklist.
- Records exact pass or fail status.
- Does not close the release issue alone.

### Privacy gate agent

- Blocks typed-text collection.
- Blocks network calls inside the IME.
- Blocks analytics SDKs inside keyboard components.
- Blocks implicit saving of user content.
- Requires sensitive fields to suppress candidates.

## Evidence requirements

A task is not complete unless at least one of these is true:

- changed file is fetched after commit and cited.
- GitHub issue is updated and cited.
- CI status is fetched and cited.
- manual QA result is recorded in Issue 3.
- blocker is explicitly recorded in Issue 3.

## v1.5 release gate

Issue 3 can close only when:

- desktop tests pass.
- Android unit tests pass.
- Android debug build passes.
- Full Keyboard manual checks pass.
- Panel IME manual checks pass.
- visible version metadata is aligned.
- no v1.5 blocker remains open.

## v1.6 entry gate

Do not start v1.6 until Issue 3 is closed.

Candidate v1.6 tracks after closure:

- candidate source caps and tab weighting.
- source toggles in keyboard UI.
- query-aware transient candidate filtering.
- improved release-state display.
- safer version metadata single-source strategy.

Typed text learning, behavioral profiling, cloud keyboard intelligence, and analytics remain out of scope unless a separate privacy design is approved first.
