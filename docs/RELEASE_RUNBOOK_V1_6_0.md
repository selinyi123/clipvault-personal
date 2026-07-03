# ClipVault Personal v1.6.0 Release Runbook

This runbook turns the Issue #36 release gate into an executable owner checklist.
It does not replace the manual Android/IME/sync/Windows clipboard QA evidence.

## Safety boundary

- Do not publish `v1.6.0` until Issue #36 has all required evidence.
- Do not commit keystores, passwords, generated `.b64` files, or release artifacts.
- Run the workflow first with `create_draft_release=false`.
- Treat `Release candidate dry run` artifacts as unsigned packaging evidence only.

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
commit, run it before continuing:

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
creation.

Do not weaken the workflow by adding push or pull-request triggers.

## 3. Configure Android signing secrets

Required repository secrets:

```text
ANDROID_RELEASE_KEYSTORE_B64
ANDROID_RELEASE_KEYSTORE_PASSWORD
ANDROID_RELEASE_KEY_ALIAS
ANDROID_RELEASE_KEY_PASSWORD
```

Example CLI setup for the keystore value:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("clipvault-release.jks")) |
  Set-Content -Encoding ascii keystore.b64
gh secret set ANDROID_RELEASE_KEYSTORE_B64 --repo selinyi123/clipvault-personal < keystore.b64
Remove-Item keystore.b64
```

Set the password/alias secrets without echoing them into logs:

```powershell
gh secret set ANDROID_RELEASE_KEYSTORE_PASSWORD --repo selinyi123/clipvault-personal
gh secret set ANDROID_RELEASE_KEY_ALIAS --repo selinyi123/clipvault-personal
gh secret set ANDROID_RELEASE_KEY_PASSWORD --repo selinyi123/clipvault-personal
```

## 4. Run the signed artifact workflow without creating a release

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

## 6. Optional draft GitHub Release

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
