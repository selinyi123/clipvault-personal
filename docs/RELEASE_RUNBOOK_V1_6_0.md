# ClipVault Personal v1.6.0 Release Runbook

This runbook turns the Issue #36 release gate into an executable owner checklist.
It does not replace the manual Android/IME/sync/Windows clipboard QA evidence.

## Safety boundary

- Do not publish `v1.6.0` until Issue #36 has all required evidence.
- Do not commit keystores, passwords, generated `.b64` files, or release artifacts.
- Run the workflow first with `create_draft_release=false`.
- Treat `Release candidate dry run` artifacts as unsigned packaging evidence only.
- The release-candidate dry run runs automatically on pushes to `main`; manual
  dispatch remains a fallback if the current-main run is missing, failed, or
  still queued.
- Run `Release artifact build` only with `--ref main`; the workflow fails closed
  if manually dispatched from any other branch or tag.
- The signed artifact pre-publication run should appear in GitHub Actions as
  `Release artifacts v1.6.0 from main draft=false`; the optional draft-release
  pass should appear as `Release artifacts v1.6.0 from main draft=true`.

## 0. Run the read-only readiness report

Start with the repository readiness checker so the current main SHA, automated
workflow evidence, release-environment setup, signed-artifact workflow status,
GitHub Release state, and Issue #36 checklist state are evaluated together:

```powershell
python tools/release_readiness.py --no-fail
python tools/release_readiness.py --json --no-fail
```

The checker is read-only. It must not trigger workflows, set secrets, create or
publish a GitHub Release, upload artifacts, complete manual QA, or close Issue
#36. Treat every `blocked` row as the next real evidence gap to close. The
Issue #36 row also lists the unchecked release-gate checklist items so the Owner
can record evidence against the exact remaining rows instead of re-counting the
issue body manually.

## 1. Confirm current main evidence

```powershell
$mainSha = gh api repos/selinyi123/clipvault-personal/branches/main --jq ".commit.sha"

gh run list `
  --repo selinyi123/clipvault-personal `
  --workflow "CI" `
  --branch main `
  --limit 10 `
  --json databaseId,status,conclusion,headSha,url,event

gh run list `
  --repo selinyi123/clipvault-personal `
  --workflow "Release candidate dry run" `
  --branch main `
  --limit 10 `
  --json databaseId,status,conclusion,headSha,url,event
```

Pick the latest completed successful run for each workflow whose `headSha`
equals `$mainSha`. If no release-candidate dry-run exists for the current main
commit after the automatic main-push run has had time to complete, run it before
continuing:

```powershell
gh workflow run "Release candidate dry run" `
  --repo selinyi123/clipvault-personal `
  --ref main
```

Inspect the selected runs and record their URLs on Issue #36:

```powershell
gh run view CI_RUN_ID --repo selinyi123/clipvault-personal
gh run view RELEASE_CANDIDATE_DRY_RUN_ID --repo selinyi123/clipvault-personal
```

Both runs must target the same current main commit.

## 2. Configure the protected release environment

Create a GitHub environment named `release` and add the desired approval policy.
The workflow uses this environment for Android signing and optional draft release
creation. Store the Android signing values as `release` environment secrets, not
repository-level secrets, so protected-environment approval gates secret access.

Do not weaken the workflow by adding push or pull-request triggers.

## 3. Configure Android signing environment secrets

Required `release` environment secrets:

```text
ANDROID_RELEASE_KEYSTORE_B64
ANDROID_RELEASE_KEYSTORE_PASSWORD
ANDROID_RELEASE_KEY_ALIAS
ANDROID_RELEASE_KEY_PASSWORD
```

Example CLI setup for the keystore value:

```powershell
try {
  [Convert]::ToBase64String([IO.File]::ReadAllBytes("clipvault-release.jks")) |
    Set-Content -Encoding ascii keystore.b64
  gh secret set ANDROID_RELEASE_KEYSTORE_B64 `
    --repo selinyi123/clipvault-personal `
    --env release < keystore.b64
} finally {
  Remove-Item keystore.b64 -ErrorAction SilentlyContinue
}
```

Set the password/alias secrets without echoing them into logs:

```powershell
gh secret set ANDROID_RELEASE_KEYSTORE_PASSWORD `
  --repo selinyi123/clipvault-personal `
  --env release
gh secret set ANDROID_RELEASE_KEY_ALIAS `
  --repo selinyi123/clipvault-personal `
  --env release
gh secret set ANDROID_RELEASE_KEY_PASSWORD `
  --repo selinyi123/clipvault-personal `
  --env release
```

