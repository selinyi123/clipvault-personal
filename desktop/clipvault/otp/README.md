# OTP Relay executable integration slice

This package implements the process-local OTP lifecycle and an executable
synthetic Android-to-Windows relay. The synthetic crypto flow remains isolated
from ordinary clipboard capture, `sync_outbox`, SQLite, files, HTTP, and
sockets. The Desktop Runtime now also has a separate, strictly-online HTTP
ingress that accepts only opaque platform-AEAD envelopes and never decrypts or
persists them in Python.

The runnable flow is:

```text
restricted capture port
  -> isolated owned candidate
  -> strict 4-8 digit normalization
  -> synthetic encrypted/authenticated envelope
  -> OTP-only in-memory transport
  -> authenticated receiver admission
  -> delivery ACK / duplicate retry handling
  -> WindowsOtpInsertPort.insert_at_selection
  -> single-use destruction
```

`SyntheticOtpCaptureAdapter`, `SyntheticOtpPairChannel`, and
`InMemoryOtpTransport` exist for deterministic integration tests. They do not
claim Android SMS access, a real network connection, or production E2EE.

## Production fail-closed boundary

`OtpPairChannelPort` is the integration contract for the future platform
channel. Until real CNG/JCA-backed channel and online transport implementations
exist, public `OtpRelayProducer` and `OtpRelayReceiver` construction always
fails closed and directs callers to a future reviewed platform factory. There
is no Python readiness flag, provider registry, or private-token mechanism
presented as a production trust root. Tests import explicitly named composers
from `clipvault.otp.testing`; the package root exports no synthetic channel,
capture adapter, transport, base composer, factory, provider capability, or
completion receipt.

The synthetic HMAC-based encrypt-then-MAC construction is not a production
AEAD and must not be enabled for cross-device daily use. No critical crypto
dependency was added in this slice.

## Opaque Desktop ingress

`POST /api/otp/relay` is the only remotely allowlisted OTP route. It requires
an already-paired bearer, accepts at most 4 KiB of strict JSON, validates fixed
protocol/time/routing fields and canonical base64url encoding, then performs
one synchronous handoff through `OtpOpaqueIngressPort`. It never enters the
ordinary sync push/pull API, `sync_outbox`, clipboard pipeline, database, file
queue, or offline retry path. If the local Windows broker is unavailable, the
request returns 503 and the envelope is destroyed instead of queued.

Legacy sync IDs and human-readable device names are not OTP AAD identities.
The authenticated sync peer must first resolve through an injected
`OtpPairIdentityPort` to an OTP-only sender/target pair, both formatted exactly
as `device:<canonical UUIDv4>`. The default identity resolver and broker port
are disabled. A future reviewed Windows adapter must use a per-user
authenticated Named Pipe, keep CNG keys outside Python, and authenticate every
metadata field as AES-256-GCM associated data before exposing plaintext to the
Windows OTP broker. The JSON wire shape is frozen by
`contracts/otp_relay_wire_v1.schema.json`: it carries a positive signed-64-bit
`sequence`, a 12-byte nonce, 4–8 ciphertext bytes and a separate 16-byte
authentication tag. It has no unauthenticated `key_id` routing hint.

Canonical AAD is frozen by `OTP-AEAD-V001` as the exact prefix
`ClipVault OTP Relay AEAD v1\0`, followed by `>B16s16s16s16sQQQ`: protocol
version, session UUID, event UUID, sender UUID, target UUID, sequence, issue
time and expiry time. The HTTP-facing algorithm label is fixed to `A256GCM`;
algorithm negotiation is not supported in protocol v1.

Python validates routing metadata and canonical wire encoding only. It does
not perform AEAD encryption, authentication, or decryption. The response is
limited to `status` plus a SHA-256 event-id hash; it never echoes event IDs,
ciphertext, nonce, or plaintext. Request, nonce, and ciphertext bytearrays are
overwritten after the synchronous handoff, and both injected ports close with
the API/Runtime lifecycle. As elsewhere in this package, Python clearing is a
best-effort application boundary rather than an OS memory-erasure guarantee.
Each broker call receives a real monotonic deadline. A conforming production
adapter must keep the API wait bounded; after a reported timeout the broker
gate is poisoned and later envelopes are discarded. There is no daemon-thread
fallback or background Python OTP copy: the reviewed Named Pipe adapter must
enforce the deadline synchronously with overlapped I/O and `CancelIoEx`, and
its concurrent `close()` must cancel any active call.
Until that adapter exists, the default port remains disabled. Repeated rejection
logs are rate-limited per content-free security code.

The shipped platform boundary is intentionally an adapter, not fabricated OS
capability:

- `OtpCapturePort` may return only an already isolated candidate;
- `OtpCaptureAuthorization` must match source, session, sender, target, grant
  and expiry; the channel rechecks all pair identities before encryption;
