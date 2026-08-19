# ClipVault Windows IME production source

This directory contains the project-authored C++20 TSF clients, external x64
Host, candidate window, installer include, and protocol-v2 production adapter
for the Windows v2 line.
The Host loads the hash-pinned official librime 1.16.1 x64 SDK from an external
cache in production builds. ClipVault-owned schemas and punctuation live under
`shared-input/rime`; the Apache-2.0 `pinyin_simp` dictionary is fetched at one
locked commit and hash-checked before staging. No TypeDuck, Moqi, libIME2,
librime source/binary, or upstream dictionary body is committed here. Builds
and tests never register the input method. Mixed-scope COM/TSF registration
remains an explicit, reversible, separately authorized command.

## Boundary

The intended later runtime split is:

```text
Windows application
  -> ClipVault TSF DLL (COM/TSF, composition, candidate UI only)
  -> per-user ACL Named Pipe, framed protobuf v2
  -> external IME host (librime and local engine session)

ClipVault Python Runtime
  -> asynchronous, Secret-Guarded Runtime Snapshot V1 publisher
  -> never a per-key dependency

ClipVault OTP path (independent and opt-in)
  -> ClipVault.exe sends only opaque CVOB offers
  -> x64 ClipVaultOtpBroker.exe owns WinCred, CNG, replay and plaintext
  -> Ctrl+Alt+O creates one short context-bound TSF insertion claim
```

The transport-neutral research contract and golden vectors remain under
`spikes/windows-ime/`; this directory implements the maintained Win32 boundary.
The native Host owns Rime sessions; the TSF DLL projects composition
through synchronous edit sessions and shows a non-activating, clickable
candidate window with numeric selection and paging. It does not:

- vendor or patch upstream code, register during build, or package a release;
- modify the existing Python Runtime, Windows installer, or release workflows;
- connect the TSF path to HTTP, SQLite, sync, clipboard history, or networking;
- persist typed text, preedit, candidate selections, or commit text;
- interleave ClipVault/OTP items into Rime ranking.

## Production implementation

Only the external x64 Host has a third-party runtime dependency. Release/package
builds require the hash-locked official librime SDK, canonical shared schemas,
and the locked external dictionary:

```text
ClipVaultTextService.dll
  -> ITfTextInputProcessorEx + ITfKeyEventSink
  -> synchronous TSF edit session and UTF-16 composition range
  -> password/PIN input-scope hard disable
  -> non-activating candidate window, click/numeric select, PgUp/PgDn
  -> framed protocol-v2 Named Pipe client
  -> x64 and x86 binaries in isolated COM registry views

ClipVaultImeHost.exe
  -> one instance per Windows session
  -> byte-mode Named Pipe with PIPE_REJECT_REMOTE_CLIENTS
  -> DACL limited to the current user and SYSTEM
  -> handshake + session/revision/stable-candidate-ID checks
  -> librime 1.16.1 adapter (or explicit echo-only test fallback)
  -> explicit select/page/commit/cancel operations
  -> asynchronous Runtime Snapshot V1 client and bounded in-memory cache

ClipVaultOtpBroker.exe
  -> frozen 96-byte current-user CVPK v1 records and persistent high sequence
  -> canonical OTP-3A CNG AES-256-GCM/HKDF verification
  -> per-user reject-remote CVOB pipe with exact signed peer paths
  -> non-activating generic prompt and 15-second one-use claims
```

The OTP broker is packaged separately under `otp-broker` and is opt-in. The
TSF DLL never links CNG or Credential Manager OTP code. A preserved
`Ctrl+Alt+O` action binds the current process, GUI thread, HWND, TSF document
and context tokens; focus or desktop changes fail closed. The Host performs the
bounded arm/consume RPC and the TSF edit session inserts with `ITfRange::SetText`.
No `SendInput`, Enter key, clipboard, file, database or Python plaintext path
exists. See `windows/otp-relay/README.md` for the CVPK/CVOB and test evidence.

### Runtime Snapshot V1