## 4. Run the signed artifact workflow without creating a release

The signed release workflow must run from the current `main` ref. It also checks
that the requested `version` matches the source-tree desktop and Android version
metadata before building artifacts.

```powershell
gh workflow run "Release artifact build" `
  --repo selinyi123/clipvault-personal `
  --ref main `
  -f version=v1.6.0 `
  -f create_draft_release=false
```

The run must complete these jobs successfully:

- Windows release artifacts
- Android signed release APK

The run title must match the requested release inputs:

- `Release artifacts v1.6.0 from main draft=false` for the first signed-artifact
  evidence run.
- `Release artifacts v1.6.0 from main draft=true` only for the later
  Owner-approved draft-release pass.

The Android artifact must include:

- `ClipVault-Android-v1.6.0-release-signed.apk`
- `ANDROID_APKSIGNER_VERIFY.txt`
- `SHA256SUMS.txt`
- `RELEASE_MANIFEST.json` with `kind=release` and `signed=true`

Download both artifact bundles from the completed run and validate the
downloaded bytes before recording the signed/final artifact evidence on Issue
#36. This is intentionally separate from workflow success: a green workflow run
does not by itself prove the artifact contents that will be attached or cited;
a green workflow run does not by itself prove the artifact contents.

```powershell
$runId = "RELEASE_ARTIFACT_RUN_ID"
$mainSha = gh api repos/selinyi123/clipvault-personal/branches/main --jq ".commit.sha"
Remove-Item release-evidence-v1.6.0 -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path release-evidence-v1.6.0 | Out-Null

gh run download $runId `
  --repo selinyi123/clipvault-personal `
  --name clipvault-windows-release-artifacts `
  --name clipvault-android-signed-release-artifacts `
  --dir release-evidence-v1.6.0

python tools/release_artifact_evidence.py `
  --windows-dir release-evidence-v1.6.0/clipvault-windows-release-artifacts `
  --android-dir release-evidence-v1.6.0/clipvault-android-signed-release-artifacts `
  --version v1.6.0 `
  --commit $mainSha `
  --run-url "https://github.com/selinyi123/clipvault-personal/actions/runs/$runId" `
  --output release-artifact-issue-comment.md
```

Post the rendered comment only after the helper passes against the downloaded
artifact directories. The helper validates `RELEASE_MANIFEST.json`,
`SHA256SUMS.txt`, required release artifact names, and Android
`ANDROID_APKSIGNER_VERIFY.txt` shape. It does not download artifacts, call
GitHub, post comments, sign APKs, complete manual QA, publish a Release, or
close Issue #36.

Because the release workflow emits GitHub artifact attestations, optionally
verify provenance for the primary binaries before publication, for example:

```powershell
gh attestation verify `
  release-evidence-v1.6.0/clipvault-windows-release-artifacts/ClipVault-Desktop-v1.6.0-portable.exe `
  --repo selinyi123/clipvault-personal
gh attestation verify `
  release-evidence-v1.6.0/clipvault-android-signed-release-artifacts/ClipVault-Android-v1.6.0-release-signed.apk `
  --repo selinyi123/clipvault-personal
```

## 5. Record evidence on Issue #36

Comment on Issue #36 with:

- workflow run URL
- Android signed artifact name
- `apksigner verify --print-certs` evidence file name
- confirmation that `RELEASE_MANIFEST.json` records `signed=true`
- the rendered `tools/release_artifact_evidence.py` report for the downloaded
  Windows and Android release artifact directories

Do not close Issue #36 until manual device QA is also recorded.

## 6. Record manual QA evidence on Issue #36

After running the real Android device, IME privacy, sync, and Windows clipboard
privacy checks from `docs/MANUAL_QA_V1_6_0.md`, use the local helper to validate
and render the manual-QA evidence comment:

```powershell
$qaEvidenceDir = ".\.field-test-artifacts\v1.6.0-manual-qa"
New-Item -ItemType Directory -Force -Path $qaEvidenceDir | Out-Null
python tools/manual_qa_evidence.py --write-template "$qaEvidenceDir\manual-qa-v1.6.0.json"
python tools/manual_qa_evidence.py --input "$qaEvidenceDir\manual-qa-v1.6.0.json" --no-fail
python tools/manual_qa_evidence.py --input "$qaEvidenceDir\manual-qa-v1.6.0.json" --output "$qaEvidenceDir\manual-qa-issue-comment.md"
```

