# ClipVault Personal v1.5.1 — CandidateMixer testability patch

Date: 2026-06-21

Scope:

> stabilize v1.5 CandidateMixer by adding direct unit-test coverage and documenting remaining migration risk.

## 1. Research inputs

This round avoided repeating prior Keystore, ULID, clipboard manager, and basic IME privacy research.

### 1.1 Federated/on-device keyboard intelligence is still risky

References:

- Wired: How Google's Android Keyboard Keeps Smart Replies Private
- arXiv: Two Models are Better than One: Federated Learning Is Not Private For Google GBoard Next Word Prediction
- arXiv: Private Federated Learning in Gboard

Takeaway:

- On-device processing is better than cloud processing for keyboard intelligence.
- Federated learning is not automatically private; typed text can still leak through model updates or attacks if the system is designed badly.
- ClipVault should not enter typed-text learning yet.

Decision:

- Keep CandidateMixer deterministic and local.
- Do not train from ordinary typed text.
- Do not send candidate telemetry.

### 1.2 Android third-party SDK privacy risk supports dependency restraint

Reference:

- arXiv: A Large-Scale Privacy Assessment of Android Third-Party SDKs

Takeaway:

- Adding SDKs for analytics, ranking, or personalization can create privacy and compliance surface area.

Decision:

- v1.5.1 adds only JUnit for local tests.
- No analytics SDK, ML SDK, or network ranking library.

### 1.3 Keyboard UX should preserve explicit control

References:

- HeliBoard / OpenBoard
- FlorisBoard
- AnySoftKeyboard

Takeaway:

- Open-source keyboards are useful design references for thin UI and explicit controls.
- Licensing and privacy constraints mean they should remain references, not copied code.

Decision:

- Keep UI frontends thin.
- Move candidate logic into Runtime.
- Continue keeping Panel migration separate from clipboard-save behavior.

## 2. Implementation decisions

### D-023 CandidateMixer testability

`CandidateMixer` is now `internal` instead of `private`.

This does not make it part of the public Runtime facade, but allows tests in the Android app module to directly cover ordering and filtering behavior.

### D-024 CandidateMixer unit tests

Added `CandidateMixerTest` with coverage for:

- memory candidate beats raw clip by default
- pinned/favorite/high-use clip can beat ordinary memory
- query filtering and prefix boost
- stable ordering when scores tie

### D-025 Android version bump blocked

Desktop version was bumped to `1.5.1`.

Android version bump from `1.5.0` to `1.5.1` was attempted but blocked by repository write safety checks when rewriting `android/app/build.gradle.kts`, likely because the file includes existing release-signing password property names. The signing configuration was not changed.

## 3. Current status

Done:

- CandidateMixer exposed as `internal` for tests.
- JUnit app test dependency added.
- CandidateMixer deterministic ordering test added.
- Desktop version bumped to `1.5.1`.
- v1.5 issue updated with the remaining Panel/Android-version/build tasks.

Partial:

- Android versionName/versionCode remain at `1.5.0` / `8` until a safe Gradle patch can be applied manually or with a narrower diff-capable tool.
- Panel IME still uses the v1.4 facade calls, with PrivacyAwareFilter and labels intact.

Needs validation:

- `./gradlew :app:testDebugUnitTest`
- `./gradlew :app:assembleDebug`
- `python -m pytest -q`
- Manual Full Keyboard Lab candidate display.
- Manual Panel IME save-clipboard regression.

## 4. Next node

### v1.5.2

- Safely patch Android version to `1.5.1` or `1.5.2` without rewriting release-signing comments/properties.
- Finish Panel IME migration to `runtime.listCandidates()` without deleting save-clipboard.
- Add PrivacyAwareFilter unit tests with Robolectric or instrumented tests.

### v1.6

- Per-source candidate toggles.
- Source caps for prompts/commands.
- Query-aware filtering using composing text only, without persistence.
