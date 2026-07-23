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

The local helper can create and preview a report while evidence is being filled.
The commands below are drafting conveniences only; they do not perform the
frozen-source checks required for final Issue #36 evidence:

```powershell
$qaEvidenceDir = ".\.field-test-artifacts\v1.6.0-manual-qa"
$finalDraftEvidence = ".\.field-test-artifacts\v1.6.0-draft-run-REPLACE_WITH_DRAFT_TRUE_RUN_ID\final-draft-artifact-evidence.json"
New-Item -ItemType Directory -Force -Path $qaEvidenceDir | Out-Null
python tools/manual_qa_evidence.py --write-template "$qaEvidenceDir\manual-qa-v1.6.0.json"
python tools/manual_qa_evidence.py --input "$qaEvidenceDir\manual-qa-v1.6.0.json" --final-draft-artifact-evidence "$finalDraftEvidence" --require-final-draft-binding --no-fail
```

For the final eligible render, copy the completed values into the generated
Owner pack template and run **Step F** in
`.field-test-artifacts/v1.6.0-owner-pack/OWNER_RELEASE_ACTION_PACK.md`. Step F pins trusted Python, the clean
frozen commit, all dynamically loaded source blobs, and both evidence inputs;
it renders to a pending file and promotes that file only after the post-checks
succeed. A comment produced by a bare helper invocation is not final
Issue #36 release-gate evidence.

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
The two strict flags must be used together. They recompute the canonical binding
of the exact `draft=true` artifact report and require the manual report's
`release_artifact_binding`, final signed run, draft Release identity, and signed
APK name/SHA-256 to match it. The artifact report is still a local snapshot;
rerun the live verifier before publication.
The Step F rendered Issue comment must show
`final_draft_binding_assurance=verified_external_snapshot`; legacy compatibility
output without that assurance is not final Issue #36 release-gate evidence.

The generated report must include:

- `schema_version=4`, `version=v1.6.0`, and the full 40-character target commit.
- `release_artifact_binding` copied from the strict final-draft artifact report,
  including its binding SHA-256, exact workflow run/attempt, draft Release ID and
  snapshot URL, and signed APK name/SHA-256. The numeric Release ID is part of the
  canonical digest; the URL is a separately validated snapshot reference and is
  rechecked against live GitHub state before publication. Use a path-free short
  evidence reference.
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
- four executed, non-skipped, passing `OutboxBaseSeqTest` cases on API 26 and
  API 27, recorded under `re_pair_outbox_high_water.instrumented_results` with
  distinct result references and SHA-256 values.
- Windows desktop environment/build source and source commit matching the
  report target commit.
- Manual Android device QA rows.
- Seven distinct Android signing-reset migration rows. Each row requires its
  own path-free evidence and must reference the final physical signed run.
- Manual IME privacy QA rows.
- Manual sync QA rows.
- Manual Windows clipboard privacy QA rows.