These file-writing modes refuse to replace existing files unless `--force` is
explicitly provided; symlinks, directories, and input/output self-overwrite are
always rejected. Preserve the filled JSON and render to a separate path.

Post the rendered comment only after the Owner has filled the JSON with real
observations. The helper is local-only: it does not call GitHub, run device QA,
sign artifacts, publish releases, edit checklist rows, or close Issue #36. A
passing manual-QA report still does not replace signed artifact evidence, final
Windows artifact evidence, release environment/secrets evidence, or final
Owner-approved GitHub Release publication.

`PASS (OWNER-ATTESTED)` means the JSON is structurally complete; the helper does
not fetch or independently parse referenced SDK/JUnit evidence and does not
prove that the reported physical observations occurred.

The evidence file must use `schema_version=2` and bind all observations to the
exact target commit, including the Windows environment source commit. Before
final device QA, run the targeted CursorWindow
regression once on API 26 and once on API 27 (an emulator is acceptable for
this compatibility lane). Require `git status --short` to be empty and require
`git rev-parse HEAD` and `git rev-parse origin/main` to equal the report target
commit before building either test APK:

```powershell
git fetch origin
git status --short
git rev-parse HEAD
git rev-parse origin/main
$qaEvidenceDir = ".\.field-test-artifacts\v1.6.0-manual-qa"
New-Item -ItemType Directory -Force -Path $qaEvidenceDir | Out-Null
$sdk = (adb shell getprop ro.build.version.sdk).Trim()
$sdk | Set-Content -NoNewline -Encoding ascii "$qaEvidenceDir\api-$sdk-sdk.txt"
Get-FileHash -Algorithm SHA256 "$qaEvidenceDir\api-$sdk-sdk.txt"
Push-Location android
.\gradlew.bat :app:connectedDebugAndroidTest --no-daemon "-Pandroid.testInstrumentationRunnerArguments.class=com.clipvault.app.capture.CaptureTransactionTest#maxControlCharacterCaptureCanBeReadThroughBoundedOutboxChunks"
Pop-Location
Get-FileHash -Algorithm SHA256 ".\android\app\build\outputs\apk\debug\app-debug.apk"
Get-FileHash -Algorithm SHA256 ".\android\app\build\outputs\apk\androidTest\debug\app-debug-androidTest.apk"
```

Before switching targets, locate the exact-test XML below
`android/app/build/outputs/androidTest-results/connected/`, inspect it, copy it
to a distinct redacted API-specific file under `$qaEvidenceDir`, and hash the
copy. Do not assume the next connected run will preserve the previous result
directory. `.field-test-artifacts/` is ignored by Git, so these retained local
files do not invalidate the clean tracked checkout check.

For each SDK, retain a redacted JUnit result filtered to the named test case
with `tests=1`, `failures=0`, `errors=0`, and `skipped=0` (not aggregate suite
counts), plus its SHA-256 and the numeric
`CLIPVAULT_CURSORWINDOW_EVIDENCE` values. Separately retain the numeric SDK
command output with its own reference and SHA-256. API 26 and API 27 references
and digests must be distinct. A compiled test, a skipped test, or a green run
on any SDK other than 26/27 does not satisfy this gate. Record the hashes of
both `app-debug.apk` and `app-debug-androidTest.apk`; the instrumentation result
is not bound to the target code unless both APK inputs are identified. Their
digests must differ, and the final signed APK digest must differ from all debug
app/test APK digests. SDK-output and JUnit/result evidence within a run must
also use different references and digests.

Separately install `ClipVault-Android-v1.6.0-release-signed.apk` on a physical
device, verify its SHA-256 against the validated release artifact report, and
bind that run to the exact target commit and artifact-evidence reference. Use
that physical release run ID for every passing Android, IME, and sync row.
Debug instrumentation evidence never substitutes for signed-release manual QA.
Do not record device serials, clipboard payloads, secrets, or full private
local paths in the JSON or rendered Issue comment. Source/reference fields
reject absolute Windows/POSIX paths, UNC paths, and `file://` URIs; use public
URLs, workflow/run references, or short relative evidence labels. Free-form
evidence is also checked for common absolute path forms, but still requires
Owner redaction review before posting.

## 7. Optional draft GitHub Release

Only after Owner approval, rerun the workflow with:

```powershell
gh workflow run "Release artifact build" `
  --repo selinyi123/clipvault-personal `
  --ref main `
  -f version=v1.6.0 `
  -f create_draft_release=true
```

This creates a draft release only. Review assets and Issue #36 evidence before
publishing the draft.
