# Instrumented QA backlog (residual device-only checks)

The v1.5.16 manual QA gate (`docs/MANUAL_QA_V1_5_16.md`) was automated as far as
the host JVM allows. Three checks remain that exercise live IME behaviour and
on-screen rendering — they cannot run on the host JVM and need an instrumented
(`androidTest`) run on a device or emulator.

This is the planned task to implement them. Until it is picked up, the checks are
encoded as `@Ignore`-d scaffolds in
`android/app/src/androidTest/kotlin/com/clipvault/app/ime/ResidualImeChecksTest.kt`.

Per `docs/AGENT_WORKFLOWS.md`, emulator CI is not added unless explicitly
planned — this file is that plan; wiring it into CI is a separate decision.

## Residual checks

| Test method | Manual QA item | Intent |
|---|---|---|
| `fullKeyboard_stripVisible_and_tapCommitsText` | Full Keyboard #1–2 | strip renders; tapping a candidate commits text |
| `panelIme_switch_and_tapCommitsText` | Panel IME #1–2, #5 | IME switch works; tapping a candidate commits text |
| `panelIme_explicitSave_requiresUserTap` | Panel IME #8 | no implicit capture; save needs an explicit tap |

## Wiring needed to implement

1. Dependencies (`android/app/build.gradle.kts`):
   ```kotlin
   defaultConfig { testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner" }
   // dependencies {
   androidTestImplementation("androidx.test.ext:junit:1.2.1")
   androidTestImplementation("androidx.test:runner:1.6.2")
   androidTestImplementation("androidx.test.uiautomator:uiautomator:2.3.0")
   androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
   ```
2. IME enablement: an IME cannot self-enable. Enable + select the ClipVault
   keyboards before the interaction, e.g. via `adb shell ime enable/set` in a
   test-orchestration step, or `UiAutomator` driving the system input-method
   picker. Restore the previous IME in teardown.
3. Drive the checks with `UiAutomator` (cross-app: IME surface + target field)
   and assert committed text by reading the focused field's contents.
4. Seed fixtures: ensure ≥1 recent clip and ≥1 memory item of each kind exist
   (insert via the Room DB or the capture path) so candidate tabs are populated.

## Running locally (once wired)

```bash
cd android
./gradlew :app:connectedDebugAndroidTest   # requires a connected device/emulator
```

## Definition of done

- The three `@Ignore` annotations are removed and the assertions are real.
- `connectedDebugAndroidTest` passes on a device/emulator.
- `docs/MANUAL_QA_V1_5_16.md` residual section is updated to point at the now-live
  instrumented tests instead of this backlog.
