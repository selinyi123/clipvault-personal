# ClipVault OTP Relay Contract

> Status: v2.4 lifecycle frozen; v2.5 AES-GCM wire profile frozen for platform implementation
> (2026-08-01). OTP Relay is not clipboard sync.

## OTP-1 — Data classes

```text
PermanentSecret
  password, token, private key, API key
  never candidate / sync / persist

EphemeralOtp
  event_id, nonce, target_device_id, code, kind, masked_source,
  issued_at, expires_at, confidence
  dedicated online-only relay and temporary UI

NormalContent
  clipboard / Personal Memory / explicit saved content
  existing persistence and sync rules
```

An `EphemeralOtp` must never be converted to a `Clip`, Memory item or ordinary sync event.

## OTP-2 — Lifetime

```text
CAPTURED -> READY -> DELIVERED -> PRESENTED -> CLAIMED -> IN_USE -> CONSUMED -> ACKED
     |         |          |           |          |
     +------> REJECTED    +---------> EXPIRED    +----------------> DISMISSED
any non-terminal state -> REVOKED
```

- Default TTL: 120 seconds, measured using a monotonic clock locally.
- Terminal states erase the payload immediately.
- Every secret-use path rechecks monotonic expiry. The host must also schedule `expire_due` at the
  nearest deadline returned by the store; a core without that scheduler is PoC-only, not production-ready.
- Wall-clock timestamps are display/protocol metadata and cannot extend local monotonic expiry.
- Capacity is bounded. Overflow fails closed and leaves existing live events untouched; it never evicts
  a credential merely to accept another one and never writes a disk queue. Independent per-target live
  and replay quotas prevent one paired device from exhausting the shared store.
- An event is claimable/consumable at most once. Session epoch, sequence, event ID, nonce, sender and
  target form the authenticated replay identity. The in-process replay set is only a bounded current-
  session guard; reconnect or process restart rotates the authenticated session epoch and key and rejects
  envelopes from the old epoch.
- Each authenticated sender has a strictly increasing positive sequence and a bounded session-lifetime
  high-water mark. Sweeping short replay markers, dismissing, consuming or clearing events never lowers
  that mark; a duplicate/older sequence stays rejected until the authenticated session is replaced.
- A monotonic-clock regression erases all live payloads, permanently closes that store instance and
  requires a fresh session epoch.

## OTP-3 — Envelope

```text
OtpRelayEnvelope
  protocol_version: 1
  event_id: opaque random identifier
  session_epoch
  sequence
  sender_device_id
  target_device_id
  issued_at_ms
  expires_at_ms
  nonce
  ciphertext
  authentication_tag
```

All identity, epoch, sequence, target and expiry fields are authenticated by the envelope; they are not
mutable unauthenticated routing hints. Only paired target devices may decrypt. The relay may hold an online, TTL-bounded ciphertext in memory,
but no ClipVault component persists the payload/envelope to SQLite, Room, files, ordinary outbox, crash
reports or analytics. Expired envelopes are never delivered after reconnect.

Wire `issued_at`/`expires_at` are authenticated before admission. The transport adapter derives one local
absolute monotonic deadline exactly once and passes the same value through add/claim/use; a retry cannot
refresh a relative TTL. A local monotonic value is never serialized as a cross-device clock.

### OTP-3A — v2.5 canonical AEAD profile

The first production pair channel uses **AES-256-GCM**, a 12-byte random nonce and a 16-byte tag. The
same `(key, nonce)` pair must never be reused. Android must use the platform JCA/Keystore implementation;
Windows must use CNG or another separately approved platform provider. The synthetic HMAC/XOR PoC is
never a production provider.

For the current self-hosted pair lane, both endpoints derive `pair_verifier` as the 32 raw bytes of
`SHA-256(pair_secret UTF-8)`. The Desktop already stores the hex encoding of this verifier rather than
the bearer secret; Android computes the same digest before deriving OTP keys. The raw pair secret and
verifier are never sent to a relay. Pairing must occur over an authenticated user-confirmed channel
(Tailscale or an equivalent verified direct path); an unauthenticated cleartext pairing exchange cannot
be cited as v2.5 E2EE evidence.

Key derivation is HKDF-SHA256 for a single 32-byte block:

```text
salt = SHA-256("ClipVault OTP Relay KDF v1\0" || session_epoch_uuid_bytes)
prk  = HMAC-SHA256(salt, pair_verifier)
info = "ClipVault OTP Relay key v1\0" || sender_uuid_bytes || target_uuid_bytes
key  = HMAC-SHA256(prk, info || 0x01)
```

The GCM additional authenticated data is exactly this byte sequence, with no JSON, locale, platform
endianness or protobuf serialization involved:

```text
UTF-8("ClipVault OTP Relay AEAD v1") || 0x00
|| uint8(protocol_version=1)
|| session_epoch UUID (16 RFC-4122 network-order bytes)
|| event_id UUID (16 RFC-4122 network-order bytes)
|| sender device UUID (16 bytes; strip canonical "device:" prefix)
|| target device UUID (16 bytes; strip canonical "device:" prefix)
|| sequence uint64 big-endian
|| issued_at_unix_ms uint64 big-endian
|| expires_at_unix_ms uint64 big-endian
```

