# Android OTP Relay production slice

Status: implementation-ready internal lane; Google Play restricted-SMS approval and physical-device
evidence remain Owner/platform gates.

## Package and permission boundary

- `ime-app` never requests Internet, SMS, or notification-listener authority.
- Runtime `debug` and `release` never request `RECEIVE_SMS` or `READ_SMS`.
- Only Runtime build type `otpSmsRelay` merges
  `app/src/otpSmsRelay/AndroidManifest.xml`, which contains `RECEIVE_SMS` and the short-lived
  `ApprovedSmsOtpReceiver`.
- Lint, compilation, and unit tests for `otpSmsRelay` remain available without release credentials.
  Every APK/AAB-producing entry point, including direct `assembleOtpSmsRelay`, is gated by Owner
  signing and an external `CLIPVAULT_PLAY_SMS_APPROVAL_REF`; `buildApprovedOtpSmsRelay` is the
  documented release entry point. A property value is repository bookkeeping, not proof that Google
  Play approved the use.

The approved lane is built with:

```powershell
cd android
.\gradlew.bat :app:buildApprovedOtpSmsRelay `
  -PCLIPVAULT_PLAY_SMS_APPROVAL_REF=<owner-controlled-reference> `
  -PCV_KEYSTORE=<owner-controlled-keystore> ...
```

## User flow and lifecycle

The Runtime main screen opens the OTP settings screen. The user must complete:

1. ordinary Desktop sync pairing;
2. one online `/api/otp/pair` exchange over a Desktop-accepted authenticated path;
3. one-time import of the returned verifier into the Android Keystore-sealed pair record;
4. the Android `RECEIVE_SMS` runtime permission in the approved build;
5. an explicit capture grant, bounded to source, pair session, sender, target and at most eight hours.

Capture remains off in a new process even if the previous process stopped unexpectedly. `SCREEN_OFF`,
shutdown, explicit revoke, deadline expiry and severe process memory pressure erase the in-memory grant
and reset `captureOptIn=false`. Forgetting a pair additionally deletes its Keystore key, sealed verifier,
monotonic sequence and nonce history. Desktop device revocation is still required before re-pairing.

The default Runtime also includes a separate per-message SMS User Consent fallback. It uses the exact
official `com.google.android.gms:play-services-auth-api-phone:18.2.0` client frozen in
`android/app/PLAY_SERVICES_SMS_USER_CONSENT_LOCK.json` under the Android SDK License. Pairing remains
available in the default Runtime. The user must tap “等待并确认下一条短信验证码”, which creates one
process-memory session bound to the current pair epoch, sender, target and a 120-second monotonic deadline.
Only then does a non-exported foreground activity call `startSmsUserConsent(null)`. Its dynamic receiver
accepts only the Google Play services `SEND_PERMISSION` broadcast and launches the system-owned consent
dialog. Denial, timeout, rotation/recreation, lock, target drift or process loss cancels the session.

After system confirmation the full message is parsed once and only the normalized numeric OTP enters the
existing authenticated relay producer with `explicitUserAction=true`. The session is consumed before
network I/O and cannot be replayed. This path requests neither `READ_SMS` nor `RECEIVE_SMS`, never sets the
durable automatic-capture opt-in and is available only on devices with a compatible Google Play services
implementation. It does not weaken the restricted automatic-capture review gate.

Before either pair or relay opens a connection, Android validates the endpoint. Cleartext HTTP accepts
only literal loopback or Tailscale IPv4/IPv6 addresses; ordinary LAN addresses and cleartext hostnames are
rejected without sending the sync bearer. HTTPS uses normal platform certificate validation. The Desktop
applies the same loopback/Tailscale source gate and neither side follows redirects.

The SMS receiver uses `goAsync()` plus a single zero-queue executor. It parses a bounded, recent message,
  passes only a normalized 4-8 digit ASCII OTP candidate into the Runtime, performs one synchronous online
POST and then finishes. Saturation, lock state, missing authorization, invalid parsing, offline state or
HTTP failure drops and wipes the candidate. It never starts WorkManager/a service, writes Room/SQLite,
or inserts an ordinary outbox row.

## Pair credential and wire

The Desktop pair response is accepted once and immediately converted into a record encrypted by a
non-exportable Android Keystore AES-GCM key under `noBackupFilesDir`. Each send reserves and synchronously
persists both the next sequence and a nonce digest before encryption/network I/O. Failed sends consume
that reservation; rollback is forbidden. The OTP payload then uses the frozen OTP-3A AES-256-GCM/HKDF/AAD
profile and `/api/otp/relay` wire envelope.

`org.json`, `SmsMessage` and other managed Android APIs necessarily create immutable `String` objects.
The implementation wipes every owned `ByteArray`/`CharArray`, never persists or logs message/verifier/OTP
content, and keeps the full SMS inside the capture process, but it does **not** claim absolute zero-trace
memory. OS paging, crash capture and managed-runtime copies remain residual platform risks.

The frozen OTP-3A plaintext profile is numeric. Alphabetic or mixed alphanumeric codes are rejected in
this version rather than silently widening a cross-platform authentication contract.

## Fallbacks and remaining evidence

Notification Listener is not registered. Android 15 OTP redaction and OEM/RCS differences make it a
fail-closed fallback, not a production source. SMS User Consent is the implemented default, explicit,
non-automatic fallback; the approved `RECEIVE_SMS` lane remains a separate optional distribution path.

Before claiming automatic OTP relay ready for public daily use, the Owner still needs Android 13/15/17
physical-device evidence, real SMS/RCS multipart cases, denial/lock/process-kill tests, Desktop online and
offline tests, a Play review video/declaration, privacy disclosure and signed install/upgrade/uninstall QA.
