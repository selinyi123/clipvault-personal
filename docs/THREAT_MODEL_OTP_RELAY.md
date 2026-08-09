# OTP Relay Threat Model

> Status: required before real SMS/notification capture or Windows automatic insertion (2026-08-01).

## Assets and trust boundaries

Assets are the OTP plaintext, the one-time ability to consume it, paired-device keys and the bound target
input context. Trust boundaries are Android capture APIs, the Companion Runtime, transport/relay,
Windows Runtime, TSF DLL/UI, the foreground application and the operating-system lock/session state.

The SMS provider, notification contents, relay server, focused web page and arbitrary local process are
not automatically trusted.

## Threats and mandatory controls

| Threat | Required control |
|---|---|
| OTP enters durable clipboard/history | Separate type/protocol; no Clip/Room/SQLite/outbox conversion |
| Replay or duplicate delivery | Authenticated session epoch/sequence/event ID/nonce/sender/target/expiry, rotating session key, bounded current-session replay set, one consume |
| Replay after marker eviction | Bounded per-sender sequence high-water marks live for the whole authenticated session and never decrease |
| Wrong device receives OTP | Explicit paired target and authenticated E2EE envelope |
| Pairing is intercepted before E2EE starts | Derive OTP keys only from an authenticated, user-confirmed pair secret; require Tailscale or equivalent verified direct pairing for the v2.5 self-hosted lane |
| Sync bearer is observed on an ordinary LAN | Reject pair and relay on both endpoints unless HTTPS or a literal loopback/Tailscale address is used; never follow redirects |
| Android and Windows authenticate different bytes | Canonical OTP-3A binary AAD/KDF profile plus cross-platform golden vector; never authenticate locale JSON or default protobuf serialization |
| AES-GCM nonce is reused | CSPRNG 12-byte nonce, bounded per-session nonce history and fail-closed duplicate detection before encryption |
| Focus changes before insertion | Bind claim to TSF process/window/document context; revalidate immediately |
| Phishing page waits for OTP | Default explicit confirmation; auto-fill only after prior short-lived arming |
| Locked/shared/remote PC leaks code | Suppress presentation/insertion while locked; Windows TSF hard-denies OTP consumption and insertion whenever `SM_REMOTESESSION` is set; capture exclusion remains defense in depth only |
| Malicious local app reads clipboard | Never use system clipboard for the default insert path |
| TSF DLL compromises host app | No network, keys, database, Python or OTP transport in the DLL |
| Logs/dumps expose payload | Redacted representations, content-free logs, dump policy and no analytics payload |
| Offline delivery becomes durable | No ordinary outbox; expire and erase instead of replaying later |
| Revoked/lost computer remains trusted | Per-device revocation clears sessions, pending events and transport keys |
| Capture API exceeds user consent | Feature-specific opt-in, visible state and platform/distribution review |
| Secret use races with expiry/revoke | No escaping store view; atomic one-use callback lease; wipe store payload before callback and lease in `finally`; platform capture grants use a process-local generation and repeat live-grant checks before the final send |
| Cleanup scheduler or clock fails | Guard every use by monotonic expiry; schedule nearest deadline; clock regression wipes and closes the session store |
| User content smuggled into metadata | Generate or strictly validate opaque identifiers; forbid message/code text in IDs and structured diagnostics |
| One target starves other paired devices | Enforce independent per-target live-event and replay-marker quotas in addition to global bounds |
| ACK for event A completes event B | Per-event non-transferable completion capability bound to pending metadata, owned envelope and authenticated ACK receipt |
| Synthetic crypto is accidentally shipped | Production packages physically exclude synthetic providers; a platform-specific factory accepts exact reviewed provider types. Python private names, registries, object tokens and mutable booleans are not approval boundaries |

## Android capture gates

1. Inline Autofill presentation is the first path and does not authorize plaintext relay.
2. `CompanionDeviceManager` association establishes a device relationship but does not itself grant SMS
   access or create the data connection.
