# ClipVault Personal v1.5.14 — Agent workflow status

Date: 2026-06-22

Scope: bind the repository agent instructions to the remaining v1.5 release blockers.

## Existing workflow files

- AGENTS.md exists and defines repository-level guardrails.
- docs/AGENT_WORKFLOWS.md exists and defines repeatable task flows.

## Current agent roles

Research agent:

- search prior art before design expansion.
- keep findings tied to ClipVault's current goal.

Patch agent:

- make one narrow source change at a time.
- avoid broad refactors while v1.5 remains open.

Test agent:

- prefer deterministic host tests for pure candidate logic.
- avoid adding heavy UI automation before release closure.

Release agent:

- align visible version metadata.
- record CI status with evidence.

QA agent:

- execute docs/MANUAL_QA_V1_5_12.md.
- record exact pass or fail status.

## Remaining v1.5 blockers

1. Panel IME service should use the existing PanelCandidateTabs helper.
2. Desktop runtime and package metadata should match.
3. Android app metadata should match the release node.
4. CI status should be known.
5. Manual QA should be completed.

## Operating rule

Until Issue 3 is closed, every future session should pick exactly one blocker, attempt one narrow improvement, update Issue 3, and stop. v1.6 planning starts only after the blocker list is closed.
