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

## 5. Record evidence on Issue #36

Comment on Issue #36 with:

- workflow run URL
- Android signed artifact name
- `apksigner verify --print-certs` evidence file name
- confirmation that `RELEASE_MANIFEST.json` records `signed=true`

Do not close Issue #36 until manual device QA is also recorded.

## 6. Record manual QA evidence on Issue #36

After running the real Android device, IME privacy, sync, and Windows clipboard
privacy checks from `docs/MANUAL_QA_V1_6_0.md`, use the local helper to validate
and render the manual-QA evidence comment:

```powershell
python tools/manual_qa_evidence.py --write-template manual-qa-v1.6.0.json
python tools/manual_qa_evidence.py --input manual-qa-v1.6.0.json --no-fail
python tools/manual_qa_evidence.py --input manual-qa-v1.6.0.json --output manual-qa-issue-comment.md
```

Post the rendered comment only after the Owner has filled the JSON with real
observations. The helper is local-only: it does not call GitHub, run device QA,
sign artifacts, publish releases, edit checklist rows, or close Issue #36. A
passing manual-QA report still does not replace signed artifact evidence, final
Windows artifact evidence, release environment/secrets evidence, or final
Owner-approved GitHub Release publication.

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
