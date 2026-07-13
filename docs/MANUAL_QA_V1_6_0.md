# ClipVault Personal v1.6.0 Manual QA Checklist

Date: 2026-07-03

Scope: current manual validation gate for Issue #36 before publishing the
`v1.6.0` release artifacts.

## Automated coverage

These checks are automated and must be backed by local output or GitHub Actions
evidence for the target commit:

| Gate | Covered by | Expected result |
|---|---|---|
| Desktop tests | `cd desktop; python -m pytest -q` | all desktop tests pass |
| Android unit tests | `cd android; ./gradlew :core:test :app:testDebugUnitTest --no-daemon` | build succeeds |
| Android debug build | `cd android; ./gradlew :app:assembleDebug --no-daemon` | build succeeds |
| Release metadata alignment | `desktop/tests/test_release_alignment.py` | desktop, Android, and installer versions align at 1.6.0 |
| Android version floor | `desktop/tests/test_release_alignment.py` | `versionCode >= 13` |
| Panel IME helper presence | `desktop/tests/test_release_alignment.py` | `PanelCandidateTabs.kt` and its test exist |

## Release-state checks

- Desktop runtime version is 1.6.0.
- Desktop package metadata is 1.6.0.
- Android `versionName` is 1.6.0.
- Android `versionCode` is 13 or higher.
- Windows installer `AppVersion` is 1.6.0.
- GitHub Actions status is recorded before closing Issue #36.

## Artifact checks

These are required for a real `v1.6.0` release but are not satisfied by a debug
build alone:

- Follow [RELEASE_RUNBOOK_V1_6_0.md](RELEASE_RUNBOOK_V1_6_0.md) for the
  owner-controlled signed artifact workflow.
- Run the "Release candidate dry run" GitHub Actions workflow to verify that
  packaging still produces downloadable candidate artifacts, SHA256SUMS, and
  `RELEASE_MANIFEST.json` without using signing secrets or publishing a GitHub
  Release.
- Build desktop portable exe for 1.6.0.
- Build desktop installer for 1.6.0.
- Build signed Android APK for `versionName=1.6.0`.
- The manual "Release artifact build" GitHub Actions workflow can prepare the
  signed Android release APK when the Owner has configured
  `ANDROID_RELEASE_KEYSTORE_B64`, `ANDROID_RELEASE_KEYSTORE_PASSWORD`,
  `ANDROID_RELEASE_KEY_ALIAS`, and `ANDROID_RELEASE_KEY_PASSWORD` as `release`
  environment secrets. It is `workflow_dispatch` only, uses the `release`
  environment, and creates at most a draft GitHub Release unless the Owner
  completes the final publication step.
- Generate checksums for every release artifact.
- Verify the signed APK with `apksigner verify --print-certs`.
- After downloading the Owner-controlled release workflow artifacts, run
  `python tools/release_artifact_evidence.py` as described in
  [RELEASE_RUNBOOK_V1_6_0.md](RELEASE_RUNBOOK_V1_6_0.md) to validate the
  Windows and Android artifact directories before posting signed/final artifact
  evidence to Issue #36.
- If Owner approves publication, attach all artifacts and checksums to GitHub
  Release `v1.6.0`.

## Structured evidence helper

Use the local helper to prepare a complete Issue #36 manual-QA evidence comment:

```powershell
$qaEvidenceDir = ".\.field-test-artifacts\v1.6.0-manual-qa"
New-Item -ItemType Directory -Force -Path $qaEvidenceDir | Out-Null
python tools/manual_qa_evidence.py --write-template "$qaEvidenceDir\manual-qa-v1.6.0.json"
python tools/manual_qa_evidence.py --input "$qaEvidenceDir\manual-qa-v1.6.0.json" --no-fail
python tools/manual_qa_evidence.py --input "$qaEvidenceDir\manual-qa-v1.6.0.json" --output "$qaEvidenceDir\manual-qa-issue-comment.md"
```