The x64 Host, and never the TSF DLL, connects to the per-user Python Runtime
pipe `\\.\pipe\ClipVaultRuntimeSnapshotV1-<Windows session id>`. One absolute
250 ms deadline covers the complete `ClientHello -> HostHello -> request ->
response` exchange. The client accepts only the current user's server process
at the expected installation path with a trusted Windows signature. Production
does not accept an environment override for this pipe name.

The response parser implements the strict bounded protobuf subset frozen in
`docs/CONTRACTS_RUNTIME_SNAPSHOT_V1.md`: at most eight items, a 65,536-byte
frame, canonical UUIDv4 publisher epochs, positive 63-bit generations, valid
UTF-8, unique stable IDs, and a lifetime no longer than 30 seconds. Unknown or
duplicate fields, stale/rollback generations, retired epochs, invalid text,
late input sessions, timeouts, and unavailable/unsigned Runtime processes all
produce an empty ClipVault surface. They never affect Rime input.

Rime candidates and Runtime candidates are rendered as two distinct groups in
the non-activating candidate window. A click sends only the visible
`publisher_epoch/generation/candidate_id` to the trusted Host. The Host returns
the already-displayed bounded text over the existing local Engine pipe and the
TSF edit session inserts it directly; no system clipboard, Python callback,
query prefix, key stream, target App identity, surrounding text, or committed
body crosses the Runtime pipe. A valid item is consumed once and the complete
surface is wiped. Password, private/incognito, unknown, no-learning, focus-loss,
and disallowed contexts cancel the logical session, wipe its cache, and reject
late responses.

The combined application layout expected by the reciprocal process check is
`{app}\ClipVault.exe` and `{app}\ime\host-x64\ClipVaultImeHost.exe`. The isolated
IME package intentionally contains no `ClipVault.exe`; therefore its Snapshot
surface remains fail-closed until the owner-signed combined installer supplies
that exact Runtime layout. Tests use an explicit random pipe and current test
executable with signature enforcement disabled; this exception is not reachable
through production configuration.

The Host creates its per-session Pipe before initializing Rime. Dictionary
maintenance is a separate `--deploy-rime` installer/settings action. The
installer prewarms the Host at sign-in; Profile activation performs only a
bounded handshake. If the Host is still starting, the TSF client keeps a short
in-memory preedit buffer and replays it into the ready session. This buffer is
never logged, persisted, synchronized, or sent to ClipVault Runtime.

Every client handshake and RPC uses overlapped Named Pipe I/O with one absolute
deadline. Timeout cancels pending I/O, disconnects the session, and preserves an
existing editor preedit as literal text without replaying ambiguous commit,
candidate, or paging actions.

The DLL does not import Python, SQLite, librime, HTTP, WinHTTP, WinINet,
Winsock, sync, clipboard, or database code. Host failure retires the old
composition/session and permits only a plain unmodified letter to start a fresh
Host session; commit, selection, and other ambiguous operations are never
replayed. An ambiguous TSF edit result also retires the local session.

The echo engine remains only a deterministic recovery test mode. The Rime smoke
test deploys the canonical assets, executes
`nihao -> 你好 candidate -> stable candidate ID -> select -> commit`, verifies
Chinese punctuation, candidate paging when present, explicit cancel/commit,
and confirms the private schema does not create a user dictionary.

### Production build and smoke test

Install Visual Studio Build Tools with the C++ x64 workload and a Windows 10/11
SDK. Prepare the pinned official SDK and checked-out dictionary in external
caches, then build. `Prepare-ProductionRimeData.ps1` accepts only the seven
files and hashes in `shared-input/rime/RIME_ASSET_LOCK.json`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  windows/ime/scripts/Prepare-RimeSdk.ps1 `
  -CacheDirectory D:\external-cache

powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  windows/ime/scripts/Build-ProductionIme.ps1 `
  -RimeSdkDirectory D:\external-cache\librime-1.16.1-msvc-x64\extracted\dist `
  -RimeDictionaryDirectory D:\external-cache\rime-pinyin-simp-0c6861ef
```

