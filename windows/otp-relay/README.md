# Windows OTP Relay production boundary

The Windows v2 package builds an independent x64
`ClipVaultOtpBroker.exe`. It is neither the Python Runtime nor the TSF DLL and
does not use the system clipboard, files, SQLite, ordinary sync/outbox, content
logs, analytics, `SendInput`, or form submission.

## Production flow

```text
ClipVault.exe (opaque CVOB offer only)
  -> per-user, reject-remote Named Pipe (strict 250 ms budget)
  -> ClipVaultOtpBroker.exe
       -> current-user Credential Manager CVPK v1 authority
       -> CNG HKDF-SHA256 + AES-256-GCM
       -> in-memory bounded OTP core
       -> non-activating generic prompt (no OTP digits)

user presses Ctrl+Alt+O in the focused field
  -> TSF sends process/thread/window/document/context binding to IME Host
  -> signed fixed-path Host arms and consumes one broker claim
  -> TSF revalidates focus and calls ITfRange::SetText in one edit session
  -> all application-owned mutable OTP buffers are wiped
```

The Desktop offer wire remains the frozen `CVOB` v1 format mirrored by
`desktop/clipvault/otp/windows_pipe.py`. The broker validates canonical UUIDv4
session/event/device identities, a positive 63-bit sequence, target/sender,
TTL/future skew, nonce/event replay, and the canonical OTP-3A AAD before
admission. CNG reproduces `contracts/vectors/otp_aead_v1.json`, including the
one-bit tamper failures.

## Credential and replay authority

Each pair is a fixed 96-byte `CVPK` v1 Generic Credential at:

```text
ClipVault/OTP/Pair/v1/<canonical-session-uuid>
```

The byte layout is `CVPK | version/reserved | session | sender | target |
32-byte verifier | u64be high_sequence`. Only the broker native target links
the Credential Manager reader and CNG decryptor. After authentication and
decryption, the broker acquires a per-session mutex, rereads the exact CVPK,
atomically replaces its high sequence with `CredWriteW`, and reads it back.
Only then can it retain the short in-memory event and acknowledge delivery.
Transient Credential Manager/provider or per-session mutex failure returns
unavailable, wipes the plaintext and preserves the in-memory Slot for a later
retry. A missing, malformed or session-mismatched CVPK is treated as invalid,
clears that Slot and never ACKs. A process restart therefore cannot replay an
acknowledged sequence.

Revocation has a second durable authority: before deleting a CVPK, the native
authority writes and reads back a 24-byte `CVRV` v1 tombstone at
`ClipVault/OTP/Revoke/v1/<canonical-session-uuid>`, under the same per-session
mutation mutex. The tombstone contains only the protocol marker and session
epoch, never a verifier or OTP. A missing CVPK is still an idempotent delete,
but a failed/transient delete leaves the tombstone in place. The Broker keeps a
process-local fence for fast rejection before slot lookup and checks the
durable marker while acquiring the CVPK under the same per-session mutex, so a
stale CVPK restored after a Broker restart cannot recreate a revoked session.
Malformed markers are deny-by-default; provider failures return unavailable.
Tombstones are intentionally not deleted during normal re-pair cleanup because
every new pair receives a fresh session epoch.

### Pair lifetime and nonce rotation

One pair epoch has a sealed 4,096-entry nonce-history budget shared with the
Android producer. The Broker accepts at most 4,095 OTP offers under that
AES-GCM key and reserves the final entry for the content-free
`kRotationRequired` (`CVOB` status `9`) response. The boundary uses the
sender's persisted sequence as well as in-process replay markers, so a Broker
restart cannot consume the reserved slot. Replay markers are never evicted or
LRU-reused under an existing key. Desktop exposes this as the safe
diagnostic code `otp_pair_rotation_required`; the rejected OTP remains owned
by the sender and is not acknowledged or persisted by the Broker.

Internal daily builds use the existing explicit re-pair flow:

1. In Desktop device management, revoke/unpair the Android device. This marks
   the route revoked, deletes the CVPK credential under the cross-process
   mutation mutex, and sends `RevokeSession` to wipe the Broker slot.
2. In Android **OTP Relay settings**, choose **Forget local OTP pair**. This
   deletes the Keystore-sealed verifier, sequence and nonce history.
3. Re-pair the device/sync bearer if it was removed, then run OTP pairing
   again. `SqliteOtpPairingAuthority.pair()` creates a new UUIDv4
   `session_epoch`, a new 256-bit verifier and therefore a new AEAD key.

The same verifier must never be replaced in place for an existing epoch.
Credential Manager writes, Broker high-sequence updates, Arm/Consume
revalidation and deletion use the exact named mutex
`Local\\ClipVaultOtpCredentialV1-<session_epoch>`. Missing or changed
credentials fail closed and their cached slots are securely reclaimed; valid
slots are never evicted merely to make room.

## Local process and focus boundary

The server DACL grants only the current user and SYSTEM and always sets
`PIPE_REJECT_REMOTE_CLIENTS`. Production also requires the exact combined
installation paths and valid Authenticode trust for the broker itself and peer:

- `{app}\ClipVault.exe` may send opaque offers;
- `{app}\ime\host-x64\ClipVaultImeHost.exe` may arm/consume;
- arbitrary same-user programs and the Python process cannot consume OTPs.

The 15-second claim contains process ID, GUI thread ID, focused HWND, a
document UUID and a context UUID. The window owner and GUI thread are checked
by the broker. The TSF side checks the active input desktop, thread focus,
current document/context and field privacy both before and after the bounded
RPC. `GetSystemMetrics(SM_REMOTESESSION)` is a hard deny before the OTP is
consumed, so Remote Desktop sessions cannot arm or insert it. Focus loss clears
tokens. Password/unknown fields fail closed. The broker
prompt is `WS_EX_NOACTIVATE`, does not show digits, hides after 15 seconds and
uses `WDA_EXCLUDEFROMCAPTURE` only as defense in depth.

## Build and evidence

`windows/ime/CMakeLists.txt` keeps three boundaries:

- `clipvault_otp_wire`: Host-safe bounded local protocol only;
- `clipvault_otp_crypto` and `clipvault_otp_broker`: CNG/WinCred, never TSF;
- `ClipVaultOtpBroker.exe`: production x64 broker and prompt.

CTest covers the normative AEAD/tamper vector, strict core transitions, real
current-user WinCred CVPK round trips and restart replay rejection, and real
local Named Pipe offer/arm/consume plus cancellation deadlines. Build and test
do not register the TSF profile, enable logon startup, or touch production
credentials. Test credentials use a random v4 session target and are deleted.
The server keeps each response pipe alive only until the peer closes or the
same absolute request deadline expires; this prevents
`DisconnectNamedPipe` from discarding unread response buffers without using
an unbounded `FlushFileBuffers` call. The real Pipe test passed 20 consecutive
runs after this regression fix.

The package includes opt-in enable/disable scripts. The v2 installer exposes
an unchecked `clipvaultotpbroker` task that calls the enable script, and its
uninstall path always calls the exact-path disable script. Enabling refuses an
unsigned broker. Owner signing, actual system registration, interactive focus
tests, Windows lock/unlock, screen sharing, and the broad application matrix
remain manual release evidence; source/CTest success is not that evidence.

Native local tests may opt into unsigned peer binaries only through a private
test namespace. The separate `CLIPVAULT_ENABLE_INSECURE_DEVELOPMENT_TRUST`
CMake option is disabled by default; even a source-tree development build must
set `CLIPVAULT_INSECURE_DEVELOPMENT_PIPE_TRUST=1`, and the installer/release
configuration never enables or propagates it. A packaged/frozen Desktop process
always requires the exact signed peer paths.
