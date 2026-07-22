# ClipVault Personal v1.6.0 Release Runbook

This runbook turns the Issue #36 release gate into an executable owner checklist.
It does not replace the manual Android/IME/sync/Windows clipboard QA evidence.

## Safety boundary

- Do not publish `v1.6.0` until Issue #36 has all required evidence.
- Do not commit keystores, passwords, generated `.b64` files, or release artifacts.
- Android signing continuity with v1.5.10 is not available. Follow
  [ANDROID_SIGNING_RESET_V1_6_0.md](ANDROID_SIGNING_RESET_V1_6_0.md) and do not
  describe the v1.6.0 Android APK as an in-place update.
- Run the workflow first with `create_draft_release=false` as a signed,
  no-draft preflight; never use its bytes as final QA/publication evidence.
- Treat `Release candidate dry run` artifacts as unsigned packaging evidence only.
- The release-candidate dry run runs automatically on pushes to `main`; manual
  dispatch remains a fallback if the current-main run is missing, failed, or
  still queued.
- Run `Release artifact build` only with `--ref main`; the workflow fails closed
  if manually dispatched from any other branch or tag.
- The signed no-draft preflight should appear as
  `Release artifacts v1.6.0 from main draft=false`. The final asset build must
  appear as `Release artifacts v1.6.0 from main draft=true`; download its draft
  Release assets before final QA and publish that same draft without rebuilding.

## Approved Android signing reset (compatibility break)

The Owner approved the signing reset on 2026-07-22: retain the application ID
`com.clipvault.app`, accept that v1.5.10 must be uninstalled before v1.6.0 can
be installed, and complete Android data migration first. The v1.5.10 APK has
one verified v2 signer whose certificate SHA-256 is
`898f21c2b59a4a4729fd386d91a86711b81ea567d5d85bf391a2e0fff2f1f9f1`, but
the corresponding private key is unavailable. There is therefore **no
cryptographic signing continuity** from v1.5.10 to v1.6.0.

This approval authorizes the reset procedure; it is not manual-QA, artifact, or
publication evidence. Before deleting the old installation, drain and verify
public clips on Desktop, verify Desktop-authoritative public Memory, revoke the
old peer, and run the documented one-shot Desktop reseed with zero peers. The
old app has no supported export path for quarantined secret/private content:
confirm quarantine is empty or explicitly accept permanent loss; otherwise
stop.
Then use the exact final draft APK to record the expected
`adb install -r` signature-mismatch failure, uninstall the old package, install
v1.6.0 fresh, pair again, and rerun Android, IME privacy, QS Tile, and sync QA.
The dedicated signing-reset document defines the required ordering, evidence,
new-key custody, and release-note wording.

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

The release workflow must also enforce the non-secret environment variable
`ANDROID_RELEASE_CERT_SHA256`, containing the 64-hex certificate SHA-256 that
the Owner independently confirmed from the long-lived release key. The workflow
requires 64 lowercase hex characters, verifies exactly one signer with
`apksigner verify --verbose -Werr --print-certs`, and compares that signer with
the Owner value before attestation or upload. A generic
valid APK signature or an unbound `apksigner` text file is not Owner identity
proof.

For v1.6.0, this variable must contain the fingerprint of the **new** long-lived
release certificate created under the approved reset:
`86bdcbca45f0e9bce4c7cfbb3bc52f85f34a482acff8220af11dc659a2ec567c`.
It must not contain the old v1.5.10 fingerprint above or any other valid-looking
fingerprint. Before dispatching any workflow that can access the secrets,
complete and verify two encrypted keystore backups in independent Owner-controlled storage
locations as required by
[ANDROID_SIGNING_RESET_V1_6_0.md](ANDROID_SIGNING_RESET_V1_6_0.md). Record only
the public certificate fingerprint, alias, and backup verification dates in
release evidence; never record passwords, keystore bytes, or private storage
paths.

Example CLI setup for the keystore value:

```powershell
[Convert]::ToBase64String(
  [IO.File]::ReadAllBytes("clipvault-release.jks")
) | gh secret set ANDROID_RELEASE_KEYSTORE_B64 `
  --repo selinyi123/clipvault-personal `
  --env release
if ($LASTEXITCODE -ne 0) { throw "Failed to set the release keystore secret" }
```

Do not create a plaintext `.b64` staging file. The pipeline keeps the encoded
value in memory and sends it directly to the GitHub CLI standard input.

Set the password/alias secrets without echoing them into logs:

```powershell
gh secret set ANDROID_RELEASE_KEYSTORE_PASSWORD `
  --repo selinyi123/clipvault-personal `
  --env release
if ($LASTEXITCODE -ne 0) { throw "Failed to set the keystore password secret" }
gh secret set ANDROID_RELEASE_KEY_ALIAS `
  --repo selinyi123/clipvault-personal `
  --env release
if ($LASTEXITCODE -ne 0) { throw "Failed to set the key alias secret" }
gh secret set ANDROID_RELEASE_KEY_PASSWORD `
  --repo selinyi123/clipvault-personal `
  --env release
if ($LASTEXITCODE -ne 0) { throw "Failed to set the key password secret" }
```

## 4. Run the signed no-draft preflight

Do not dispatch this workflow until the new signer fingerprint has been
independently verified from both retained keystore copies, all four environment
secret names are present, and `ANDROID_RELEASE_CERT_SHA256` contains that new
fingerprint.

The workflow must run from current `main`, and its target must equal the SHA
selected in section 1:

```powershell
gh workflow run "Release artifact build" `
  --repo selinyi123/clipvault-personal `
  --ref main `
  -f version=v1.6.0 `
  -f create_draft_release=false
if ($LASTEXITCODE -ne 0) { throw "failed to dispatch signed preflight" }
```

Require both `Windows release artifacts` and `Android signed release APK` to
pass, and require the title
`Release artifacts v1.6.0 from main draft=false`. This run exercises the signing
and packaging path, but it creates no final draft. Its bytes are not eligible
for final Android, IME, sync, Windows, publication, or Issue #36 evidence.

## 5. Build, download, and verify the final draft asset set

After the signed preflight and signer-identity gate pass, dispatch the one run
that creates the final draft assets:

```powershell
gh workflow run "Release artifact build" `
  --repo selinyi123/clipvault-personal `
  --ref main `
  -f version=v1.6.0 `
  -f create_draft_release=true
if ($LASTEXITCODE -ne 0) { throw "failed to dispatch final draft build" }
```

The selected run must be a successful `workflow_dispatch` on `main`, have
`headSha == $mainSha`, and have the exact title
`Release artifacts v1.6.0 from main draft=true`.

Generate the ignored Owner pack and follow its Step E commands exactly:

```powershell
python tools/prepare_v1_6_release_owner_pack.py
if ($LASTEXITCODE -ne 0) { throw "Owner pack generation failed" }
Get-Content .field-test-artifacts/v1.6.0-owner-pack/OWNER_RELEASE_ACTION_PACK.md
```

Those commands deliberately:

- use a fresh `.field-test-artifacts/v1.6.0-draft-run-<run-id>` directory and
  refuse stale output;
- fail after every unsuccessful `gh` or Python command;
- validate the run title, event, branch, head SHA, and conclusion;
- require a draft, non-prerelease `v1.6.0` Release targeting `$mainSha`;
- require the exact eight-asset inventory with no empty asset;
- download both Actions artifacts and the current mutable draft Release assets;
- compare all eight files byte-for-byte by SHA-256;
- save the draft Release digest set for the pre-publication recheck.

Execute `tools/release_artifact_evidence.py --require-live-final-draft` only
through the generated Owner pack's Step E. It requires an exact clean target
checkout, absolute trusted Git, GitHub CLI, and Python executables outside the
repository, the Android SDK `lib/apksigner.jar`, an absolute trusted `java.exe`,
and all three downloaded directories. Run from the exact repository root; the
pack rejects subdirectory execution. Batch launchers, any tool path traversing a
reparse point, UNC/device namespace paths, non-fixed drives, and workspace-local
executables are rejected. The Git/GitHub CLI
environment is sanitized, and each executed repository validator is checked
before and after use against its exact target-commit blob so index flags or
ignored bytecode cannot hide a modified verifier. Strict mode fails unless it verifies:

- current `main` and the exact successful `draft=true` run ID/attempt, workflow,
  branch, source commit, event, and display title;
- the exact two non-expired Actions bundles and their archive digests;
- the exact eight regular, non-empty files in both Actions and draft Release
  inventories, including Release API size/digest parity;
- `gh attestation verify` for every Actions file with fixed repository, exact
  workflow/ref certificate identity, OIDC issuer, `refs/heads/main`, exact
  source/signer commit, hosted-runner, and SLSA predicate constraints;
- at least one cryptographic certificate per file whose `runInvocationURI`
  matches the exact run attempt;
- a fresh independent `apksigner verify --verbose -Werr --print-certs` result
  for the downloaded draft APK matching both captured evidence and the Owner
  certificate trust anchor;
- the live `release` environment `ANDROID_RELEASE_CERT_SHA256` variable matches
  that independently supplied Owner trust anchor at both ends of collection; and
- unchanged current-main, run, draft Release metadata, and local bytes at the
  end of collection; and
- the exact `refs/tags/v1.6.0` is either absent or resolves through any annotated
  tags to the target commit, with unchanged tag state at the end of collection.

The helper writes a machine JSON snapshot plus a path-free Issue comment with a
canonical artifact binding SHA-256. The JSON is not self-authenticating and its
status field must never be trusted by itself: readiness must rerun the live
checks or independently cross-check the binding, current GitHub state, and the
exact Release ID. The helper never treats workflow-controlled
attestation predicate metadata as the run trust root. A green workflow alone
still does not prove artifact contents, and this evidence does not replace
manual QA, Owner publication approval, final publication, or Issue #36 closure.

The GitHub release-by-tag REST endpoint returns published Releases only. Strict
mode therefore uses the authenticated release listing, which exposes drafts
only to users with push access, and requires exactly one matching `v1.6.0`
draft. GitHub's Actions and Release REST schemas expose canonical `sha256:`
digests; absence or malformed values fails closed.

All final device and Windows QA in the next section must use files downloaded
from the draft Release directory, not bytes from the `draft=false` preflight.

## 6. Record manual QA evidence on Issue #36

After running the real Android device, IME privacy, sync, and Windows clipboard
privacy checks from `docs/MANUAL_QA_V1_6_0.md`, the local helper may be used for
a drafting preview. The commands below are not the final eligible render:

The physical Android lane must also follow the ordered signing-reset migration
in [ANDROID_SIGNING_RESET_V1_6_0.md](ANDROID_SIGNING_RESET_V1_6_0.md). In
particular, retain redacted evidence that public clips drained before uninstall,
Desktop-authoritative Memory was included in the zero-peer reseed, the old
quarantine was empty or its permanent loss was explicitly accepted, and
`adb install -r` of the
new final-draft APK over an installed v1.5.10 failed with
`INSTALL_FAILED_UPDATE_INCOMPATIBLE`, the fresh install succeeded only after
the old package was removed, and the device was paired and configured again.
The expected update rejection proves the compatibility break; it is not a
successful installation or final QA result.

```powershell
$qaEvidenceDir = ".\.field-test-artifacts\v1.6.0-manual-qa"
$finalDraftEvidence = ".\.field-test-artifacts\v1.6.0-draft-run-REPLACE_WITH_DRAFT_TRUE_RUN_ID\final-draft-artifact-evidence.json"
New-Item -ItemType Directory -Force -Path $qaEvidenceDir | Out-Null
python tools/manual_qa_evidence.py --write-template "$qaEvidenceDir\manual-qa-v1.6.0.json"
python tools/manual_qa_evidence.py --input "$qaEvidenceDir\manual-qa-v1.6.0.json" --final-draft-artifact-evidence "$finalDraftEvidence" --require-final-draft-binding --no-fail
```