This manual QA report does not replace signed artifact evidence, final Windows
artifact evidence, signed Android APK evidence, release environment/secrets
evidence, or Owner-approved `v1.6.0` GitHub Release publication.
Its strict-mode `PASS (OWNER-ATTESTED)` result means the required structure and reported
values are complete; the helper does not fetch or independently parse the
referenced SDK/JUnit files and cannot prove that physical observations occurred.
Legacy schema-v2 and schema-v3 compatibility modes may be structurally valid
(`ok=true`) but always remain `BLOCKED`, even when all rows for those frozen
formats pass and a binding object is present. Regenerate schema v4 and execute
the required re-pair outbox high-water row plus all seven Android signing-reset
migration rows. Other schema versions are intentionally blocked. Historical
success exit status only reports structural completeness; never interpret it
as Issue #36 release eligibility.

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
$connectedResults = ".\android\app\build\outputs\androidTest-results\connected"
if (Test-Path -LiteralPath $connectedResults) {
  $existingResults = Get-Item -LiteralPath $connectedResults -Force -ErrorAction Stop
  if (($existingResults.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Refusing reparse-point connected-test results"
  }
  Rename-Item -LiteralPath $connectedResults -NewName ("connected.previous-" + [Guid]::NewGuid().ToString("N")) -ErrorAction Stop
}
Push-Location android
try {
  .\gradlew.bat :app:connectedDebugAndroidTest --no-daemon "-Pandroid.testInstrumentationRunnerArguments.class=com.clipvault.app.capture.CaptureTransactionTest#maxControlCharacterCaptureCanBeReadThroughBoundedOutboxChunks"
  if ($LASTEXITCODE -ne 0) { throw "CursorWindow filtered instrumentation failed" }
} finally {
  Pop-Location
}
if (-not (Test-Path -LiteralPath $connectedResults -PathType Container)) {
  throw "CursorWindow run did not create fresh connected-test results"
}
$freshResults = Get-Item -LiteralPath $connectedResults -Force -ErrorAction Stop
if (($freshResults.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
  throw "Fresh connected-test results must not be a reparse point"
}
$appDebugApk = ".\android\app\build\outputs\apk\debug\app-debug.apk"
$testDebugApk = ".\android\app\build\outputs\apk\androidTest\debug\app-debug-androidTest.apk"
$cursorAppApkSha256 = (Get-FileHash -LiteralPath $appDebugApk -Algorithm SHA256).Hash.ToLowerInvariant()
$cursorTestApkSha256 = (Get-FileHash -LiteralPath $testDebugApk -Algorithm SHA256).Hash.ToLowerInvariant()
$cursorAppApkSha256
$cursorTestApkSha256
```

Connected-test XML is written below
`android/app/build/outputs/androidTest-results/connected/`. Locate the XML that
contains the exact CursorWindow test-case name, confirm the single test passed,
and immediately copy it to a redacted `api-$sdk-cursorwindow.xml` under
`$qaEvidenceDir`. Hash that copy before starting another connected run because
Gradle may replace the result directory.

Then run the outbox suite as a separate filtered invocation:

```powershell
if (Test-Path -LiteralPath $connectedResults) {
  $existingResults = Get-Item -LiteralPath $connectedResults -Force -ErrorAction Stop
  if (($existingResults.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Refusing reparse-point connected-test results"
  }
  Rename-Item -LiteralPath $connectedResults -NewName ("connected.previous-" + [Guid]::NewGuid().ToString("N")) -ErrorAction Stop
}
Push-Location android
try {
  .\gradlew.bat :app:connectedDebugAndroidTest --no-daemon "-Pandroid.testInstrumentationRunnerArguments.class=com.clipvault.app.data.OutboxBaseSeqTest"
  if ($LASTEXITCODE -ne 0) { throw "Outbox baseline filtered instrumentation failed" }
} finally {
  Pop-Location
}
if (-not (Test-Path -LiteralPath $connectedResults -PathType Container)) {
  throw "Outbox baseline run did not create fresh connected-test results"
}
$freshResults = Get-Item -LiteralPath $connectedResults -Force -ErrorAction Stop
if (($freshResults.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
  throw "Fresh connected-test results must not be a reparse point"
}
$outboxAppApkSha256 = (Get-FileHash -LiteralPath $appDebugApk -Algorithm SHA256).Hash.ToLowerInvariant()
$outboxTestApkSha256 = (Get-FileHash -LiteralPath $testDebugApk -Algorithm SHA256).Hash.ToLowerInvariant()
if ($outboxAppApkSha256 -cne $cursorAppApkSha256 -or
    $outboxTestApkSha256 -cne $cursorTestApkSha256) {
  throw "Debug app or instrumentation APK changed between filtered test runs; discard both results and rerun"
}
```

Locate the XML for `com.clipvault.app.data.OutboxBaseSeqTest`, confirm exactly
all four baseline cases ran without skip/failure, and immediately copy it to a
distinct redacted `api-$sdk-outbox-base.xml` under `$qaEvidenceDir`. Hash that
copy before changing devices. Never point both evidence rows at one aggregate
XML or duplicate the same file: CursorWindow and outbox-baseline JUnit evidence
must be independently filtered snapshots with different references and hashes.
The post-outbox digest comparison must pass so both results are bound to the
same debug app and instrumentation APK bytes; otherwise discard both snapshots.
The pre-run rename and post-run directory check prevent a failed Gradle command
from reusing an older PASS XML; keep both commands in the same PowerShell
session so the saved paths and APK digests remain bound.
`.field-test-artifacts/` is ignored by Git so retaining this local evidence does
not make the tracked checkout dirty.

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

1. Pair the fresh final signed Android installation using a one-time Desktop
   code.
2. Share text from another app into ClipVault and confirm it appears locally.
3. Use the Quick Settings tile to explicitly save current clipboard content.
4. Enable ClipVault Panel IME and confirm a candidate tap commits text.
5. Confirm Panel IME explicit save requires a user tap.
6. Confirm public clips sync Desktop -> Android and Android -> Desktop. Confirm
   public Memory syncs Desktop -> Android; Android -> Desktop Memory sync is not
   part of the current contract.
7. Confirm secret/private content remains isolated under the current contracts.
8. Create and fully sync several explicit Android saves so the local outbox is
   empty but its sequence high-water mark is greater than zero. Unpair that
   device on Desktop, generate a new one-time code, and pair the same Android
   installation again. Make one more explicit save and confirm Desktop receives
   it, Android clears the acknowledged outbox row, and later saves continue in
   order. Then disconnect networking, create at least one pending explicit save,
   re-pair, reconnect, and confirm that pending row and a later row both arrive
   in order. Repeat once after clearing/rebuilding the Desktop database if that
   is part of the final Windows upgrade scenario. Record safe test labels,
   Desktop receive/ACK observations, and whether the Android pending indicator
   cleared; do not require release-APK internal database access and never record
   clip content.

## Android signing-reset migration QA (schema v4)

The signing-reset migration is deliberately separate from ordinary pairing and
sync QA. Record each gate as its own item under
`sections.android_signing_reset_qa.items`; do not combine several observations
into the pairing or sync rows. Every passing item needs non-placeholder,
path-free evidence and `run_ids` containing the exact
`final_signed_android_run_id` for the physical final signed APK run.

1. `dual_backup_verified`: independently restore or open both encrypted
   new-key backups and confirm they identify the Owner-approved replacement
   certificate fingerprint. Do not publish the keystore, passwords, or private
   recovery locations.
2. `old_outbox_barrier_drained`: pause new captures on v1.5.10, send a benign
   public marker, confirm Desktop has acknowledged through that barrier, and
   confirm Android cleared the pending row before uninstalling the old app. A
   marker visible on Desktop without a cleared Android outbox is not a PASS.
3. `quarantine_decision`: confirm the v1.5.10 quarantine is empty. If it is not
   exportable, record the Owner's explicit acceptance that those quarantined
   items will be permanently lost; never copy their contents into evidence.
4. `zero_peer_reseed`: stop or revoke the old Android peer, confirm Desktop has
   zero peers, and atomically replace retained Desktop outbound history with the
   documented current public-state reseed plus content-free marker. Account for
   every skipped category; unaccepted invalid/unsafe public rows block release.
5. `update_incompatible`: attempt to install the exact final signed APK over
   v1.5.10 and record that Android rejects it specifically with
   `INSTALL_FAILED_UPDATE_INCOMPATIBLE`.
6. `fresh_install`: uninstall v1.5.10, fresh-install the exact final signed APK,
   then re-pair and re-enable the Share target, Quick Settings tile, and Panel
   IME as applicable.
7. `reseed_delivery_verified`: run the safe reseed delivery verification and
   confirm exactly one post-reseed peer acknowledged through the unchanged
   reseed high-water. Keep all Desktop outbox writers frozen from apply through
   this verification.

Schema v2 and v3 inputs remain readable for historical review, but the helper
must report `release_ready=false` for them. Only a complete schema-v4 report can
be eligible for the final Issue #36 release gate.

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
text. Its default marker is case-specific, UTC-timestamped, and deliberately
split into low-entropy tokens so Secret Guard leaves a successful control in
the public list instead of quarantining it. The UTC timestamp plus a short
random nonce also prevents an earlier content-hash duplicate from being
mistaken for the current run.
It is a manual QA helper only; running it does not by itself satisfy the Issue
#36 gate unless the Owner also records the observed ClipVault result. For every
case, check both the public list and quarantine; wait for at least two watcher
polls before moving to the next probe.
Run these cases with an isolated QA configuration/database. First exit the
ordinary ClipVault Desktop instance and confirm that no `clipvault.exe` watcher
or API listener remains. Before starting the final portable artifact, create a
separate QA config whose absolute database, log, and Obsidian Vault paths all
stay under one disposable QA directory, whose backup is disabled, whose server
uses a separate loopback port, and whose fresh database has no paired device.
Start only that artifact with its explicit `--config` path and confirm it is the
sole clipboard watcher for the run. A successful normal control is a real
public clip and would otherwise be eligible for persistence, sync, Obsidian,
and private-repository backup. Stop the QA instance before restarting the
ordinary instance; preserve only the redacted evidence, then discard the
isolated QA data after the release decision.

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