- synthetic capture requires an explicit user action;
- an Android source may run automatically only when its grant explicitly
  enables automatic capture;
- no implementation in this branch reads SMS, notifications, clipboard, or
  typed text.

## Data ownership and ACK semantics

Candidate, nonce, envelope, ACK, and plaintext lease buffers are ownership
transfer objects. Every success/error/expiry/close path overwrites the buffers
owned by that layer. Python overwrite is application-level best effort and
cannot guarantee removal from interpreter internals, paging, crash dumps, or
the final target application.

The transport ACK means that the receiver authenticated the envelope and
admitted the OTP into its bounded process-local store. It does not mean that a
website accepted the code. If an ACK is lost, the exact envelope retry returns
a new authenticated ACK without admitting plaintext a second time. A modified
envelope, conflicting event identity, stale sequence, invalid target, expired
deadline, or bad ACK fails closed.

ACK verification returns an immutable, channel-bound completion receipt but
does not yet remove sender state. The transport atomically matches that receipt
to its owned envelope, wipes the envelope, and only then may the channel retire
the pending sender record. A transport failure therefore remains retryable.
The per-event Python object capability prevents accidental cross-event receipt
mix-ups inside this synthetic harness; it is not an OS security boundary.
Arbitrary same-process code execution or reflection can inspect or forge Python
objects and is treated as full Runtime compromise.

The sender and receiver retain only bounded metadata for pending ACK and replay
receipts. `InMemoryOtpTransport` owns encrypted envelopes until verified ACK,
expiry, explicit discard, or close. It never falls back to the ordinary sync
outbox, so an offline or restarted process may lose an OTP by design.

## Local store and sink obligations

1. Create a fresh canonical UUID-v4 `session_epoch` after pair/session
   authentication. Do not reuse it after restart or re-authentication.
2. Represent devices as canonical `device:<uuid-v4>` identities and use a
   strictly increasing positive sender sequence.
3. Authenticate wire issue/expiry fields before deriving one receiver-local
   monotonic deadline. Never refresh that deadline on retry.
4. Schedule `OtpRelayCoordinator.expire()` at
   `next_deadline_monotonic()`; lazy checks prevent stale use but an idle host
   still needs scheduled cleanup.
5. Bind each claim to an `OtpClaimContext` with a typed sink and opaque UUID-v4
   context token. Revalidate that context immediately before direct insertion.
6. Keep plaintext callbacks synchronous and bounded. The store wipes its
   stored value before invoking the callback and wipes the temporary lease in
   `finally`.
7. Revoke targets and close channel, transport, coordinator, and platform
   capture adapters when pairing/session authority ends.

`WindowsOtpConsumer` accepts only `WindowsOtpInsertPort`. The interface exposes
`is_context_current` and `insert_at_selection`; it has no clipboard method and
does not expose a generic keystroke-injection fallback. The concrete TSF/CNG
implementation remains a Windows-platform deliverable.

## Focused semantic coverage

The original Foundation semantics remain covered by `test_otp_relay.py` and
`test_otp_coordinator.py` (`OTP-V001` through `OTP-V010`). The integration slice
adds:

| ID | Covered behavior |
|---|---|
| `OTP-V011` | authorized capture -> encrypted delivery -> ACK -> TSF direct use |
| `OTP-V012` | exact live capture grant and authorized automatic platform adapter |
| `OTP-V013` | production constructors always reject until platform factory exists |
| `OTP-V014` | ciphertext/tag tamper is discarded before local admission |
| `OTP-V015` | lost-ACK retry is idempotent and never re-admits plaintext |
| `OTP-V016` | tampered/cross-event ACK cannot retire sender or transport state |
| `OTP-V017` | stale Windows context cannot lease or destroy a pending OTP |
| `OTP-V018` | unacknowledged transport expiry wipes envelope buffers |
| `OTP-V019` | OTP modules import no persistence, clipboard, sync, or network layer |
| `OTP-V020` | complete relay leaves the ordinary `sync_outbox` untouched |

Run the focused slice with:

```powershell
cd desktop
python -m pytest -q tests/test_otp_relay.py tests/test_otp_coordinator.py tests/test_otp_production_slice.py
```

## Remaining platform gates

- Android companion/SMS Code Autofill or approved SMS permission adapter and
  device tests;
- reviewed AEAD/key-agreement implementation using platform-protected keys;
- real LAN/Tailscale/relay transport with memory-only TTL behavior;
- Windows TSF `InsertTextAtSelection` adapter, focus/lock-screen checks and CNG;
- signed Android/Windows artifacts and manual two-device security QA.

Until those gates pass, this is a production-shaped executable slice, not a
daily-use cross-device OTP release.