Render the final comment only through **Step F** of the generated
`.field-test-artifacts/v1.6.0-owner-pack/OWNER_RELEASE_ACTION_PACK.md`. That step pins the trusted interpreter,
clean frozen commit, dynamically loaded source blobs, and both evidence input
digests, then promotes a pending output only after all post-checks succeed. A
bare helper render is not final Issue #36 release-gate evidence.

These file-writing modes refuse to replace existing files unless `--force` is
explicitly provided; symlinks, directories, and input/output self-overwrite are
always rejected. Preserve the filled JSON and render to a separate path.
Strict mode recomputes the supplied final-draft artifact snapshot and fails unless
the manual report names the same canonical binding, run attempt, numeric draft
Release ID, snapshot URL, and signed APK. The URL is cross-checked but is not a
canonical-binding input; live revalidation remains required before publication.
Only a rendered report with
`final_draft_binding_assurance=verified_external_snapshot` is eligible for the
final Issue #36 evidence review.

Post the rendered comment only after the Owner has filled the JSON with real
observations. The helper is local-only: it does not call GitHub, run device QA,
sign artifacts, publish releases, edit checklist rows, or close Issue #36. A
passing manual-QA report still does not replace signed artifact evidence, final
Windows artifact evidence, release environment/secrets evidence, or final
Owner-approved GitHub Release publication.

Strict-mode `PASS (OWNER-ATTESTED)` means the JSON is structurally complete; the helper does
not fetch or independently parse referenced SDK/JUnit evidence and does not
prove that the reported physical observations occurred.
Legacy schema-v2 compatibility mode can return `ok=true`, but it remains
`BLOCKED` even if a binding object is supplied: it lacks the required re-pair
outbox high-water row. Its historical success exit status means structural
completeness only, not Issue #36 release eligibility.

The evidence file must use `schema_version=4` and bind all observations to the
exact target commit, including the Windows environment source commit. Before
final device QA, run the targeted CursorWindow regression and the Android
outbox-baseline Room regression once on API 26 and once on API 27 (an emulator
is acceptable for this compatibility lane). Require `git status --short` to be
empty and require
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

Before another connected run, locate the exact CursorWindow result below
`android/app/build/outputs/androidTest-results/connected/`, inspect it, copy it
to a redacted `api-$sdk-cursorwindow.xml` under `$qaEvidenceDir`, and hash the
copy. Then run the outbox suite separately and snapshot its result immediately:

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

Copy the result containing exactly all four `OutboxBaseSeqTest` cases to a
distinct redacted `api-$sdk-outbox-base.xml` and hash it before switching
targets. It must report no failure, error, or skip. Do not reuse one aggregate
XML for both evidence rows and do not assume a later connected run preserves
the previous result directory. The digest comparison above must pass so both
JUnit results identify the same debug APK inputs; otherwise discard both result
snapshots. `.field-test-artifacts/` is ignored by Git, so
these retained local files do not invalidate the clean tracked checkout check.
Keep the two invocations in one PowerShell session. Renaming the prior result
directory, checking Gradle's exit code, and requiring a newly created result
directory prevent a failed invocation from reusing stale PASS XML.

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

Separately install the copy of
`ClipVault-Android-v1.6.0-release-signed.apk` downloaded from the draft Release
directory on a physical device. Verify its SHA-256 against the saved draft
digest set and bind that run to the exact target commit, draft URL, draft=true
run, and artifact-evidence reference. Use that physical release run ID for
every passing Android, IME, and sync row.
Debug instrumentation evidence never substitutes for signed-release manual QA.
Run the Windows installer/portable and clipboard privacy cases with the EXEs
downloaded from that same draft Release. The schema-v4 manual helper does not
independently cross-check Windows bytes, so each Windows evidence row must cite
the draft URL plus the exact EXE name/SHA-256 from the saved digest set; this is
still an Owner-attested observation.
Do not record device serials, clipboard payloads, secrets, or full private
local paths in the JSON or rendered Issue comment. Source/reference fields
reject absolute Windows/POSIX paths, UNC paths, and `file://` URIs; use public
URLs, workflow/run references, or short relative evidence labels. Free-form
evidence is also checked for common absolute path forms, but still requires
Owner redaction review before posting.

