# Instrumented QA backlog (residual device-only checks)

The historical IME manual QA residuals were automated as far as the host JVM
allows, then carried forward into the current Issue #36 / v1.6.0 manual QA
gate (`docs/MANUAL_QA_V1_6_0.md`). Five checks still exercise live IME
behaviour and on-screen rendering; they cannot run on the host JVM and need an
instrumented (`androidTest`) run on a device or emulator.

Until a device/emulator cycle is picked up, the checks are encoded as
`@Ignore`-d scaffolds in
`android/app/src/androidTest/kotlin/com/clipvault/app/ime/ResidualImeChecksTest.kt`.

CI now compiles the `androidTest` source set with AndroidX Test dependencies so
the residual QA scaffolds cannot drift out of buildability. It still does not run
`connectedDebugAndroidTest`, does not enable/select the IME on a device, and does
not satisfy the Owner/manual QA gate for Issue #36.

## Residual checks

| Test method | Manual QA item | Intent |
|---|---|---|
| `fullKeyboard_stripVisible_and_tapCommitsText` | Full Keyboard #1-2 | strip renders; tapping a candidate commits text |
| `panelIme_switch_and_tapCommitsText` | Panel IME #1-3, #5 | IME switch works; tapping a candidate commits text |
| `panelIme_explicitSave_requiresUserTap` | Panel IME #8 | no implicit capture; save needs an explicit tap |
| `sensitiveEditor_clearsRenderedAndInFlightCandidates` | FK #3-4, Panel #6-7 | normal-to-sensitive transition clears old/stale candidates |
| `sensitiveEditor_blocksExplicitClipboardSave` | Panel sensitive-save gate | save disabled; Room/outbox unchanged |

## Wiring status and remaining device work

1. Dependencies (`android/app/build.gradle.kts`): wired for compile-only CI.

   ```kotlin
   defaultConfig { testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner" }
   // dependencies {
   androidTestImplementation("androidx.test.ext:junit:1.2.1")
   androidTestImplementation("androidx.test:runner:1.6.2")
   androidTestImplementation("androidx.test.uiautomator:uiautomator:2.3.0")
   androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
   ```

2. CI compile gate: `.github/workflows/ci.yml` runs
   `./gradlew :app:compileDebugAndroidTestKotlin --no-daemon` after Android unit
   tests and before assembling the debug APK.
3. IME enablement: an IME cannot self-enable. Enable + select the ClipVault
   keyboards before the interaction, e.g. via `adb shell ime enable/set` in a
   test-orchestration step, or `UiAutomator` driving the system input-method
   picker. Restore the previous IME in teardown.
4. Drive the checks with `UiAutomator` (cross-app: IME surface + target field)
   and assert committed text by reading the focused field's contents.
5. Seed fixtures: ensure at least one recent clip and one memory item of each
   kind exist (insert via the Room DB or the capture path) so candidate tabs are
   populated.
6. Add a test Activity with ordinary and password/incognito editors. Keep the
   same IME View alive while switching focus; delay the facade response in the
   stale-result case so the transition occurs before its main-thread callback.
7. Snapshot Room/outbox counts before the sensitive-save check and assert they
   remain unchanged after attempting the disabled action.

## Running locally

Compile the residual test source set without a device:

```bash
cd android
./gradlew :app:compileDebugAndroidTestKotlin --no-daemon
```

Run the real checks only after replacing the scaffolds with assertions and
connecting a device/emulator:

```bash
cd android
./gradlew :app:connectedDebugAndroidTest   # requires a connected device/emulator
```

## Definition of done

- The five `@Ignore` annotations are removed and the assertions are real.
- `connectedDebugAndroidTest` passes on a device/emulator.
- `docs/MANUAL_QA_V1_6_0.md` and the Issue #36 evidence comment are updated to
  point at the now-live instrumented tests instead of this backlog.
