# ClipVault Personal v1.4 — Candidate safety shell

Date: 2026-06-21

Scope:

> Android IME candidate safety before deeper CandidateMixer/Rime integration.

This is a focused iteration. It does not add Chinese input, model ranking, cloud sync, or keylogging-style learning.

## 1. Research inputs

### 1.1 Android EditorInfo / InputType is the correct first guard

Sources:

- https://developer.android.com/reference/android/view/inputmethod/EditorInfo
- https://developer.android.com/reference/android/text/InputType

Findings:

- `EditorInfo.inputType` exposes the target text box content type to the IME.
- `TYPE_TEXT_VARIATION_PASSWORD` and `TYPE_TEXT_VARIATION_WEB_PASSWORD` mark password-like text fields.
- `TYPE_NUMBER_VARIATION_PASSWORD` marks numeric password fields.
- `TYPE_TEXT_FLAG_NO_SUGGESTIONS` means the editor says the IME does not need to show suggestion candidates.

Decision:

- Add a local `PrivacyAwareFilter` before any ClipVault candidate panel is rendered.
- Suppress ClipVault candidates in password-like fields and fields marked no-suggestions.
- Typed keys still work; only ClipVault recall candidates are hidden.

### 1.2 Direct commit is still the preferred paste path

Source:

- https://developer.android.com/reference/android/view/inputmethod/InputConnection#commitText(java.lang.CharSequence,%20int)

Decision:

- Continue using `InputConnection.commitText()` for one-tap paste.
- Do not route candidate paste through the global clipboard.

### 1.3 Privacy keyboard references support conservative defaults

Sources:

- https://github.com/Helium314/HeliBoard
- https://github.com/florisboard/florisboard
- https://github.com/openboard-team/openboard

Findings:

- Privacy keyboards generally expose clear controls for learning/incognito behavior.
- GPL keyboards can be useful UX references but should not be copied into this repository without a licensing decision.

Decision:

- Use the projects as architecture/UX references only.
- Keep ClipVault's keyboard frontends thin and runtime-driven.

### 1.4 Stack Overflow / Q&A snippets are not an implementation base for security code

Source:

- https://arxiv.org/abs/1710.03135

Decision:

- Security-sensitive Android code should be based on official API docs and direct reasoning.
- Do not copy Stack Overflow snippets for IME/privacy/crypto paths.

## 2. Implementation decisions

### D-016 PrivacyAwareFilter

Add `android/app/src/main/kotlin/com/clipvault/app/ime/PrivacyAwareFilter.kt`.

Suppression conditions:

- `TYPE_TEXT_FLAG_NO_SUGGESTIONS`
- `TYPE_TEXT_VARIATION_PASSWORD`
- `TYPE_TEXT_VARIATION_VISIBLE_PASSWORD`
- `TYPE_TEXT_VARIATION_WEB_PASSWORD`
- `TYPE_NUMBER_VARIATION_PASSWORD`

### D-017 Candidate provenance labels

Panel and full-keyboard candidate labels now expose source type explicitly:

- `[clip:<content_type>] ...`
- `[memory:<kind>] ...`

This is a lightweight prerequisite for a future CandidateMixer: the user must know whether a candidate came from recent clipboard history or Personal Memory.

### D-018 Candidate-only suppression

Suppressing ClipVault candidates does not disable the keyboard itself. Plain key entry remains functional in the Full Keyboard Lab. The Panel IME can still switch back and explicitly save clipboard content.

## 3. v1.4 status

Done:

- PrivacyAwareFilter added.
- Panel IME hides recent clips and memory panels in sensitive/no-suggestion fields.
- Full Keyboard Lab hides recent clip strip in sensitive/no-suggestion fields.
- Candidate source labels added.
- Desktop version bumped to 1.4.0.
- Android version bumped to 1.4.0 / versionCode 7.

Still needs validation:

- `python -m pytest -q`
- `./gradlew :core:test`
- `./gradlew :app:assembleDebug`
- Manual test in: normal note field, password field, numeric PIN field, web password field, no-suggestions field.

## 4. Next node: v1.4.1 validation / v1.5 CandidateMixer

### v1.4.1

- Add Android unit tests for `PrivacyAwareFilter.shouldSuppressCandidates()`.
- Run Gradle build and fix compile issues if any.
- Validate behavior on a real device.

### v1.5

- Introduce `Candidate` model with source, text, label, score, and risk flags.
- Keep ranker deterministic.
- Add per-source toggles: recent clips, memory phrases, prompts, commands.
- Do not persist normal typed text.

### v2.1

- librime JNI proof of concept.
- fcitx5-android fallback evaluation.
- Licensing decision before importing any GPL keyboard code.