## 7. Recheck and publish the existing draft

Before approval, record on Issue #36:

- the exact target SHA, current-main CI, and RC dry-run URLs;
- the final `draft=true` run and draft Release URLs;
- the exact eight-asset names, sizes, and SHA-256 digest set;
- constrained provenance and Owner Android certificate identity evidence;
- the approved signing-reset decision, old and new public certificate
  fingerprints, dual-backup verification dates, expected update-rejection
  evidence, completed data-migration safeguards, and successful fresh-install
  evidence;
- the validator-rendered manual QA report and its referenced evidence;
- an Owner publication statement binding all of the above.

The draft and published Release notes must prominently state that the Android
APK is not an in-place update from v1.5.10, requires public-clip drain and the
documented zero-peer Desktop reseed before uninstall, and that the old app has
no supported export for quarantined Android-only secret/private content. The
notes must require an empty-quarantine confirmation or explicit acceptance of
permanent loss, plus fresh installation, pairing, reseed pull, IME enablement,
and QS Tile setup.
They must identify both public certificate fingerprints and must not imply that
the old signer authorized, rotated to, or is cryptographically continuous with
the new signer.

Do not reconstruct the publication commands from snippets in this runbook.
Generate a fresh Owner pack and execute its Step H as one intact, fail-closed
PowerShell block. That block revalidates the clean exact-target checkout, trusted
tool paths, current `main`, workflow run, live release-environment certificate,
all attestations, the exact release-tag state, and the Owner-approved binding;
every draft asset ID/size/digest and the committed bytes of each executed
validator are checked again.
The strict verifier recomputes that binding and hands one minimal publication
projection directly to PowerShell memory; Step H never trusts a binding field
re-read from mutable disk JSON.
It publishes by the verified numeric Release ID, not by a mutable tag lookup,
then checks the same ID and re-downloads the published bytes. The generated
block finally runs `release_artifact_evidence.py --require-live-published-release`
against that download. This reconstructs the approved pre-publication binding
from live state and emits a distinct publication-closure binding plus a path-free
Issue comment draft. Run it only while
`main` and `refs/tags/v1.6.0` are procedurally frozen. Use an Owner-exclusive Release mutation window;
GitHub does not make branch/tag movement, draft asset
mutation, and publication one atomic transaction. Step H therefore resolves the
exact tag immediately before publication and again after publication, including
annotated-tag chains.

### Post-publication recovery

Once the numeric Release `PATCH` is sent, a nonzero client exit is not proof that
GitHub left the Release as a draft: the response can be lost after publication.
Step H therefore always follows it with an exact-ID read-only GET. If that GET
cannot establish the state, or any later lookup, download, hash, or verifier
command fails, assume `v1.6.0` may already be public. Do not rebuild, edit the
Release, delete evidence, or run the normal draft publication path again. Keep
Issue #36 open and preserve the first failure output. If exact-ID GET proves the
Release remains a draft, stop and review before starting a new nonce-bound Step
H; never blindly retry the previous mutation path.

Use a newly generated Owner pack and the dedicated **Step H recovery** block. In
the original trusted PowerShell session it reuses the already validated values.
If that session was lost, first re-establish the same frozen target/run/binding,
trusted executable paths, signer input, repository-root check, helper functions,
and clean tracked-source checks from the fresh pack; do not execute its draft
checks or `PATCH`. The recovery block creates a unique download directory and
unique JSON/comment names, performs only `GET`/download operations plus local
verification, and runs `--require-live-published-release` against the fresh
bytes. Repeated transient failures must use another unique recovery directory so
the original evidence remains inspectable.

After Step H's post-publication checks, review the generated comment and rerun
`tools/release_readiness.py`. Issue #36 remains open unless every automated and
Owner/manual gate is verifiably complete; neither publication nor the closure
binding alone authorizes completion.
