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
Credential failure returns unavailable, wipes the plaintext, and never ACKs.
A process restart therefore cannot replay an acknowledged sequence.

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
RPC. Focus loss clears tokens. Password/unknown fields fail closed. The broker
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
