# Android Signing Reset for v1.6.0

This document defines the exceptional Android migration required for the
ClipVault Personal v1.6.0 release. It supplements
[RELEASE_RUNBOOK_V1_6_0.md](RELEASE_RUNBOOK_V1_6_0.md) and
[MANUAL_QA_V1_6_0.md](MANUAL_QA_V1_6_0.md); it does not replace either release
gate.

## Owner decision and immutable facts

On 2026-07-22, the Owner approved all of the following as one compatibility
decision:

- keep the application ID `com.clipvault.app`;
- replace the unavailable v1.5.10 signing key with a new long-lived release key;
- accept that an installed v1.5.10 cannot be updated in place and must be
  uninstalled before installing v1.6.0; and
- complete the public-data migration before that destructive uninstall; and
- proceed only after the old app's quarantine is confirmed empty, or after the
  Owner explicitly accepts permanent loss of every quarantined row.

The released v1.5.10 APK has one verified APK Signature Scheme v2 signer:

```text
certificate subject: CN=ClipVault Personal, OU=Self, O=Personal, L=NA, ST=NA, C=NA
certificate SHA-256: 898f21c2b59a4a4729fd386d91a86711b81ea567d5d85bf391a2e0fff2f1f9f1
```

The corresponding private key cannot be recovered from the APK and is no
longer available to the Owner. Consequently:

- there is **no cryptographic signing continuity** between v1.5.10 and v1.6.0;
- v1.6.0 must not be described as a seamless, normal, or in-place Android
  update;
- the old fingerprint must not be configured as
  `ANDROID_RELEASE_CERT_SHA256` for the new build; and
- generating a valid new signature does not prove authorization by, rotation
  from, or continuity with the old signer.

Owner approval permits this reset but does not by itself satisfy signed-artifact
verification, physical-device QA, publication approval, or Issue #36 closure.

An initial replacement candidate was generated on 2026-07-22 with certificate
SHA-256
`86bdcbca45f0e9bce4c7cfbb3bc52f85f34a482acff8220af11dc659a2ec567c`.
It was configured as the GitHub `release` environment's public trust-anchor
variable on 2026-07-22, but no Release artifact build ran and it was never used
for a published v1.6.0 release. The Owner superseded it before publication after
the required independent backup verification could not be completed.

The final replacement long-lived release certificate was generated and
independently recovery-tested on 2026-07-23. It is the only signer approved for
the v1.6.0 Android release:

```text
certificate subject: CN=ClipVault Personal, OU=Release, O=Personal, L=NA, ST=NA, C=NA
certificate SHA-256: ef93502c8e5e68f1d0c8b46c36c521b84a09b11be8bc924030b5ada16d761757
```

The release workflow and Owner publication gate must reject any other new
fingerprint. This public trust anchor does not relax the backup, migration, or
manual-QA requirements below.

## 1. Preserve data before touching the old installation

Complete this section while v1.5.10 is still installed. Do not uninstall the
package or clear its data until every row is complete.

1. Pair the old Android installation with the intended current Desktop node if
   it is not already paired. v1.5.10 predates `outbox_base_seq`; the current
   Desktop therefore records an explicit unknown legacy baseline and establishes
   it from the first retained positive sequence. If the peer row was recreated,
   send/sync a benign item and confirm the Android pending outbox clears before
   relying on the barrier below. Merely seeing a gapped event on Desktop is not
   proof that Android received a contiguous ACK.
2. Pause new clipboard/share captures. Send one benign, recognizable barrier
   marker through the old Android Share target, let synchronization finish, and
   confirm on Desktop that the marker arrived and confirm on Android that the
   pending outbox cleared after the Desktop cursor advanced through it. This
   proves all earlier retained public clip events drained. A visible marker
   with a still-pending Android row is a failed drain.
3. Verify the migrated public-clip counts or safe labels on Desktop. Personal
   Memory is Desktop-authoritative in v1.5.10: Android consumes it but does not
   publish Android-created Memory back to Desktop. Verify the required public
   Memory already exists on Desktop; do not claim an Android -> Desktop Memory
   migration that the product cannot perform.
