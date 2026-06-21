# ClipVault Personal v1.5.7 — Android and Panel convergence

Date: 2026-06-21

Scope: close two long-running v1.5 blockers: Android app version drift and Panel IME candidate-source divergence.

## Research inputs

This round avoided repeating clipboard managers, keyboard privacy, Keystore, ULID, Python metadata, CandidateMixer ranking, InputType suppression, Android local testing, and basic CI setup.

Android versioning documentation defines two separate app version fields: an internal monotonically increasing version code and a user-visible version name. This project therefore needs both values to move when Android builds are part of the release node.

GitHub Actions REST documentation lists workflow runs by repository, branch, event, status, and other filters. The current connector endpoint used in this project returns no push-triggered runs, so CI status still cannot be treated as verified from this chat session.

## Implementation status

Done:

- Android app version updated from versionCode 8 / versionName 1.5.0 to versionCode 9 / versionName 1.5.6.
- Panel IME tabs now consume runtime.listCandidates().
- Panel IME still filters tabs into recent clips and memory kinds.
- Explicit save-clipboard behavior was preserved.
- PrivacyAwareFilter suppression still guards Panel candidate rendering.

Still open:

- CI run status is still unavailable through the current connector response.
- Android and desktop version numbers are aligned at 1.5.6 for this node, while the node itself is recorded as v1.5.7 because it closes migration work after v1.5.6.

## Validation commands

Run from repository root:

- desktop: cd desktop && python -m pytest -q
- android tests: cd android && ./gradlew :core:test :app:testDebugUnitTest --no-daemon
- android build: cd android && ./gradlew :app:assembleDebug --no-daemon

## Next node

v1.5.8:

- Read actual GitHub Actions results from the Actions UI or an endpoint that returns push-triggered runs.
- Fix the first concrete CI failure.
- If CI is green, close Issue 3 and open v1.6 planning.

v1.6:

- source toggles
- candidate source caps
- transient query-aware filtering without storing typed text
