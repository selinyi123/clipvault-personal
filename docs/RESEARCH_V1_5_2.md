# ClipVault Personal v1.5.2 — PrivacyAwareFilter tests

Date: 2026-06-21

Scope:

> close the v1.5 privacy-test gap by making PrivacyAwareFilter directly testable.

## 1. Research inputs

This round avoided repeating Keystore, ULID, generic clipboard managers, and CandidateMixer ranking research.

### 1.1 Android InputType is the right boundary for candidate suppression

References:

- Android Developers `InputType`
- `TYPE_TEXT_FLAG_NO_SUGGESTIONS`
- sensitive text variations
- sensitive numeric variations

Takeaway:

- Some input fields explicitly tell IMEs that suggestions are not needed.
- Sensitive text and numeric fields are represented as input type bit patterns.
- ClipVault should hide saved candidates before any UI rendering in those fields.

Decision:

- Keep the EditorInfo overload for real IME usage.
- Add an internal `shouldSuppressCandidates(inputType: Int)` pure function for unit tests.

### 1.2 Plain JUnit is enough for this layer

Reference:

- Robolectric getting started guide

Takeaway:

- Robolectric is useful when tests need Android resources or framework behavior.
- This filter only needs integer bit masks, so a plain JUnit test is lower-friction.

Decision:

- Do not add Robolectric in v1.5.2.
- Keep the tested logic as a pure function.

## 2. Implementation decisions

### D-026 Pure inputType filter

`PrivacyAwareFilter` now has two methods:

- `shouldSuppressCandidates(info: EditorInfo?)`
- `internal shouldSuppressCandidates(inputType: Int)`

The IME still calls the `EditorInfo` version. Tests call the pure `Int` version.

### D-027 PrivacyAwareFilter unit tests

Added `PrivacyAwareFilterTest` covering:

- no-suggestions text fields
- sensitive text fields
- sensitive numeric fields
- ordinary text fields
- ordinary numeric fields

## 3. Status

Done:

- PrivacyAwareFilter pure function added.
- PrivacyAwareFilter JUnit tests added.
- Desktop version bumped to `1.5.2`.

Still partial:

- Android app version remains `1.5.0` / versionCode 8 until a narrow version-only patch can be applied.
- Panel IME still needs migration from separate facade calls to `runtime.listCandidates()`.

Needs validation:

- `./gradlew :app:testDebugUnitTest`
- `./gradlew :app:assembleDebug`
- `python -m pytest -q`

## 4. Next node

### v1.5.3

- Apply Android version-only patch.
- Migrate Panel IME to CandidateMixer without changing save-clipboard behavior.
- Add one Full Keyboard candidate-display smoke test if feasible.

### v1.6

- Per-source toggles for clip/memory/prompt/command.
- Candidate source caps.
- Query-aware filtering using composing text only, without persistence.
