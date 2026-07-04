# ClipVault Personal v1.7 Field-Test Package Plan

Date: 2026-07-04

This document defines the package-upload path for v1.7 stability field testing.
It does not declare v1.7 stable, does not publish `v1.7.0`, does not close Issue #36,
and does not replace the v1.7 exit criteria in `docs/STABILITY_PLAN_V1_6_V1_7.md`.

## Scope

The current source metadata remains `1.6.0` until a dedicated v1.7 release-gate
issue exists and Owner approval explicitly authorizes a version bump. A field
test can still exercise the v1.7 stability criteria against the current target
SHA by uploading release-candidate artifacts and recording device evidence.

Use two artifact lanes:

| Lane | Workflow | Uploaded artifacts | Use | Stable/release meaning |
|---|---|---|---|---|
| Field-test candidate | `Release candidate dry run` (`.github/workflows/release-candidate.yml`) | `clipvault-windows-release-candidate`; `clipvault-android-release-candidate` | Owner/device smoke testing and v1.7 stability evidence collection | Candidate only. Not signed/final evidence. |
| Signed/final release | `Release artifact build` (`.github/workflows/release.yml`) | `clipvault-windows-release-artifacts`; `clipvault-android-signed-release-artifacts`; optional draft GitHub Release | Owner-controlled release gate after secrets/environment/manual QA are ready | Release evidence only after Owner approval and artifact validation. |

## Field-test preconditions

Before uploading field-test packages:

1. The target branch or main SHA is known.
2. Desktop tests and Android unit/debug-unit tests have passed locally or in CI
   for the target SHA.
3. `Issue #36` status is known and remains open unless all v1.6.0 release-gate
   evidence is recorded.
4. The package run is labeled as `field-test candidate` or `release-candidate
   dry run`, not `stable`, `signed`, `published`, or `v1.7.0 release`.
   These field-test candidate artifacts are not signed/final release evidence.
5. No version bump to `1.7.0` is made unless a dedicated v1.7 release-gate issue
   exists and Owner approval records why the bump is no longer just a progress
   signal.

## Upload commands

After the target branch has been pushed, trigger the candidate workflow for the
exact ref that should be tested:

```powershell
gh workflow run "Release candidate dry run" `
  --repo selinyi123/clipvault-personal `
  --ref BRANCH_OR_MAIN
```

Find the run and confirm it completed successfully:

```powershell
gh run list `
  --repo selinyi123/clipvault-personal `
  --workflow "Release candidate dry run" `
  --branch BRANCH_OR_MAIN `
  --limit 5
```

Download the artifacts into separate directories so Windows and Android
manifests/checksums cannot collide:

```powershell
$runId = "RELEASE_CANDIDATE_RUN_ID"
$targetSha = "TARGET_SHA"
$version = "1.6.0"
Remove-Item field-test-v1.7 -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path field-test-v1.7/windows | Out-Null
New-Item -ItemType Directory -Force -Path field-test-v1.7/android | Out-Null

gh run download $runId `
  --repo selinyi123/clipvault-personal `
  --name clipvault-windows-release-candidate `
  --dir field-test-v1.7/windows
gh run download $runId `
  --repo selinyi123/clipvault-personal `
  --name clipvault-android-release-candidate `
  --dir field-test-v1.7/android

python scripts/verify_release_manifest.py `
  --artifact-dir field-test-v1.7/windows `
  --platform windows `
  --version $version `
  --commit $targetSha `
  --expect-dry-run
python scripts/verify_release_manifest.py `
  --artifact-dir field-test-v1.7/android `
  --platform android `
  --version $version `
  --commit $targetSha `
  --expect-dry-run
```

## Device-use rules

- Windows: use the candidate portable executable and installer for install,
  launch, clipboard, sync, and uninstall smoke checks. Expect unsigned-Windows
  warnings unless Owner adds a separate code-signing process.
- Android: use `ClipVault-Android-v<version>-debug.apk` for real-device install
  smoke unless Owner signs a release APK. The `release-unsigned.apk` artifact is
  packaging evidence and should not be cited as a signed install package.
  Android unsigned release APK is not a signed install package.
- Record the run URL, target SHA, artifact names, device model, OS version,
  install result, and pass/fail observations in the v1.7 release-gate issue or
  `docs/HANDOFF.md`.

## Stable exit boundary

Do not call v1.7 stable until:

- every `docs/STABILITY_PLAN_V1_6_V1_7.md` exit-criteria row has automated, CI,
  and Owner/manual evidence;
- a dedicated v1.7 release-gate issue exists and has Owner approval;
- any `1.7.0` version bump is aligned across desktop, Android, installer, and
  docs;
- signed/final artifacts are validated separately from release-candidate
  artifacts; and
- public release notes match the actual uploaded assets.
