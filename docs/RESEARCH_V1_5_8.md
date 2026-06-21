# ClipVault Personal v1.5.8 — Panel tab truncation fix

Date: 2026-06-21

Scope: fix the Panel IME candidate tab truncation risk introduced by the CandidateMixer migration, and align visible version metadata for this node.

## Research inputs

This round avoided repeating clipboard managers, keyboard privacy, Keystore, ULID, Python metadata, Android versioning, InputType suppression, Android local testing, and basic CI setup.

Faceted search systems use explicit dimensions to let users narrow result sets. For ClipVault Panel IME, tabs are facets over the unified candidate set: clip, memory term, phrase, prompt, and command.

The implementation implication is that a tab should not be starved by taking too small a global top-k set before applying the facet filter.

## Implementation status

Done:

- Panel IME now asks for 200 unified candidates before tab filtering.
- The 200 limit matches the current Runtime backing set: up to 100 clips and up to 100 memory rows.
- Panel tab filtering still happens by source and kind.
- Explicit save-clipboard behavior is unchanged.
- Desktop runtime version updated to 1.5.8.
- Desktop package metadata version updated to 1.5.8.
- Android app version updated to versionCode 10 / versionName 1.5.8.

Still open:

- CI run status is still unavailable through the current connector response.
- Manual IME verification is still required before closing v1.5.

## Validation commands

- desktop: cd desktop && python -m pytest -q
- android tests: cd android && ./gradlew :core:test :app:testDebugUnitTest --no-daemon
- android build: cd android && ./gradlew :app:assembleDebug --no-daemon

## Next node

v1.5.9:

- Read actual GitHub Actions results from the Actions UI or another endpoint that returns push-triggered runs.
- Fix the first concrete CI failure.
- Manually validate Full Keyboard and Panel IME behavior.
- If validation passes, close Issue 3 and open v1.6 planning.