3. SMS permissions, AutofillService role and Notification Listener are separate capabilities. Each needs
   an explicit user flow, Android 13/15/17 compatibility evidence and distribution-policy review.
4. The IME package must not request SMS or network permissions. Plaintext parsing, if later approved,
   occurs only in the Companion Runtime and never forwards the complete message body.
5. Android 15 redacts detected OTP notification content from untrusted notification listeners. A trusted
   companion association is an exemption, but the exact SMS/RCS app and OEM behavior still requires device
   evidence; notification parsing is a fallback, not the sole production capture path.
6. Android 17 with target API 37 delays standard OTP SMS broadcasts/provider visibility for most apps by
   three hours. Connected-device companion apps are listed as exempt, but ClipVault must prove that exemption
   on a real Android 17 device before claiming automatic capture; otherwise it falls back to SMS User Consent
   or explicit forwarding instead of relaying an expired code.
7. Google Play lists connected-device companion and cross-device SMS transfer as possible restricted-SMS
   exceptions, subject to declaration and review. `READ_SMS`/`RECEIVE_SMS` may ship only after that approval;
   sideload success is not Play-policy evidence.

Current authoritative references:

- [Android 15 OTP notification redaction](https://developer.android.com/about/versions/15/behavior-changes-all#otp-redaction)
- [Android 17 OTP protection](https://developer.android.com/about/versions/17/behavior-changes-17#otp-protection)
- [Google Play SMS/Call Log permitted uses and exceptions](https://support.google.com/googleplay/android-developer/answer/10208820)

The release-declaration checklist is maintained in [PLAY_SMS_PERMISSION.md](PLAY_SMS_PERMISSION.md).

## Windows insertion gates

1. A non-activating prompt must preserve the original TSF text context.
2. Direct insertion uses a valid TSF edit session. Synthetic keyboard input is an explicit fallback only
   and cannot be called “reliable” across integrity levels.
3. Armed insertion checks lock state, expiry, process, window and document/context identity and never
   presses Enter.
4. Killing the external Runtime must not crash or hang the host application.

## Residual risks and honest claims

- Relaying an OTP extends the possession factor from the phone to the paired computer.
- Managed runtimes, paging, OS APIs and crash capture mean absolute zero-memory/zero-trace guarantees are
  impossible. The product guarantee is no application-level persistence or content logging.
- An online-only event may be lost on process death, network failure or target unavailability.
- The process-local replay set is not durable replay protection. Authenticated transport session epochs
  and key rotation reject old-session envelopes after reconnect or restart.
- The v2.5 self-hosted key profile derives from an independent random verifier minted by the Desktop OTP
  pair authority and imported once into each endpoint's platform-protected store. The legacy sync bearer
  only authenticates that ceremony and is not key material. Compromise of either paired endpoint or its
  protected pairing state therefore compromises that pair's OTP channel; it does not expose another
  pair. An unauthenticated or ordinary-LAN cleartext pairing ceremony is outside the E2EE claim.
- The v2 HTTP lane temporarily reuses the sync bearer for request admission. It is therefore fail-closed
  to HTTPS or literal loopback/Tailscale endpoints on Android and Desktop. A future OTP-only bearer/hash
  narrows the impact of endpoint or proxy mistakes but does not replace transport confidentiality.
- Per-event in-process capabilities prevent accidental ACK/event mix-ups. They do not defend against
  arbitrary code execution or reflection inside the trusted Runtime process; such access is already an
  endpoint compromise and must instead be limited by package/process boundaries and code review.
- The local core receives a monotonic expiry derived once from authenticated wire timestamps; monotonic
  values are not comparable across devices and must never be accepted directly from an untrusted peer.
- The secret-use callback is a trusted boundary. Running it outside the store lock preserves cleanup and
  revocation progress, but the core cannot forcibly stop a blocked callback or prevent a malicious sink
  from copying plaintext; production sinks must be bounded and narrowly reviewed.
- Some apps do not correctly identify OTP fields; platform success cannot be promised universally.
