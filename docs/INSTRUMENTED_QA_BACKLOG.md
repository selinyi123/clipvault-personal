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

## Current IME sprint acceptance (not yet executed)

The checks below extend the residual flows above for the current IME sprint.
Host-JVM tests cover deterministic decisions and source boundaries, while live
editor callbacks, IME lifecycle races, accessibility output, Room
instrumentation, and rendered candidate behaviour still require a connected
device or emulator. Nothing in this section is device evidence until the check
is executed against the exact recorded commit.

### Panel IME: latest request and input session wins

- [ ] Seed visibly distinct Recent, term, and prompt fixtures. Use an
  instrumentation-controlled delayed facade for the first two runtime reads,
  rapidly select Recent -> term -> prompt, and confirm that only the final
  prompt title and candidates render. Do not substitute timing sleeps; record
  this row as BLOCKED if the controlled facade is unavailable. No earlier
  request may replace the final tab.
- [ ] With an old candidate rendered and another load in flight, move focus
  ordinary -> password/incognito -> ordinary, both inside one test Activity and
  across two apps. Sensitive editors must show no personal candidates, the save
  action must remain disabled, and returning to an ordinary editor must render
  only the new input session.
- [ ] Switch away from or destroy/recreate the Panel IME while a candidate load
  or main-thread render callback is pending. An old rendered button must not
  commit text after invalidation; the recreated IME must load and commit only
  fresh candidates.

### Full Keyboard: editor actions and accessibility

- [ ] Focus fields advertising GO, SEARCH, SEND, NEXT, PREVIOUS, and DONE.
  Confirm that the action key shows the matching label and invokes the target
  editor action exactly once; NEXT/PREVIOUS must move focus as requested by the
  host editor.
- [ ] Exercise NONE, UNSPECIFIED, and `IME_FLAG_NO_ENTER_ACTION`, plus a test
  editor whose `performEditorAction` returns false. Confirm that the keyboard
  sends exactly one Enter/newline fallback and does not invoke a stale action
  from the previous input session.
- [ ] With TalkBack enabled, traverse the ClipVault candidate key, IME switch,
  Shift, symbol toggle, Delete, Space, and editor-action keys. Confirm their
  spoken labels and that Shift/symbol state changes announce enabled/disabled.
  Repeat at 200% font scale in portrait and landscape and confirm that special
  keys remain visible, distinguishable, and tappable.

### Memory candidates: real Room and rendered output

- [ ] Run `com.clipvault.app.data.MemoryCandidatePageTest` through
  `connectedDebugAndroidTest` and record a non-skipped pass. This is the
  device/emulator check for the real Room metadata window, hydration predicates,
  UTF-8 byte boundaries, supported kinds, and exclusion of deleted/invalid rows.
- [ ] Use synthetic fixtures inserted by instrumentation into an isolated test
  database: all six supported Memory kinds with distinct pinned/use-count
  values, plus oversized text/label, invalid or overlong kind, deleted, and
  current Secret Guard rows. Do not use real credentials or a user's database.
  Confirm that each Panel tab renders the expected deterministic eligible
  order, excluded rows never appear, and opening or rapidly switching tabs does
  not crash, hang, or expose rejected payloads.

### Existing automated evidence

- `:app:testDebugUnitTest` covers editor-action resolution and Enter fallback,
  request/session invalidation, stale candidate commit gates, Memory metadata
  and payload budgets, generated `[memory:<kind>]` query matching, and the rule
  that stored labels do not expand query semantics.
- `:app:compileDebugAndroidTestKotlin` only proves that
  `ResidualImeChecksTest` and `MemoryCandidatePageTest` compile.
- `:app:assembleDebug` proves packaging only. None of these commands enables an
  IME, interacts with an editor, runs TalkBack, or executes connected tests.

### Evidence boundary

Do not claim that `connectedDebugAndroidTest`, rapid-tab/focus transitions,
cross-app stale-button rejection, editor-action callbacks, TalkBack, large-font
rendering, or the final signed-APK physical-device lane has passed unless that
specific run is recorded. The five residual methods remain `@Ignore`-d until
they contain real assertions; a compiled scaffold is not Issue #36 manual QA
evidence.

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
- The current IME sprint acceptance checklist is executed against the exact
  recorded commit, with no clip contents or device serials included in evidence.
- Issue #36 still requires the final signed APK physical-device lane; an
  emulator or unsigned debug APK result does not close the release gate.