This builds the x64 Host plus x64/x86 TSF clients with `/W4 /WX`, requires and
deploy-validates real Rime, runs the affected CTests, creates the isolated
package layout, and dry-runs registration scripts with `-WhatIf`. It does not
register anything. `Build-NativeSlice.ps1` remains a developer-only entry that
may build echo mode; release candidates must use `Build-ProductionIme.ps1`.

Local evidence on 2026-08-01: a clean `production-v6` Release build with MSVC
19.44 and Windows SDK 10.0.26100 passed x64 16/16 and x86 5/5 CTests under
`/W4 /WX /MT`. This includes exact Rime staging/deployment, eight clean first-run
cycles, two prepared schema-session pools, Runtime Snapshot vectors, canonical
CNG AEAD/tamper vectors, real current-user WinCred replay persistence, and real
local OTP offer/arm/consume Pipe exchanges. The isolated `package-v6` was
created, registration scripts passed `-WhatIf`, and the v2 Inno include
compiled with the real package. An additional Rime smoke
stress run passed 100/100 and the OTP Pipe test passed 20/20. Import inspection
confirmed that both TSF DLLs import no librime, CNG, WinCred, Python, SQLite or
network library; the Host imports neither CNG nor WinCred, while only the
separate broker imports CNG/Credential Manager. These binaries are still
unsigned, so the production Snapshot/OTP peer checks intentionally reject the
standalone package. This is not interactive TSF or broad application-
compatibility evidence.

All first-party CMake targets, including tests, use the static MSVC runtime.
The dependency audit inspected all 25 generated DLL/EXE artifacts and found no
`MSVCP`, `VCRUNTIME`, `CONCRT`, or API-set UCRT imports. The pinned official
`rime.dll` imports only `dbghelp.dll`, `KERNEL32.dll`, and `USER32.dll`.
Consequently the IME package has no external VC Redistributable prerequisite.
`Test-ProductionDependencies.ps1` is a mandatory production gate and is also
shipped in `scripts` for reproducible inspection.

Windows TSF registration is mixed-scope: this implementation stores the COM
server under HKCU, while `ITfInputProcessorProfiles::Register` stores the TIP
profile under HKLM. A reversible dry-run is:

```powershell
& windows/ime/scripts/Register-ClipVaultIme.ps1 `
    -PackageDirectory windows/ime/out/package -WhatIf

# Actual registration requires a separate, explicit system-wide authorization:
& windows/ime/scripts/Register-ClipVaultIme.ps1 `
    -PackageDirectory windows/ime/out/package `
    -AllowSystemWideTsfRegistration
& windows/ime/scripts/Unregister-ClipVaultIme.ps1 `
    -PackageDirectory windows/ime/out/package
```

Both scripts support `-WhatIf`, wait for both 64-bit `System32\regsvr32` and
32-bit `SysWOW64\regsvr32`, and share one sibling `host-x64` runtime. The
root installer includes `installer/ClipVaultImeV2.iss.inc` only when
`ClipVaultImeV2PackageDir` is explicitly defined, which switches that variant
to elevated registration mode. Its unchecked `clipvaultotpbroker` task invokes
the signed Broker enable script; uninstall always invokes the exact-path
disable script. The current lock does not authorize leaving a system-wide
profile installed.

## Frozen protocol properties

- Protocol version is `2` on every frame.
- Named Pipe transport uses a four-byte unsigned big-endian payload length and
  one exact serialized protobuf `Frame`, bounded to 1..1,048,576 bytes. Zero,
  truncated, oversized, and trailing-byte frames close the connection. Every
  new connection, including after Host restart, must complete exactly
  `ClientHello -> HostHello` before application frames. A ready connection
  accepts application frames but rejects another Hello; golden vectors mark
  every new pipe connection with an explicit boundary.
- A random `host_instance_id` is the Windows wire name for the shared
  contract's host epoch. A changed value invalidates every old session,
  revision, and candidate ID.
- New session requests use monotonically increasing `request_seq` values and
  responses echo `ack_request_seq`. An exact duplicate receives the identical
  cached response; a lower, unknown, or conflicting sequence fails closed.