The helper is a formatter and validator only. It does not run Android device QA,
inspect Windows clipboard behavior, post to GitHub, edit the Issue #36 checklist,
or close the release gate. It exits non-zero unless every required manual-QA item
is marked `pass` with evidence; use `--no-fail` only while drafting or recording
blocked or failing rows. `--write-template` and `--output` write UTF-8 files
directly, avoiding Windows PowerShell redirection encoding differences.
Both modes refuse to overwrite an existing file by default. Use `--force` only
after confirming the destination is a tool-managed disposable copy; the helper
always refuses symlink/directory outputs and never allows `--output` to replace
its own `--input` evidence JSON.

The generated report must include:

- `schema_version=2`, `version=v1.6.0`, and the full 40-character target commit.
- tester and timezone-qualified ISO-8601 timestamps.
- separate Android execution rows for API 26, API 27, and the physical device
  used with the final signed release APK. Each row records the SDK, build
  variant, source commit, app APK name/SHA-256, and (for compatibility runs)
  instrumentation APK name/SHA-256 without recording a device serial. The final
  signed run additionally requires the independently validated
  artifact-evidence reference.
- non-skipped API 26 and API 27 results for
  `CaptureTransactionTest#maxControlCharacterCaptureCanBeReadThroughBoundedOutboxChunks`,
  including a result reference and SHA-256.
- Windows desktop environment/build source and source commit matching the
  report target commit.
- Manual Android device QA rows.
- Manual IME privacy QA rows.
- Manual sync QA rows.
- Manual Windows clipboard privacy QA rows.

This manual QA report does not replace signed artifact evidence, final Windows
artifact evidence, signed Android APK evidence, release environment/secrets
evidence, or Owner-approved `v1.6.0` GitHub Release publication.
Its `PASS (OWNER-ATTESTED)` result means the required structure and reported
values are complete; the helper does not fetch or independently parse the
referenced SDK/JUnit files and cannot prove that physical observations occurred.

Older evidence without `schema_version=2` is intentionally blocked. Regenerate
the template instead of copying a v1 report forward: the older shape cannot
prove the API 26/27 compatibility regression ran rather than being compiled or
skipped.

## API 26 and API 27 CursorWindow compatibility QA

This is a compatibility-test lane, not final signed-APK manual QA. An API 26 or
API 27 emulator is acceptable for this targeted regression; the final signed
APK still requires the separate physical-device lane below. Use one connected
target at a time. First require a clean checkout whose HEAD equals the exact
target commit recorded in the evidence file:

```powershell
git fetch origin
git status --short
git rev-parse HEAD
git rev-parse origin/main
$qaEvidenceDir = ".\.field-test-artifacts\v1.6.0-manual-qa"
New-Item -ItemType Directory -Force -Path $qaEvidenceDir | Out-Null
```

Do not proceed if the status is non-empty or either commit differs from the
report target. Record the SDK before each run:

```powershell
$sdk = (adb shell getprop ro.build.version.sdk).Trim()
$sdk | Set-Content -NoNewline -Encoding ascii "$qaEvidenceDir\api-$sdk-sdk.txt"
Get-FileHash -Algorithm SHA256 "$qaEvidenceDir\api-$sdk-sdk.txt"
Push-Location android
.\gradlew.bat :app:connectedDebugAndroidTest --no-daemon "-Pandroid.testInstrumentationRunnerArguments.class=com.clipvault.app.capture.CaptureTransactionTest#maxControlCharacterCaptureCanBeReadThroughBoundedOutboxChunks"
Pop-Location
Get-FileHash -Algorithm SHA256 ".\android\app\build\outputs\apk\debug\app-debug.apk"
Get-FileHash -Algorithm SHA256 ".\android\app\build\outputs\apk\androidTest\debug\app-debug-androidTest.apk"
```

Connected-test XML is written below
`android/app/build/outputs/androidTest-results/connected/`. Locate the XML that
contains the exact test-case name, inspect it, then copy it to a distinct
redacted `api-26-...` or `api-27-...` file under `$qaEvidenceDir` and hash that
copy before running the other SDK; a later connected run may replace prior
output. `.field-test-artifacts/` is ignored by Git so retaining this local
evidence does not make the tracked checkout dirty.

Run once with an API 26 target and once with an API 27 target. For each run:

1. Confirm `$sdk` is exactly `26` or `27`. Save that numeric output
   as redacted SDK evidence and record a reference plus SHA-256; API 26 and API
   27 must use distinct evidence files/digests.
2. Filter the JUnit XML to the named test case and confirm that result reports
   `tests=1`, `failures=0`, `errors=0`, and `skipped=0`; do not copy aggregate
   counts from the full instrumentation suite.
   A green Gradle task with the test skipped is not evidence.
   Also record both generated APK digests: the app APK and Android
   instrumentation-test APK are independent inputs to the connected test and
   must not share a digest. The final signed APK digest must also differ from
   every debug app/test APK digest.
3. Record the numeric-only `CLIPVAULT_CURSORWINDOW_EVIDENCE` output. The
   `payload_bytes` value must exceed `4194304`; `wire_bytes` must be between
   `1` and `6356992`.
4. Hash the JUnit XML or an exported redacted result file with
   `Get-FileHash -Algorithm SHA256`. Record a repository/workflow URL or a
   short relative evidence label plus the digest. API 26 and API 27 must use
   distinct result references/digests. Do not record a device serial,
   clipboard payload, absolute/UNC local path, or `file://` URI.

The same redaction rule applies to every free-form `evidence` and `next_step`
value. The helper rejects common absolute local-path forms and HTML-escapes the
rendered table, but it cannot detect arbitrary clipboard text; review the
generated Markdown before posting it.

Within one SDK run, the SDK-output reference/digest and JUnit/result
reference/digest must identify different evidence; do not copy one digest into
multiple semantic rows.

The debug instrumentation APK and its results cannot substitute for the final
signed APK evidence or the physical-device manual checks.

## Manual Android device QA

Run on a real Android device with
`ClipVault-Android-v1.6.0-release-signed.apk` installed. Verify its SHA-256
matches the independently validated release artifact report, then reference
the corresponding `physical` / `release` `android_runs` entry from every
passing Android, IME, and sync row:

1. Pair Android with the desktop node using a one-time desktop pairing code.
2. Share text from another app into ClipVault and confirm it appears locally.
3. Use the Quick Settings tile to explicitly save current clipboard content.
4. Enable ClipVault Panel IME and confirm a candidate tap commits text.
5. Confirm Panel IME explicit save requires a user tap.
6. Confirm public clips and memory sync desktop <-> Android.
7. Confirm secret/private content remains isolated according to the current
   contracts.

## Manual IME privacy QA

1. Open a normal text field and confirm candidates can appear.
2. Move to password/incognito/no-suggestions fields.
3. Confirm candidates are hidden or replaced with the suppression message.
4. Confirm in-flight candidates are cleared on the transition into a sensitive
   field.
5. Confirm typed text is not written to Room, outbox, logs, sync payloads, or
   desktop storage.

## Manual Windows clipboard privacy QA

Run with a Windows source app or the repository probe that sets registered
clipboard privacy formats:

```powershell
python tools/clipboard_privacy_probe.py exclude-monitor
python tools/clipboard_privacy_probe.py viewer-ignore
python tools/clipboard_privacy_probe.py history-off
python tools/clipboard_privacy_probe.py cloud-off
python tools/clipboard_privacy_probe.py normal
```

The probe overwrites the current Windows clipboard with non-sensitive marker
text. It is a manual QA helper only; running it does not by itself satisfy the
Issue #36 gate unless the Owner also records the observed ClipVault result.

1. `ExcludeClipboardContentFromMonitorProcessing` prevents capture.
2. `Clipboard Viewer Ignore` prevents capture.
3. `CanIncludeInClipboardHistory=0` prevents capture.
4. `CanUploadToCloudClipboard=0` prevents capture.
5. A normal text clipboard item without those formats is still captured.

## Close criteria

Do not close Issue #36 unless all automated, artifact, and manual checks above
have recorded evidence. If a device, signing key, or owner approval is missing,
record that as blocked instead of marking the gate complete.

The release-candidate dry run is a packaging preflight only. Its manifest records
`signed=false` and `published=false`; its unsigned Android release APK and
uploaded workflow artifacts do not satisfy the signed release requirement.