4. Open the old app's quarantine and determine whether it is empty without
   recording its masked contents. v1.5.10 has no supported detail, copy, reveal,
   or export path for quarantined rows, and app-private storage cannot be
   recovered after uninstall because Android backup is disabled.
5. If quarantine is not empty, stop. Continue only if the Owner explicitly
   accepts permanent loss of every quarantined row. Do not describe that choice
   as migration, preservation, export, or backup.
6. Force-stop the old app or disconnect it from the synchronization network,
   revoke its Desktop peer, stop ClipVault Desktop, and verify the Desktop
   database has zero paired peers. The old app must never synchronize again
   after this point.
7. With Desktop stopped, create a unique UTC run id and execute the one-shot
   reseed tool first in its default read-only mode, review only its safe
   aggregate counts/budgets, and then repeat the exact command with `--apply`:

   ```powershell
   $runId = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
   python tools/prepare_android_signing_reset.py `
     --db "<existing Desktop clipvault.db>" `
     --run-id $runId
   python tools/prepare_android_signing_reset.py `
     --db "<existing Desktop clipvault.db>" `
     --run-id $runId `
     --apply
   ```

   Apply mode fails unless the Desktop instance lock is available, the schema is
   current, there are zero peers, Secret Guard/origin checks pass, and the
   explicit event/payload budgets are satisfied. In the same transaction it
   clears every retained Desktop outbound row, preserves the AUTOINCREMENT
   high-water, and writes only the current safe public clips,
   Desktop-authoritative public Memory, and one content-free ACK marker. This
   prevents a fresh client pulling from zero from replaying deleted, stale,
   oversized, or newly quarantined legacy history. It does not export
   secret/private data.
   `skipped_invalid` and `skipped_unsafe_origin` must both be zero unless the
   Owner separately records which public rows are being excluded and explicitly
   accepts that exclusion. Account for all other skipped categories under the
   secret/deleted/quarantine contract before continuing.
8. Retain the ignored local state/result and safe aggregate output. Do not pair
   the replacement app until apply succeeds. From apply until delivery
   verification, keep Desktop clipboard capture, local metadata edits, and every
   other Desktop outbox writer frozen; any new outbound sequence invalidates the
   run and requires a separately reviewed replacement preparation. A repeated run is idempotent only
   while the exact snapshot events are still retained; if recovery is needed
   after pruning, stop and create a separately reviewed run id/state file.

Record only redacted migration evidence: timestamps, safe aggregate counts,
the barrier/outbox-drained observation, zero-peer confirmation, reseed result,
and the Owner's quarantine-empty or explicit-loss decision. Never put content,
credentials, device serials, private paths, database paths, or backup locations
in GitHub, QA JSON, logs, screenshots, or Release notes.

If any required public item cannot be accounted for, the quarantine decision is
not explicit, or reseed preparation is incomplete, stop. Data uncertainty is a
release blocker, not permission to proceed with uninstall.

## 2. Create and retain the new signing identity

Generate a new long-lived release keystore on an Owner-controlled machine. Use
an interactive password prompt or secure password-manager injection; never put a
password on a shell command line, in shell history, in a response file, or in
Git. Keep the existing release workflow inputs:

```text
ANDROID_RELEASE_KEYSTORE_B64
ANDROID_RELEASE_KEYSTORE_PASSWORD
ANDROID_RELEASE_KEY_ALIAS
ANDROID_RELEASE_KEY_PASSWORD
ANDROID_RELEASE_CERT_SHA256
```

Before using the new key for a release:

1. Create **two encrypted keystore backups in independent Owner-controlled
   storage locations**. The working copy does not count as either backup.
2. Retain the keystore and its credentials separately where practical. Do not
   commit or upload the keystore as a Release asset.
3. Open or restore each backup independently and run `keytool -list -v` against
   the intended alias.
4. Confirm both copies produce the same new certificate SHA-256 and that it is
   different from the old v1.5.10 fingerprint.
5. Record only the new public fingerprint, alias, and the two verification dates
   in Issue #36. Do not record passwords, keystore bytes, recovery material, or
   storage paths.
6. Configure the four values as GitHub `release` environment secrets. Configure
   the independently confirmed new 64-character lowercase fingerprint as the
   `release` environment variable `ANDROID_RELEASE_CERT_SHA256`.

Loss of this new private key would create another forced reinstall boundary.
The signed preflight must not run until both backups have been independently
verified.

## 3. Prove the expected update rejection

Use only `ClipVault-Android-v1.6.0-release-signed.apk` downloaded from the exact
final `draft=true` Release described by the runbook. Verify its SHA-256 against
the saved draft digest set before it touches the device.

The device must still have the real v1.5.10 `com.clipvault.app` installation and
its data at this point. Verify the version without recording a device serial:

```powershell
$installedPackage = (adb shell dumpsys package com.clipvault.app | Out-String)
if ($LASTEXITCODE -ne 0 -or $installedPackage -notmatch "versionName=1\.5\.10") {
  throw "The device does not have an eligible v1.5.10 installation"
}
```

Then attempt an update without uninstalling and retain its output locally:

```powershell
$resetEvidenceDir = ".\.field-test-artifacts\v1.6.0-signing-reset"
New-Item -ItemType Directory -Force -Path $resetEvidenceDir | Out-Null
$updateLog = Join-Path $resetEvidenceDir "adb-update-rejection.txt"
$updateOutput = (adb install -r ".\ClipVault-Android-v1.6.0-release-signed.apk" 2>&1 | Out-String)
$updateExit = $LASTEXITCODE
$updateOutput | Set-Content -Encoding utf8 -LiteralPath $updateLog
if ($updateExit -eq 0) {
  throw "Unexpected in-place update success; stop the signing-reset release lane"
}
if (-not (Select-String -LiteralPath $updateLog -SimpleMatch `
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE" -Quiet)) {
  throw "Update failed for a reason other than the expected signature mismatch"
}
Get-FileHash -Algorithm SHA256 -LiteralPath $updateLog
```

The eligible observation is a nonzero exit and Android's signature mismatch,
normally reported as:

```text
INSTALL_FAILED_UPDATE_INCOMPATIBLE
```

Preserve a redacted result reference and digest under the ignored
`.field-test-artifacts/` evidence directory. Do not include a device serial,
local absolute path, clipboard content, or private data. A different failure
such as no device, insufficient storage, an absent old package, or a corrupt APK
does not prove the signing discontinuity and must not be accepted.

This expected rejection is evidence of the compatibility break. It is not an
installation PASS, and it must occur before the old package is removed.

## 4. Perform the destructive reinstall

Proceed only after sections 1-3 are complete and reviewed by the Owner.
Uninstalling the package deletes its app-private data on the device:

```powershell
adb uninstall com.clipvault.app
if ($LASTEXITCODE -ne 0) { throw "Old ClipVault uninstall failed" }

adb install ".\ClipVault-Android-v1.6.0-release-signed.apk"
if ($LASTEXITCODE -ne 0) { throw "Fresh v1.6.0 install failed" }
```

Confirm the installed version and retain a redacted success reference. The
fresh-install evidence must identify the final draft APK name and SHA-256 and
must bind to the same target commit, workflow run attempt, and numeric draft
Release ID used by the artifact evidence.

Do not restore application database files from v1.5.10 into v1.6.0. Restore
public data only by pairing the fresh installation and pulling the prepared
one-shot Desktop reseed. Quarantined v1.5.10 rows cannot be restored through
this path.

## 5. Reconfigure and rerun physical-device QA

The fresh installation has no valid old pairing or Android component setup.
Complete and record all of the following using the final signed APK:

1. start Desktop only after the reseed apply succeeded, generate a new one-time
   pairing code, and pair the fresh installation;
2. pull the complete prepared snapshot, then allow one additional sync cycle so
   Android sends its durable ACK back to Desktop. Using the same database,
   run id, and (if overridden) state-file arguments as apply, run:

   ```powershell
   python tools/prepare_android_signing_reset.py `
     --db "<existing Desktop clipvault.db>" `
     --run-id $runId `
     --verify-delivery
   ```

   This row passes only when the safe JSON result has `status=ok`,
   `mode=verify-delivery`, `paired_devices=1`, and `delivery_verified=true`.
   The verifier rejects no peer, multiple peers, a peer paired before the reseed,
   an ACK below the recorded `reseed_end_seq`, a different database/state owner,
   or any durable outbox high-water other than the exact reseed end. It proves
   a post-reseed peer ACK, not which APK is installed; the separately recorded
   final-APK uninstall, fresh-install, and fresh-pairing evidence is still
   mandatory. Compare safe public-clip and Desktop-authoritative Memory
   counts/labels with the pre-uninstall record;
3. capture the Share-sheet flow and confirm the explicitly shared text appears
   locally;
4. add and exercise the Quick Settings Tile, retaining a redacted screenshot;
5. enable ClipVault Panel IME again and confirm an explicit candidate tap pastes
   the selected text;
6. rerun IME privacy QA: password and incognito/private fields suppress
   candidates, typed text is not logged, and IME code performs no network work;
7. rerun bidirectional public-clip sync QA and Desktop -> Android public-Memory
   sync QA; do not claim Android -> Desktop Memory creation; and
8. confirm secret/private content remains isolated under the current contract,
   including after re-pair and reconnect.

Also execute the full re-pair/outbox high-water scenario and the remaining
physical Android rows in [MANUAL_QA_V1_6_0.md](MANUAL_QA_V1_6_0.md). Screenshots
must be reviewed for notifications, account identifiers, clipboard content,
pairing codes, paths, and other private information before they are referenced
from Issue #36.

## 6. Required Issue #36 and Release-note disclosure

Issue #36 evidence must include:

- the dated Owner approval for this exact reset decision;
- the old public certificate fingerprint and the independently verified new
  public certificate fingerprint;
- confirmation that both new encrypted keystore backups were independently
  opened and fingerprint-checked, with verification dates but no locations;
- redacted public-clip drain, zero-peer reseed, and fresh-client restore
  evidence;
- Owner confirmation that the old quarantine was empty, or explicit acceptance
  that every quarantined row was permanently lost because no export path exists;
- the expected `adb install -r` `INSTALL_FAILED_UPDATE_INCOMPATIBLE` evidence;
- successful uninstall, fresh-install, new-pairing, IME, QS Tile, privacy, and
  bidirectional-sync evidence; and
- the exact final draft APK name/SHA-256 and its artifact binding.

Both the draft and published v1.6.0 Release notes must put an Android migration
warning near the top. At minimum, state all of the following in plain language:

```text
Android signing reset: the private signing key used for v1.5.10 is unavailable.
The v1.6.0 APK keeps package ID com.clipvault.app but has a new signing identity,
so Android cannot install it over v1.5.10. Before uninstalling v1.5.10,
synchronize and verify public clips on Desktop, verify Desktop-authoritative
public Memory, revoke the drained old peer, and run the documented one-time
Desktop reseed preparation. v1.5.10 has no supported export path for quarantined
Android-only secret/private items: confirm its quarantine is empty, or explicitly
accept their permanent loss; otherwise stop. Uninstall v1.5.10, install v1.6.0
fresh, pair again, pull the prepared reseed, and re-enable the Panel IME and
Quick Settings Tile.
Old certificate SHA-256: 898f21c2b59a4a4729fd386d91a86711b81ea567d5d85bf391a2e0fff2f1f9f1
New certificate SHA-256: ef93502c8e5e68f1d0c8b46c36c521b84a09b11be8bc924030b5ada16d761757
There is no cryptographic signing continuity between these certificates.
```

Do not publish if the warning is missing, softened into a normal upgrade claim,
or contains unresolved markers. Publication still requires the fail-closed
Step H flow from the Owner pack and a separate final Owner publication approval.
