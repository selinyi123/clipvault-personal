# Runtime Snapshot Contract V1

Status: frozen for the ClipVault v2 daily-use implementation.

This is the bounded local contract by which the data-owning Runtime makes already
authorized Personal Memory and clipboard candidates available to a no-network
IME process. It is not Engine Protocol V2, a search API, a typed-text channel,
an OTP channel, or a persistence format.

## 1. Privacy invariants

- The IME/Host never sends keys, preedit, surrounding text, selected text,
  target-document text, query prefixes, window titles, URLs or account identity
  to the Runtime.
- Runtime applies current Secret Guard and deletion/quarantine policy before an
  item enters a response. A persisted historical `is_secret=false` flag is not
  sufficient authorization.
- Password, incognito, no-personalized-learning, unknown-sensitive and configured
  sensitive-app contexts set `clipvault_allowed=false`. The client cancels pending
  requests, wipes the current snapshot and rejects late generations.
- Snapshot items never contain OTP, passwords, API keys, private keys, access
  tokens or an unfiltered `risk_flags` escape hatch.
- Selection commits the already-displayed text locally. It does not report the
  target App, surrounding text or committed body to Runtime.
- Runtime/IPC failure returns an empty ClipVault surface and never affects Rime,
  Direct input, system Inline Autofill or editor actions.

## 2. Bounds

```text
protocol_version                  1
maximum response items            8
candidate_id UTF-8 bytes          1..128
label UTF-8 bytes                 0..64
text UTF-8 bytes                  1..16,384
total encoded response bytes      <=65,536
request deadline                  <=250 ms
snapshot lifetime                 <=30 s
```

Oversized items are excluded, not silently truncated: clicking a visually
truncated preview must still commit exactly the original authorized item. The UI
may shorten only its rendering while retaining the bounded full item in memory.

## 3. Identity and generations

Every Runtime process creates a random UUIDv4 `publisher_epoch`. Every accepted
snapshot increments a positive 63-bit `generation`. Candidate IDs are opaque and
scoped to `(publisher_epoch, generation)`.

A client records the request ID, input-session generation and current
`clipvault_allowed` state. A response is usable only when all three are still
current. Runtime restart changes `publisher_epoch`; client restart or input
context change invalidates every pending callback. Generation rollback, duplicate
candidate IDs and duplicate frame fields fail closed.

## 4. Android Binder mapping

The isolated IME binds by explicit component to the Runtime's signature-level
permission. Its request contains only:

```text
request_id
input_session_generation
limit (1..8)
```

The response contains `publisher_epoch`, `snapshot_generation`, the echoed
request/session IDs and parallel candidate records. Binder death, timeout,
mismatched array lengths, an invalid field, an oversized aggregate or a late
generation yields an empty surface. The IME package still declares no network,
SMS, notification-listener or clipboard-capture permission.

## 5. Windows Named Pipe mapping

Windows uses a separate per-user local pipe owned by the Python Desktop Runtime.
The x64 IME Host fetches a snapshot asynchronously when an allowed context starts
and caches only the validated response. The TSF DLL never connects to Python.

The Desktop production composition keeps this surface explicitly disabled by
default. It creates the worker and pipe only with the following configuration:

```toml
[ime_snapshot]
enabled = false
host_path = ""
require_signed_host = true
```

When enabled, `host_path` is a required local-drive absolute Windows path to the
expected IME Host executable. UNC, extended (`\\?\`), device (`\\.\`) and
relative namespaces fail closed before startup. `require_signed_host=true` is the
release setting; disabling the signature requirement is an explicit
local-development exception and is not v2 daily-use acceptance evidence. A
disabled surface creates neither a worker thread nor a Named Pipe.

The optional worker creates and closes its SQLite connection inside its own
thread. Pipe creation, Host verification, database or protocol failures record
only a content-free degraded error and retry this surface. They never request
global Runtime shutdown and never stop API service, clipboard capture, sync or
Rime input.

The production pipe name is:

```text
\\.\pipe\ClipVaultRuntimeSnapshotV1-<ProcessIdToSessionId>
```

The name is only a locator, never an authentication secret. Tests may append a
random namespace suffix, but production builds do not accept an environment
override for the pipe name.

Security descriptor: current interactive user and SYSTEM only. The server sets
`PIPE_REJECT_REMOTE_CLIENTS`; the client verifies the server process belongs to
the current user and the expected signed installation before accepting content.
All I/O is overlapped/cancellable and bounded by the 250 ms deadline.

Frames use a four-byte unsigned big-endian payload length followed by the same
strict protobuf-wire subset already implemented by the Windows Engine V2 slice.
Payloads over 65,536 bytes, duplicate singleton fields, unknown fields, invalid
UTF-8, unsupported wire types and trailing bytes close the connection.

Message type is fixed by connection state rather than an attacker-controlled
`kind` field: the client sends one `ClientHello`, the server sends one
`HostHello`, then exactly one `SnapshotRequest` / `SnapshotResponse` pair is
exchanged before the server disconnects. A message in the wrong state or a
request-id mismatch closes the connection. One absolute 250 ms deadline covers
the complete hello, request and response exchange. Reconnection and refresh are
asynchronous Host work and never occur on the TSF key path.
After the response write completes, the server flushes the local pipe before
disconnecting; otherwise a client can observe a valid four-byte length prefix
followed by `ERROR_PIPE_NOT_CONNECTED` before the buffered payload is readable.

### ClientHello (`kind=1`)

```text
1 protocol_version uint = 1
2 client_instance  bytes = canonical UUIDv4 ASCII
```

### HostHello (`kind=2`)

```text
1 protocol_version uint = 1
2 publisher_epoch bytes = canonical UUIDv4 ASCII
```

### SnapshotRequest (`kind=3`)

```text
1 request_id uint64, 1..2^63-1
2 limit      uint32, 1..8
```

### SnapshotResponse (`kind=4`)

```text
1 request_id       uint64
2 publisher_epoch  bytes
3 generation       uint64, 1..2^63-1
4 expires_at_ms    uint64
5 item             repeated embedded message
```

Item message:

```text
1 candidate_id bytes
2 source       uint: 1=memory, 2=clipboard
3 label        bytes
4 text         bytes
```

The local pipe carries no bearer token, network credential, database key or OTP.
Authentication is the OS identity, expected executable/install identity and pipe
ACL; confidentiality does not rely on an attacker-controlled pipe name.

## 6. Selection and UI

Rime candidates, system Inline Autofill and ClipVault snapshot candidates remain
separate surfaces. A selection is accepted only if its visible
`publisher_epoch/generation/candidate_id` still matches the current cache. A
snapshot refresh must not change what an already-rendered click commits.

On Windows, the TSF client receives selected text from its trusted x64 Host over
the Engine/local UI channel and inserts it inside the current edit session. On
Android, the isolated IME commits the current Binder snapshot item through the
current `InputConnection`. Neither path uses the system clipboard.

## 7. Golden assertions

`contracts/vectors/runtime_snapshot_v1.json` freezes the following cases:

- `SNAP-V001`: bounded allowed response is accepted;
- `SNAP-V002`: password/incognito/sensitive context makes the surface empty;
- `SNAP-V003`: late request/session generation is discarded;
- `SNAP-V004`: publisher epoch change invalidates every old ID;
- `SNAP-V005`: oversized item or aggregate is rejected;
- `SNAP-V006`: duplicate ID/field, invalid UTF-8 or generation rollback rejects;
- `SNAP-V007`: Runtime/pipe/Binder timeout leaves engine input operational;
- `SNAP-V008`: selection commits once locally and sends no typed context back.