- If a consumed response has expired before an identical retry arrives, the
  Host and TSF client retire the session together instead of returning a
  non-invalidating error that would leave their ledgers diverged.
- A syntactically valid strict-next request consumes its sequence even when its
  payload produces `INVALID_ARGUMENT`; that error is cached for exact retry so
  the client and Host cannot deadlock on different next sequence numbers. This
  includes a malformed Start sent against an already-live session. A malformed
  fresh Start returns an invalidating error and leaves a bounded, content-free
  failure tombstone, so the TSF client retires and a corrected reuse of the same
  sequence cannot silently create a Host-only session.
- A forward request gap retires and wipes the Host session while the TSF ledger
  retires the corresponding client view; preedit and candidate caches cannot
  survive on only one side.
- State-dependent mutation requests carry `expected_revision`; stale revisions
  and invalid/stale candidate IDs return explicit errors without changing
  state. `EndSessionRequest` is the exception: it accepts the strict next
  `request_seq` without a revision precondition and clears the session.
- `InputContext` carries platform, field kind, editor action, incognito,
  learning, ClipVault-surface permission, and an optional opaque local scope.
  Password and incognito contexts disable learning and ClipVault surfaces.
- All caret and segment offsets use UTF-16 code units. Non-empty segments use
  explicit RAW/CONVERTED/SELECTED kinds and continuously partition preedit.
- Candidate selection uses an opaque stable `candidate_id`, never plaintext,
  a page number, or an array index. IDs are scoped to one composition
  generation and cannot be reused by a later composition, even for the same
  logical candidate; the local model derives them with a per-Host keyed MAC.
  This protocol transports ENGINE candidates only;
  ClipVault/OTP/Inline Autofill surfaces remain separate.
- Candidate paging is explicit in both directions, and `EngineState.mode`
  distinguishes DIRECT, COMPOSING, SELECTING, and DISABLED.
- Set-option, commit, cancel, and end-session are distinct messages. An exact
  duplicate end request receives the same `SessionEnded` from a bounded,
  content-free tombstone.
- The TSF DLL must not load Python, SQLite, librime, or network code.

### TSF client response ledger

The Host returns a byte-equivalent cached `EngineState` for an exact duplicate
request. This is necessary when the first response was lost. The TSF client
must separately enforce at-most-once editor projection:

1. Accept responses only for the current host epoch and live session.
2. Keep bounded, strongly typed state per live `(host_instance_id, session_id)`.
   Because this protocol serializes requests and responses, the ledger accepts
   only the exact next `ack_request_seq`. A duplicate is ignored; a gap or
   out-of-order response retires the session fail closed.
3. Reserve a new sequence before applying `commit_text` through the TSF edit
   session. A cached or lower response updates nothing and must not insert text
   again. If the edit result is ambiguous, retire the session instead of
   blindly retrying the commit.
4. A different `ack_request_seq` is a different response and may commit new
   text normally.
5. On `SessionEnded`, reject further frames for that session and remove its
   ledger entries. On a new host epoch, reject all old-epoch frames, clear old
   sessions and their ledger entries, and start fresh sessions; never replay
   unconfirmed key streams.

The ledger stores only opaque IDs and sequence numbers. It never stores
`commit_text`, preedit, candidate text, or editor content.

The local Host model demonstrates explicit locally-authenticated acknowledgement
and an injected monotonic retry deadline. Retry identity uses a per-Host keyed
MAC (never an enumerable plain key digest); mutable MAC and response buffers are
wiped on acknowledgement, expiry, retirement, or restart. End tombstones contain
only opaque session/sequence metadata and the content-free response. This does
not authenticate a real Named Pipe peer or prove native memory zeroization.
Acknowledgements bind `(host_instance_id, session_id, ack_request_seq)`; a stale
Host epoch or sequence beyond the issued bound is rejected without clearing
cached responses. A wrong-Host request colliding with a live session ID retires
and wipes that Host session before returning an invalidating error.
`UPSTREAM_LOCK.json` therefore keeps both production native
integration and the real acknowledgement/deadline mechanism fail-closed under
`protocol_gate`.

