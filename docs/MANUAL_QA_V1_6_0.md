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
python tools/manual_qa_evidence.py --write-template manual-qa-v1.6.0.json
python tools/manual_qa_evidence.py --input manual-qa-v1.6.0.json --no-fail
python tools/manual_qa_evidence.py --input manual-qa-v1.6.0.json --output manual-qa-issue-comment.md
```

The helper is a formatter and validator only. It does not run Android device QA,
inspect Windows clipboard behavior, post to GitHub, edit the Issue #36 checklist,
or close the release gate. It exits non-zero unless every required manual-QA item
is marked `pass` with evidence; use `--no-fail` only while drafting or recording
blocked or failing rows. `--write-template` and `--output` write UTF-8 files
directly, avoiding Windows PowerShell redirection encoding differences.

The generated report must include:

- `version=v1.6.0` and the full 40-character target commit.
- tester and timestamp.
- Android device/app/APK source.
- Windows desktop environment/build source.
- Manual Android device QA rows.
- Manual IME privacy QA rows.
- Manual sync QA rows.
- Manual Windows clipboard privacy QA rows.

This manual QA report does not replace signed artifact evidence, final Windows
artifact evidence, signed Android APK evidence, release environment/secrets
evidence, or Owner-approved `v1.6.0` GitHub Release publication.

## Manual Android device QA

Run on a real Android device with the `v1.6.0` APK installed:

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