Plaintext is only the normalized 4–8 ASCII OTP digits. Routing fields may be parsed before decryption
only to select the bounded paired-device record; none is trusted until GCM authentication succeeds.
The receiver rejects a noncanonical UUID, zero/overflow sequence, invalid time window, reused nonce,
wrong target/session, tag failure or ciphertext outside the OTP length bound before local admission.

`contracts/vectors/otp_aead_v1.json` is normative. Android and Windows must reproduce its HKDF, AAD,
ciphertext and tag bytes exactly, then pass one-bit tamper tests for every authenticated field. A
platform-specific production factory may construct the pair session only from exact reviewed provider
types after those vectors and platform key-storage tests pass. A Python-private registry, object
capability or mutable `production_ready` flag is not an approval boundary.

## OTP-4 — Operations

```text
offer(event) -> ACCEPTED | DUPLICATE | REJECTED | EXPIRED
present(event_id, target_device_id) -> PRESENTED | NOT_FOUND
claim(event_id, nonce, claim_context) -> claim_id | DENIED
use_secret(claim_id, consumer) -> CONSUMED | ALREADY_CONSUMED | EXPIRED
ack(event_id, target_device_id) -> ACKED | NOT_FOUND
dismiss(event_id) -> DISMISSED
revoke_target(target_device_id) -> count
expire_due(now_monotonic) -> count
next_expiry_deadline() -> monotonic deadline | NONE
clear_all(reason) -> count
```

`claim_context` is local-only and binds the Windows TSF process/window/document context or the Android
Autofill/IME session. `use_secret` is the only plaintext access API: it atomically removes and wipes the
store-owned payload, invokes the consumer with one short-lived mutable lease, then wipes that lease in a
`finally` path. The callback runs outside the store's global lock so a blocked or reentrant consumer cannot
stall expiry, revocation or unrelated targets. It never returns a `memoryview` or byte buffer. Concurrent
ACK, dismiss, revoke and expiry cannot modify the detached lease while it is in use. A consumer must be a
bounded trusted sink and must not retain or copy the lease beyond the callback; the core cannot forcibly
terminate a blocked callback or prevent a malicious sink from copying plaintext.

The core represents `claim_context` as a strong sink kind plus a canonical
opaque context token. The platform adapter owns the mapping from that token to
the current process/window/document or Autofill session. The same context must
be presented again at `use_secret`; a changed or stale context fails closed
before a plaintext lease is created.

Submitting a valid mutable payload transfers ownership to the store. The caller buffer is wiped on both
acceptance and rejection. Event, claim, session and device IDs are generated/validated opaque tokens;
free-form message text or OTP digits are forbidden in metadata fields. Sender
and target identifiers use one canonical random device-ID format rather than
accepting user labels, phone numbers or arbitrary ASCII tokens.

Clock reads used by the store and its coordinator are serialized. A maintenance
operation that expires payloads also returns the corresponding live metadata in
the same clock/lock step, so coordinator-owned nonce material is wiped before
that operation returns; it cannot report “no next deadline” while retaining an
expired nonce.

## OTP-5 — Platform presentation

- Android system Inline Autofill is a separate protected system surface. The IME does not scrape its View
  for plaintext.
- Any ClipVault plaintext capture lives in the Companion Runtime and exposes only the minimum OTP object
  to the IME/relay.
- Windows uses a non-activating prompt and explicit TSF insertion by default.
- “Armed auto-fill” requires a short-lived prior user claim bound to process, window and text context.
  Focus alone is insufficient. ClipVault never simulates Enter or submits the form.

## OTP-6 — Logging and diagnostics

Forbidden: code, ciphertext, nonce, full sender/source, message body, target field contents.

Allowed: truncated one-way event-ID digest, state transition name, numeric TTL/latency, payload length,
opaque target alias ID and error code. Diagnostic objects, structured serialization and
`repr`/`toString` must redact payloads.

## OTP-7 — Required vectors

The isolated core maps its implementation-specific tests to the canonical
semantic IDs `OTP-V001` through `OTP-V010` in
`contracts/vectors/input_foundation_v2.json`. Platform capture and presentation
tests are additive; they cannot weaken these core cases.

1. exactly one successful consume and all later attempts fail;
2. duplicate `event_id + nonce` rejected without replacing the first payload;
3. wrong target, wrong nonce and stale claim rejected;
4. monotonic expiry despite wall-clock changes;
5. capacity exhaustion rejects the new event without altering existing payloads;
6. dismiss, revoke, lock/process-stop cleanup;
7. offline target causes expiry, not ordinary outbox insertion;
8. diagnostics, structured serialization and object representation contain no OTP digits/ciphertext;
9. use/ACK/dismiss/expiry races never expose a partially wiped payload or consume twice;
10. clock regression wipes and closes the store; rejected offers wipe the transferred input buffer;
11. old session epochs, replay-window boundaries and process-restart envelopes are rejected;
12. after replay-marker expiry, the same or lower sender sequence remains rejected; another authenticated
    sender has an independent bounded high-water mark; retry never extends original expiry.
