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
manifests/checksums cannot collide. Prefer the repository helper when GitHub
CLI artifact downloads are flaky, because it reads the workflow-run artifact
inventory, downloads each artifact ZIP by exact artifact name/id, rejects
unsafe or non-flat ZIP member paths, checks GitHub's artifact SHA-256 digest
when the API returns one, and can immediately run the dry-run manifest/checksum
verifier:

```powershell
$runId = "RELEASE_CANDIDATE_RUN_ID"
$targetSha = "TARGET_SHA"

python tools/download_field_test_artifacts.py `
  --run-id $runId `
  --output-dir field-test-v1.7 `
  --target-commit $targetSha `
  --verify-manifests `
  --clean
```

Fallback `gh run download` commands:

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

`tools/download_field_test_artifacts.py` does not trigger workflows, post issue
comments, install apps, run device QA, sign or publish releases, close Issue
#82/#36, or claim v1.7 stable. It only prepares and validates local
release-candidate artifact directories for the next evidence step.

## Evidence helper

After the Owner or agent downloads the artifacts, use the local helper to
machine-check both candidate directories and render an artifact-only Issue #82
comment draft. This draft is expected to remain `BLOCKED` because device smoke
rows still require Owner observation:

```powershell
python tools/field_test_evidence.py `
  --verify-artifacts `
  --windows-dir field-test-v1.7/windows `
  --android-dir field-test-v1.7/android `
  --target-commit $targetSha `
  --ci-run-url "CI_RUN_URL" `
  --candidate-run-url "RELEASE_CANDIDATE_RUN_URL" `
  --tester "OWNER_OR_AGENT_NAME" `
  --tested-at "ISO_8601_TIMESTAMP" `
  --output field-test-v1.7-artifacts-comment.md `
  --no-fail
```

After the Owner runs the real-device smoke checks, use the same helper to
prepare a complete Issue #82 comment:

```powershell
python tools/field_test_evidence.py --write-template field-test-v1.7.json
python tools/field_test_evidence.py --input field-test-v1.7.json --no-fail
python tools/field_test_evidence.py --input field-test-v1.7.json --output field-test-v1.7-issue-comment.md
gh issue comment 82 `
  --repo selinyi123/clipvault-personal `
  --body-file field-test-v1.7-issue-comment.md
```

`tools/field_test_evidence.py --verify-artifacts` calls the same manifest and
checksum verifier used above with `--expect-dry-run`, then marks only the
artifact-verification rows as `pass`. The helper also validates that any full
report names the target commit, CI run, release-candidate run, Windows
candidate artifact, Android candidate artifact, Android debug APK install
package, downloaded-manifest verification, Windows smoke results, and Android
IME/privacy smoke results. It does not download artifacts, install apps, run
device QA, post to GitHub, sign or publish releases, close Issue #82, close
Issue #36, or claim v1.7 stable.
Scope note: it does not download artifacts, install apps, run device QA.
All required rows must be `pass` with observed evidence before the helper marks
the field-test report ready. `blocked` and `fail` rows must include a concrete
next step. A ready field-test report is still not v1.7 stable evidence by
itself; stable release remains gated by the exit criteria below.

## Windows portable smoke helper

After candidate artifacts are downloaded and verified, the Owner or agent can
run a low-side-effect Windows portable smoke before spending time on installer,
clipboard, sync, or Android checks:

```powershell
python tools/windows_candidate_smoke.py `
  --windows-dir field-test-v1.7/windows `
  --output field-test-v1.7-windows-smoke.json `
  --no-fail

python tools/field_test_evidence.py `
  --verify-artifacts `
  --windows-dir field-test-v1.7/windows `
  --android-dir field-test-v1.7/android `
  --target-commit $targetSha `
  --ci-run-url "CI_RUN_URL" `
  --candidate-run-url "RELEASE_CANDIDATE_RUN_URL" `
  --tester "OWNER_OR_AGENT_NAME" `
  --tested-at "ISO_8601_TIMESTAMP" `
  --windows-smoke-report field-test-v1.7-windows-smoke.json `
  --output field-test-v1.7-artifacts-and-windows-smoke-comment.md `
  --no-fail
```

The smoke helper runs the portable candidate with `--help`, then starts it with
an isolated temporary config and probes `/api/health` on a temporary loopback
port. The smoke config disables backup and sets the watcher poll interval to
`600000` ms so the short health probe does not read or ingest the user's current
clipboard. The resulting report can mark only the `portable_launch` row. It
does not run the installer, write the clipboard, validate clipboard capture,
test LAN/Tailscale sync, run Android device checks, sign or publish releases,
close Issue #82/#36, or claim v1.7 stable.

## Read-only readiness report

Before asking the Owner to spend device time, run the read-only Issue #82
readiness checker:

```powershell
python tools/field_test_readiness.py --no-fail
python tools/field_test_readiness.py --json --no-fail
```

The checker reads the current `main` SHA, the latest matching CI and
`Release candidate dry run` runs, the release-candidate artifact metadata from
GitHub's workflow-run artifacts API, and the Issue #82 body/comments. It is
intended to catch evidence drift such as a stale issue-body baseline while a
newer comment contains the current SHA and run URLs.

The readiness checker does not download artifacts, verify local artifact bytes,
install apps, run device QA, post comments, edit Issue #82, sign or publish
releases, close Issue #82, close Issue #36, or claim v1.7 stable. A pass on the
artifact-metadata row only means GitHub still reports the named candidate
artifacts as present and not expired; downloaded manifest/checksum verification
and real-device smoke remain separate Owner/action rows.

## Owner action pack

To reduce handoff drift before spending device time, generate a local Owner
action pack for the current `main` readiness state:

```powershell
python tools/prepare_field_test_owner_pack.py `
  --output-dir field-test-owner-pack `
  --tester "OWNER_OR_AGENT_NAME" `
  --tested-at "ISO_8601_TIMESTAMP"
```

If the Windows and Android candidate artifact directories have already been
downloaded, pass them so the generated evidence JSON/comment can mark the
manifest/checksum rows as `pass` while leaving device-smoke rows blocked:

```powershell
python tools/prepare_field_test_owner_pack.py `
  --output-dir field-test-owner-pack `
  --windows-dir field-test-v1.7/windows `
  --android-dir field-test-v1.7/android `
  --tester "OWNER_OR_AGENT_NAME" `
  --tested-at "ISO_8601_TIMESTAMP"
```

The generated pack contains:

- `OWNER_FIELD_TEST_ACTION_PACK.md` with exact current SHA/run URLs, artifact
  metadata, download/verify commands, Windows smoke commands, Android ADB smoke
  commands, and remaining Issue #82 rows. The Windows smoke snippet writes a
  temporary isolated config under the smoke directory, and the Android snippet
  records/restores the previous input method in a `finally` block;
- `field-test-v1.7.json`, a prefilled evidence template for Owner observations;
- `field-test-v1.7-issue-comment.md`, a rendered Issue #82 draft that remains
  `BLOCKED` until every required Owner/manual row is filled; and
- `pack-summary.json`, a machine-readable summary of the generated pack.

`tools/prepare_field_test_owner_pack.py` is an action-pack generator only. It
does not download artifacts, install apps, run device QA, post comments, edit
issues, sign or publish releases, close Issue #82/#36, or claim v1.7 stable.

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
