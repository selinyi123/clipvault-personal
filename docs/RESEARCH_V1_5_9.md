# ClipVault Personal v1.5.9 — Validation gap

Date: 2026-06-21

Scope: review the v1.5.8 Panel tab truncation fix and identify the next validation gap.

## Research inputs

This round avoided previously covered areas and focused on Android test strategy. Recent research on Android instrumentation testing reports that emulator-based checks in CI are useful but often costly and fragile. For this project, host JVM tests remain the better first layer for pure logic, with manual device checks for input-method behavior.

## Review result

v1.5.8 fixed the Panel tab truncation risk by requesting a larger unified candidate pool before tab filtering. The next desired improvement is a regression test that prevents the pool size from being lowered accidentally.

Attempted but blocked in this session:

- Extract Panel tab filtering into a pure helper.
- Add a host JVM unit test for Panel tab filtering.
- Add a lightweight regression test for the Panel candidate pool invariant.

No source behavior was changed in this node.

## Current state

Closed:

- CandidateMixer architecture and tests.
- PrivacyAwareFilter tests.
- Full Keyboard and Panel IME both use CandidateMixer.
- Panel tab truncation risk fixed in source.
- Desktop and Android version metadata aligned at 1.5.8.

Still open:

- CI run status remains unavailable through the current connector response.
- Manual IME verification is still required.
- Panel tab filtering testability remains a follow-up item.

## Next node

v1.5.10:

- Use a narrower code path to add Panel tab filtering testability.
- Validate through CI or manual device tests.
- Close Issue 3 only after CI and manual IME validation are confirmed.
