# ClipVault Personal Agent Workflows

This document defines the task flow for future coding-agent sessions.

## Workflow 1: Release blocker closure

Use this workflow until Issue 3 is closed.

1. Read Issue 3.
2. Pick exactly one open blocker.
3. Inspect the smallest related file set.
4. Make the smallest patch possible.
5. Run or request the relevant validation command.
6. Update Issue 3 with the actual result.
7. Stop. Do not start unrelated work.

Allowed blockers:

- Panel IME service wiring to PanelCandidateTabs.
- Desktop version metadata alignment.
- Android version metadata alignment.
- CI result discovery.
- Manual QA completion.

## Workflow 2: Research before architecture

Use this before any v1.6 design change.

1. Search for prior art.
2. Record only findings that affect ClipVault's current goal.
3. Avoid repeating previous topics unless a new fact changes the decision.
4. Convert findings into a small design decision.
5. Do not implement the design until v1.5 blockers are closed.

## Workflow 3: Code review

Use this for repository review tasks.

1. Start from docs/ARCHITECTURE.md and docs/CONTRACTS.md.
2. Inspect code that implements the contract.
3. Look for privacy boundary violations first.
4. Look for version drift and test gaps second.
5. Report exact file paths and line ranges.
6. Patch only one defect class at a time.

## Workflow 4: Test and QA

Use this before closing a release node.

1. Run desktop tests.
2. Run Android unit tests.
3. Build Android debug APK.
4. Complete docs/MANUAL_QA_V1_5_12.md.
5. Record pass, fail, or unavailable in Issue 3.
6. Close Issue 3 only when every required check is confirmed.

## Workflow 5: Operating loop for recurring sessions

Use this when a user asks to continue without naming a specific blocker.

1. Read AGENTS.md.
2. Read this file.
3. Read Issue 3.
4. Select the first unfinished blocker in this order:
   - CI result discovery.
   - service wiring.
   - version metadata alignment.
   - manual QA evidence.
5. Attempt exactly one narrow improvement.
6. If the patch is blocked, record the blocker and stop.
7. If the patch lands, fetch the changed file and cite it in the final report.
8. Do not create v1.6 work until Issue 3 is closed.

## Agent roles

Research agent:

- Finds prior art and risk signals.
- Produces concise design implications.
- Does not modify runtime code.

Patch agent:

- Makes small source changes.
- Avoids broad refactors.
- Keeps privacy boundaries intact.

Test agent:

- Adds or improves deterministic tests.
- Prefers host JVM tests for pure candidate logic.
- Does not add emulator CI unless explicitly planned.

Release agent:

- Aligns visible version metadata.
- Reads CI or records why CI is unavailable.
- Updates Issue 3 with evidence.

QA agent:

- Executes the manual checklist.
- Records exact pass or fail status.
- Does not close the release issue alone.

## Current stop rule

Stop any workflow if it would require adding typed-text collection, analytics SDKs, network calls inside the IME, or implicit saving of user content.