## Executable slice and semantic mapping

`tools/engine_slice.py` remains a transport-neutral local simulation. The native
Host now additionally executes paging, explicit CommitComposition,
CancelComposition, stable-ID selection, and Host restart recovery; SetOption
and EndSession remain protocol/golden-vector coverage rather than complete
native lifecycle features.

| Foundation semantic ID | Executable conformance evidence | Static vector evidence |
|---|---|---|
| `ENG2-V001` | revision-zero session, identical Start retry, conflicting Start rejection, stable opaque selection ID, one commit | cached/conflicting Start and candidate select/commit sequence |
| `ENG2-V002` | stale revision and prior-composition ID have no editor effect | next/previous paging, stable page IDs, no cross-composition ID reuse |
| `ENG2-V003` | exact cached response, strict next-ack ledger, reserve-before-edit, at-most-once projection | duplicate commit response |
| `ENG2-V004` | ambiguous editor result retires the session and blocks replay | n/a |
| `ENG2-V005` | Host epoch restart clears client/Host state and rejects old ID | host restart scenario |
| `ENG2-V006` | surrogate-safe UTF-16 caret/segment validation | non-BMP preedit frames |
| `ENG2-V007` | Host and client both force learning/ClipVault surfaces off | password and incognito contexts |
| `ENG2-V008` | idempotent EndSession, bounded content-free tombstone, cache acknowledgement/deadline cleanup | duplicate SessionEnded cleanup |

## Offline validation

Run from the repository root:

```powershell
python spikes/windows-ime/tools/validate_bootstrap.py
python spikes/windows-ime/tools/run_engine_slice_conformance.py
python windows/ime/tools/validate_native_skeleton.py
```

All three commands use only the Python standard library. The bootstrap validator checks
the immutable root pins, fail-closed gates, `.proto` field numbers, frozen frame
schema, negative surrogate-split fixture, and complete golden-frame state
machine. The conformance runner executes the eight mapped `ENG2-V001` through
`ENG2-V008` tests plus nine sequence, recovery, acknowledgement, framing, and
handshake hardening tests (17 tests total) against the local Host/client model.
The native validator checks the build, pinned official SDK and canonical Rime
asset hashes, Rime adapter, candidate UI, Host restart, pipe security, TSF
edit-session, editor privacy classification, mixed-scope dual-architecture
registration, and forbidden TSF-DLL dependencies.

`--require-build-ready` is a deliberate hard gate. It currently fails while
the remaining protocol, compatibility, legal/NOTICE, architecture, and signing
gates remain unresolved:

```powershell
python spikes/windows-ime/tools/validate_bootstrap.py --require-build-ready
```

That failure is expected until transitive NOTICE/legal approval, ARM64,
application compatibility, installer/signing, and complete native
duplicate-response cache/acknowledgement gates finish. Passing Python validation
is not TSF registration or interactive application evidence; native CTest is
runtime evidence for the external Host boundary.

## Remaining production gates

1. Perform reversible HKCU registration and interactive Notepad/Chromium/Office/
   Terminal testing for compose, window placement, click/numeric select, page,
   commit, cancel, focus changes, and Host kill/restart.
2. Complete owner/legal approval and transitive license/NOTICE packaging for
   the locked official binary and `rime-pinyin-simp` dictionary.
3. Sign the combined Desktop/IME/Broker layout, provision a real CVPK pair,
   and record end-to-end OTP offer/prompt/armed-insert evidence across focus
   changes, lock/unlock, remote desktop, screen sharing, expiry, and replay.
4. Implement bounded native duplicate-response caching/acknowledgement cleanup,
   explicit EndSession, and installer-supervised Host lifecycle/update handoff.
5. Add ARM64 plus clean install/upgrade/uninstall matrices.
6. Run the signed installer on a VM without the VC Redistributable and record
   executable launch, TSF registration, Rime deployment and removal evidence.
7. Sign DLL/EXE/installer, run the application compatibility matrix and sustained
   daily-use soak, then seek the dedicated v2 release-gate approval.
