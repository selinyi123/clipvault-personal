# ADR-0016: OTP Relay as an ephemeral credential channel

Status: **Accepted for isolated PoCs; platform capture and distribution path remain gated** (2026-08-01)

## Context

An OTP is neither a durable clipboard item nor a permanent secret. Treating it as an ordinary clip would
place it in history, search, sync outbox, backup or learning. Blindly injecting the next OTP into whichever
window has focus also creates a phishing and focus-race hazard.

## Decision

1. Define `EphemeralOtp` as a separate in-memory type and `OtpRelayEnvelope` as a separate protocol.
   Neither may enter Room/SQLite, clipboard history, ordinary sync/outbox, Obsidian, GitHub, search,
   Personal Memory, analytics, crash payloads or content logs.
2. The default TTL is 120 seconds. Events are bound to one target device and one authenticated transport
   session epoch, deduplicated by sequence/event ID/nonce/sender/target, consumed at most once and erased
   on consume, dismiss, revoke, expiry, lock or process stop. A clock regression erases and closes the
   current store.
3. v2.4 is a local/synthetic PoC: the memory core and Android/Windows platform surfaces are validated
   independently and do not transmit plaintext between devices. Cross-device OTP is a v2.5 Beta only
   after explicit pairing, authenticated E2EE envelopes, revocation and replay gates pass. There is no
   offline replay or durable relay queue. Device identities, public keys and user settings may persist;
   OTP payloads may not.
4. Android first supports system Inline Autofill presentation without reading suggestion plaintext. The
   default Companion Runtime also supports a user-started, target/TTL-bound SMS User Consent session for
   one system-confirmed message without SMS permissions. Automatic `RECEIVE_SMS` capture remains isolated
   to the separately reviewed build lane. Plaintext capture belongs to the Companion Runtime, never the IME package.
5. Windows defaults to a non-activating prompt plus explicit insertion through the active TSF context.
   Automatic insertion is allowed only after a short-lived user-created claim binds process, window and
   text context. It never presses Enter or submits a form.
6. Plaintext is exposed only through a bounded one-use callback lease. The store never returns an
   escaping secret view; host cleanup scheduling is mandatory before production integration.
7. v2.5 pair transport uses the canonical OTP-3A AES-256-GCM/HKDF-SHA256 profile and
   `contracts/vectors/otp_aead_v1.json`. Android JCA and Windows CNG must reproduce the same bytes.
   Synthetic crypto is physically excluded from production; Python private state, registries and
   mutable readiness flags are not cryptographic or approval boundaries.

## Consequences

- OTP expiry or an offline target can cause loss; this is an intentional consequence of no persistence.
- Cross-device relay expands the possession factor to an explicitly paired computer and must be opt-in,
  per-device revocable and disabled while the computer is locked.
- Java/Kotlin/Python/Windows memory and operating-system internals prevent an absolute “zero trace” claim;
  the enforceable promise is that ClipVault application code does not persist the OTP payload.

## Related

- [CONTRACTS_OTP_RELAY](../CONTRACTS_OTP_RELAY.md)
- [THREAT_MODEL_OTP_RELAY](../THREAT_MODEL_OTP_RELAY.md)
- [ADR-0013](0013-cross-platform-input-process-boundary.md)
